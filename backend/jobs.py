from . import qwen_runtime as qr
import asyncio
import json
import logging
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import httpx
import websockets

from .store import Store
from .job_activity import seconds_between
from .workflows import prepare_character, check_graph, character_description
from .face import refine
from . import head, anchor

logger = logging.getLogger('gen_studio')
TERMINAL = {'completed', 'failed', 'cancelled', 'submission_unknown'}


def image_model_label(parameters):
    if parameters.get('engine')=='qwen_image21':return 'Qwen Image 2.1'
    if parameters.get('edit_profile')=='mystic_base8' or parameters.get('model')=='MOSS-Image-BASE':
        return 'MOSS BASE + Mystic' if float(parameters.get('mystic_strength',.7))>0 else 'MOSS BASE'
    return 'MOSS SFT'


class JobService:
    def __init__(self, client, settings):
        self.client = client
        self.settings = settings
        self.root = Path(settings['data_root'])
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.root / 'state.sqlite3', settings=settings)
        self.lock = asyncio.Lock()
        self.worker = None
        self.intel_worker = None
        self.intel_wake = asyncio.Event()
        self.enhancer_active = False
        self.enhancer_task = None
        self.enhancement = None
        self.wake = asyncio.Event()
        self.coordinator = None
        self.analysis_lease = False
        self.active_job_id = None
        self.cancel_wake = asyncio.Event()

    def reserve_analysis(self):
        if self.coordinator and not self.coordinator.claim(self,'analysis'):return False
        self.analysis_lease=True
        return True

    def release_analysis(self):
        if self.analysis_lease and self.coordinator:self.coordinator.release(self,'analysis')
        self.analysis_lease=False

    async def start(self):
        for job in self.store.unfinished_jobs():
            self.store.update(job['id'], _activity_recovered=True)
            if job['parameters'].get('operation') in ('video_upscale','video_upscale_assembly'):
                from .tessera import recover
                await recover(self,job)
                continue
            if job.get('cancel_requested'):
                self.store.update(job['id'], status='cancelling',
                    cancel_submission_unknown=job.get('cancel_submission_unknown', False) or (job['status']=='submitting' and not job.get('prompt_id')))
            elif job['parameters'].get('engine') in ('pika_h3_flex','h3_i2va') or job['parameters'].get('engine')=='pika_ref2va' and not job.get('video_sequence_version'):
                self.store.update(job['id'],status='running' if job.get('pika_submission_attempted') or job.get('engine_instance_id') else 'queued')
            elif job['parameters'].get('engine')=='viggle_remake' and not job.get('video_sequence_version'):
                self.store.update(job['id'],status='running' if job.get('viggle_submission_attempted') or job.get('engine_instance_id') else 'queued')
            elif job.get('video_sequence_version') and not job.get('prompt_id'):
                self.store.update(job['id'],status='queued')
            elif job['parameters'].get('operation')=='motion_hybrid' and job['status']!='submitting' and not job.get('prompt_id'):
                self.store.update(job['id'],status='queued')
            elif job['parameters'].get('operation') in ('long_character','long_assembly','ninja_shots_plan','ninja_shots_prepare','ninja_shots_render'):
                self.store.update(job['id'],status='queued')
            elif job['status'] == 'submitting':
                self.store.update(job['id'], status='submission_unknown', error='El servidor se reinició durante el envío. Revisa ComfyUI antes de volver a enviar.')
            elif job['parameters'].get('operation') in ('identity_anchor','geometry_copy','upscale_1080'):
                self.store.update(job['id'], status='queued')
            elif job['parameters'].get('operation') == 'guided_head' and job['status'] in {'preparing', 'refining', 'downloading'}:
                self.store.update(job['id'], status='running' if job.get('prompt_id') else 'queued')
            elif job['status'] in {'preparing', 'downloading'}:
                # Downloading has a known prompt ID and must only resume history reconciliation.
                self.store.update(job['id'], status='running' if job.get('prompt_id') else 'queued')
        from . import h3_intel_queue
        if ((self.intel_worker is None or self.intel_worker.done())
                and (self.settings.get("h3_parallel_planning") or h3_intel_queue.pending(self))):
            self.intel_worker = asyncio.create_task(h3_intel_queue.work(self))
        self.worker = asyncio.create_task(self.work_loop())

    async def close(self):
        if self.intel_worker:
            self.intel_worker.cancel()
            await asyncio.gather(self.intel_worker, return_exceptions=True)
        if self.enhancer_task and not self.enhancer_task.done():
            self.enhancer_task.cancel()
            try:
                await self.enhancer_task
            except asyncio.CancelledError:
                pass
        if self.worker:
            self.worker.cancel()
            try:
                await self.worker
            except asyncio.CancelledError:
                pass

    async def get(self, base, route):
        response = await self.client.get(base + route)
        response.raise_for_status()
        return response.json()

    async def post(self, base, route, **kwargs):
        response = await self.client.post(base + route, **kwargs)
        if not response.is_success:
            raise ValueError(f'{route}: HTTP {response.status_code}: {response.text[:2000]}')
        return response.json() if response.text.strip() else {}

    def event(self, job_id, message):
        job = self.store.job(job_id)
        self.store.update(job_id, vram_events=job['vram_events'] + [message])
        logger.info('%s %s', job_id, message)

    async def idle(self):
        from .tessera import busy
        if await busy(self):return False
        if self.settings.get('pika_h3_flex_enabled'):
            from .pika_h3_flex.runtime import running
            if running(self.settings):return False
        from .viggle_remake.runtime import running as viggle_running
        if viggle_running(self.settings):return False
        queue = await self.get(self.settings['comfy_url'], '/queue')
        return not queue.get('queue_running') and not queue.get('queue_pending')

    async def release_ollama(self, job_id):
        base = self.settings['ollama_url']
        aliases = {'simpligen-agent:latest', 'simpligen-qwen36:latest'}
        for attempt in range(3):
            loaded = (await self.get(base, '/api/ps')).get('models', [])
            names = {m.get('name') or m.get('model') for m in loaded}
            if not names:
                self.event(job_id, 'Ollama confirmó que no hay modelos cargados.')
                return
            unknown = [n for n in names if not isinstance(n, str) or not (n.startswith('qwen3') or n in aliases)]
            if unknown:
                raise ValueError('Hay modelos ajenos a la alternancia autorizada: ' + ', '.join(map(str, unknown)))
            for name in names:
                await self.post(base, '/api/generate', json={'model': name, 'keep_alive': 0, 'stream': False})
                self.event(job_id, f'Descarga solicitada para {name}.')
            for _ in range(10):
                await asyncio.sleep(1)
                if not (await self.get(base, '/api/ps')).get('models'):
                    self.event(job_id, 'Descarga de Ollama confirmada antes de enviar a ComfyUI.')
                    return
        raise ValueError('Ollama no liberó sus modelos después de tres intentos.')

    async def work_loop(self):
        while True:
            try:
                if self.coordinator and self.coordinator.maintenance:
                    await asyncio.sleep(.5)
                    continue
                if self.enhancer_active:
                    await asyncio.sleep(0.5)
                    continue
                # Sequence parents schedule attached children under one engine lease.
                # long_assembly also has long_parent_id, but is independently queued.
                jobs = [j for j in self.store.unfinished_jobs() if not j.get('qwen_waiting') and not j.get('sequence_waiting') and (not j['parameters'].get('sequence_parent_id') or j['parameters'].get('standalone_segment_attempt')) and not j['parameters'].get('ninja_sequence_owner') and not j['parameters'].get('hybrid_parent_id')
                    and not (j['parameters'].get('engine')=='h3_direct' and j['parameters'].get('long_parent_id')
                        and type(j['parameters'].get('long_segment_index')) is int)]
                job = next((j for j in jobs if j['status']=='cancelling'), None)
                if job is None:
                    job = next((j for j in jobs if j['status'] in {'running', 'refining'} and (j.get('prompt_id') or j['parameters'].get('engine') in ('pika_h3_flex','h3_i2va') and (j.get('pika_submission_attempted') or j.get('engine_instance_id')))), None)
                if job is None:
                    queued=[j for j in jobs if j['status']=='queued']
                    job=queued[0] if queued else None
                    sequence_resume=False
                    # An earlier unrelated queued job must not prevent reconciliation
                    # of an accepted child that still occupies the external engine.
                    for candidate in queued:
                        p=candidate['parameters'];accepted=None
                        if candidate.get('video_sequence_version'):
                            from .video_sequences import active_child
                            child=active_child(self,candidate)
                            accepted=child if child and (child.get('prompt_id') or child['parameters'].get('engine')=='viggle_remake' and (child.get('viggle_submission_attempted') or child.get('engine_instance_id') or (child.get('viggle_paint_runtime',{}).get('attempted') and not child.get('viggle_paint_runtime',{}).get('completed'))) or child['parameters'].get('engine')=='pika_ref2va' and (child.get('pika_submission_attempted') or child.get('engine_instance_id') or child.get('pika_swap_guide',{}).get('attempted'))) and child['status'] not in TERMINAL else None
                        elif p.get('operation') in ('ninja_shots_prepare','ninja_shots_render'):
                            from .ninja_shots import accepted_child
                            accepted=accepted_child(self,candidate)
                        elif p.get('operation')=='motion_hybrid':
                            from .motion_hybrid import accepted_child
                            accepted=accepted_child(self,candidate)
                        elif p.get('operation')=='long_character' and p.get('engine')=='h3_direct':
                            from .long_video import accepted_child
                            accepted=accepted_child(self,candidate)
                        if accepted:
                            job=candidate;sequence_resume=True;break
                    if job:
                        from .generation_limits import check as generation_allowed
                        if not generation_allowed(self,job):
                            job=next((candidate for candidate in queued[1:] if generation_allowed(self,candidate)),None)
                    if job and not sequence_resume and job['parameters'].get('operation') not in ('identity_anchor','geometry_copy','video_upscale','video_upscale_assembly') and not await self.idle():
                        self.store.update(job['id'], stage='Esperando a que termine la cola externa de ComfyUI')
                        job = None
                if job:
                    async with self.lock:
                        if self.enhancer_active:
                            continue
                        current = self.store.job(job['id'])
                        if current['status'] not in {'queued', 'running', 'refining', 'cancelling'}:
                            continue
                        if current['status'] == 'queued':
                            self.store.update(job['id'], status='preparing')
                    await self.run(job)
                    continue
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception('Error de supervisión de cola')
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=2)
            except TimeoutError:
                pass

    def record_engine_contact(self, job_id, state='event'):
        from .store import now
        job = self.store.job(job_id)
        if not job:return
        activity = job.get('activity') or {}
        changed = state in ('running', 'queued', 'history') and activity.get('engine_state') != state
        if changed or not activity.get('last_engine_contact_at') or seconds_between(activity['last_engine_contact_at'], now()) >= 5:
            self.store.update(job_id, _engine_contact=state)

    async def listen(self, socket, job_id):
        async for message in socket:
            if not isinstance(message, str):
                continue

            try:
                event = json.loads(message)
                data = event.get('data', {})
                job = self.store.job(job_id)
                if data.get('prompt_id') and job.get('prompt_id') != data['prompt_id']:
                    continue
                if job.get('cancel_requested'):
                    continue
                if event['type'] == 'progress' and job['status'] == 'running':
                    maximum = data.get('max', 0)
                    stage='Creando character sheet con Qwen Image 2.1 · 4 MP' if job.get('guided_phase')=='character-sheet-4mp' else '1 de 2 · Generando movimiento con Viggle' if job.get('guided_phase')=='hybrid-motion' else 'Muestreo del nodo actual'
                    if job.get('guided_phase') == 'ninja-track':stage = 'Siguiendo el rostro y preparando la máscara'
                    elif job.get('guided_phase') == 'face-track':stage = 'Siguiendo el rostro en los fotogramas'
                    if job['parameters'].get('operation')=='motion_layers':stage=job.get('layers_stage') or 'Personaje + Escenario'
                    if job['parameters'].get('operation')=='image_generation':stage='Generando tu imagen con '+image_model_label(job['parameters'])
                    node = str(data.get('node') or (job.get('activity') or {}).get('current_node') or '')
                    sampler = '24' if job['parameters'].get('direct_preset') in ('realism','hands') else '13'
                    sample = job['parameters'].get('engine') == 'h3_direct' and node == sampler
                    self.store.update(job_id, progress={'value': data.get('value', 0), 'max': maximum}, stage=stage,
                        _engine_contact='running', _activity_event={'kind':'progress',
                            'key':str(job.get('prompt_id'))+':'+node,'value':data.get('value',0),'max':maximum,
                            'sample_observed':sample})
                elif event['type'] == 'executing' and data.get('node') and job['status'] == 'running':
                    stages = {'8': 'Preparando referencia y movimiento', '19': 'Siguiendo al personaje con SAM3',
                              '13': 'Generando el personaje con MiniMax H3', '14': 'Decodificando el vídeo',
                              '16': 'Exportando el vídeo'}
                    if job['parameters'].get('engine') == 'h3_direct' and job['parameters'].get('operation') in (None,'h3_recorded_render'):
                        if job['parameters'].get('direct_preset') in ('realism','hands'):
                            stages={'1':'Cargando el modelo H3','11':'Cargando el codificador de referencias',
                                '12':'Cargando el decodificador de vídeo','13':'Cargando el decodificador de audio',
                                '14':'Leyendo el vídeo fuente','16':'Leyendo la referencia',
                                '17':'Preparando el tamaño del vídeo','18':'Preparando el tamaño de la referencia',
                                '19':'Preparando referencia y movimiento','24':'Generando el personaje con MiniMax H3',
                                '25':'Decodificando el vídeo','26':'Decodificando el audio',
                                '27':'Componiendo vídeo y audio','28':'Exportando el vídeo'}
                        else:
                            stages={'1':'Cargando el modelo H3','3':'Cargando el codificador de referencias',
                                '4':'Cargando el decodificador de vídeo','5':'Cargando el decodificador de audio',
                                '7':'Leyendo el vídeo fuente','8':'Preparando referencia y movimiento',
                                '13':'Generando el personaje con MiniMax H3','14':'Decodificando el vídeo',
                                '18':'Decodificando el audio','15':'Componiendo vídeo y audio','16':'Exportando el vídeo'}
                    if job['parameters'].get('operation')=='motion_layers':
                        stages={'14':'Leyendo el vídeo fuente','16':'Leyendo la referencia del personaje','19':'Preparando referencia y movimiento',
                            '24':'1 de 2 · Generando al personaje','25':'Decodificando la primera pasada','28':'Guardando al personaje',
                            'track':'Siguiendo la silueta del personaje','select':'Seleccionando a la persona marcada','save-mask':'Guardando su protección',
                            'condition':'Preparando el nuevo escenario','av-latent':'Protegiendo al personaje en movimiento',
                            'sample':'2 de 2 · Generando escenario e interacciones','decode':'Decodificando el escenario',
                            'composite':'Restaurando al personaje protegido','save':'Guardando la escena final'}
                    if job['parameters'].get('engine') == 'h3_ninja':
                        stages={'video':'Leyendo el vídeo fuente','sam3':'Cargando el seguimiento de personas',
                            'track':'Siguiendo el rostro y preparando la máscara','select':'Seleccionando la persona marcada',
                            'save-mask':'Guardando la máscara','preview-save':'Guardando la vista previa de la máscara',
                            'model':'Cargando el modelo de refinamiento','clip':'Cargando el codificador de referencias',
                            'load-mask':'Cargando la máscara revisada','crop':'Preparando el recorte en movimiento',
                            'condition':'Preparando la identidad del rostro','av-latent':'Preparando vídeo y audio',
                            'sample':'Generando el refinamiento facial','decode':'Decodificando el rostro refinado',
                            'uncrop':'Integrando el rostro en la toma','save':'Guardando el refinamiento facial'}
                    if job['parameters'].get('operation')=='image_generation':
                        stages={'model':'Cargando '+image_model_label(job['parameters']),'clip':'Cargando el codificador de texto','encode':'Interpretando tu descripción y referencias','cache':'Preparando las referencias de Qwen','positive':'Leyendo tu descripción','negative':'Preparando los detalles a evitar','distill':'Aplicando el LoRA de 8 pasos','mystic':'Aplicando Mystic','realism':'Aplicando el acabado fotográfico','sample':'Generando tu imagen con '+image_model_label(job['parameters']),'decode':'Revelando la imagen','save':'Guardando tu imagen'}
                    if job['parameters'].get('engine')=='h3_mdmz':
                        stages={'detect':'MDMZ · Detectando el personaje con SAM3','guide':'MDMZ · Preparando guía invertida','condition':'MDMZ · Preparando referencias','sample':'MDMZ · Generando el personaje','unload':'MDMZ · Liberando memoria antes de decodificar','decode':'MDMZ · Decodificando el vídeo','save':'MDMZ · Guardando el resultado'}
                    if job['parameters'].get('engine')=='h3_alisson':
                        stages={'model':'Cargando el modelo de Alisson','clip':'Cargando el codificador de la referencia',
                                'foreground':'Separando el personaje del fondo de referencia','condition':'Preparando la identidad del personaje',
                                'guide':'Preparando todos los fotogramas y el audio como guía',
                                'sample':'Generando el intervalo completo con Alisson','decode':'Decodificando el vídeo de Alisson',
                                'save':'Guardando el vídeo de Alisson'}
                    if job.get('guided_phase')=='character-sheet-4mp':
                        stages={key:'Preparando character sheet con Qwen Image 2.1 · 4 MP' for key in ('sg_unet','sg_clip','sg_vae','sg_encode','sg_cache','sg_latent','sgopt_ref_image_1__load','sgopt_ref_image_2__load')}
                        stages.update(sg_sampler='Creando character sheet con Qwen Image 2.1 · 4 MP',
                                      sg_decode='Decodificando el character sheet de 4 MP',sg_save='Guardando el character sheet de 4 MP')
                    self.store.update(job_id, stage=stages.get(str(data['node']), 'Procesando la toma'), progress=None,
                        _engine_contact='running', _activity_event={'kind':'node',
                            'key':str(job.get('prompt_id'))+':'+str(data['node']), 'node':str(data['node'])})
                elif event['type'] == 'execution_cached' and job['status'] == 'running':
                    sampler = '24' if job['parameters'].get('direct_preset') in ('realism','hands') else '13'
                    cached = job['parameters'].get('engine') == 'h3_direct' and sampler in {str(n) for n in data.get('nodes',[])}
                    self.store.update(job_id, _engine_contact='event', _activity_event={'cached_execution':cached})
            except (ValueError, KeyError):
                continue

    async def connect_progress(self, client_id, job_id):
        if self.settings.get('disable_websocket', False) or not client_id:
            return None, None
        parsed = urlparse(self.settings['comfy_url'])
        ws_url = urlunparse(parsed._replace(scheme='ws' if parsed.scheme == 'http' else 'wss', path='/ws', query=f'clientId={client_id}'))
        try:
            socket = await websockets.connect(ws_url, open_timeout=5, max_size=8 * 1024 ** 2)
            return socket, asyncio.create_task(self.listen(socket, job_id))
        except Exception:
            logger.exception('WebSocket no disponible; se conserva seguimiento por historial')
            return None, None

    @qr.bound
    async def run(self, job):
        async def execute():
            if job['parameters'].get('operation') in ('video_upscale','video_upscale_assembly'):
                from .tessera import run
                return await run(self,job)
            from .tessera_runtime import release_idle
            await release_idle(self)
            if job['parameters'].get('engine') in ('pika_h3_flex','h3_i2va'):
                from .pika_h3_flex.adapter import process
                return await process(self,job)
            if job.get('video_sequence_version') or job['parameters'].get('ninja_segments_version') or job.get('qwen_director_version') or job['parameters'].get('engine') in ('h3_alisson','h3_mdmz','h3_secourses') or job['parameters'].get('operation') in ('upscale_1080','motion_hybrid','motion_anchor','motion_bunny','motion_layers','image_generation','omni_background'):
                from .job_cancel import run
                return await run(self, job)
            return await self._run(job)
        if self.coordinator:
            async with self.coordinator.generation(self,job) as ready:
                if ready:return await execute()
            return
        return await execute()

    async def _run(self, job):
        job_id = job['id']
        current = self.store.job(job_id)
        if (current and current['status'] == 'queued' and
                (job['parameters'].get('sequence_parent_id') or job['parameters'].get('long_parent_id') or job['parameters'].get('ninja_sequence_owner') or job['parameters'].get('hybrid_parent_id'))):
            self.store.update(job_id, status='preparing', stage='Preparando el tramo')
        base = self.settings['comfy_url']
        socket = receiver = None
        prompt_id = job.get('prompt_id')
        terminal_seen = False
        try:
            if current.get('video_sequence_version'):
                from .video_sequences import process as sequence_process
                await sequence_process(self,current)
                return
            if job['parameters'].get('engine')=='pika_ref2va':
                from .pika_ref2va.adapter import process as ref2va_process
                await ref2va_process(self,current)
                return
            if job['parameters'].get('engine')=='viggle_remake':
                from .viggle_remake.integration import process as viggle_process
                await viggle_process(self,current)
                return
            if job['parameters'].get('operation')=='h3_recorded_render':
                from .h3_recorded_render import process as replay
                await replay(self,current)
                return
            if current.get('qwen_director_version') and not current.get('prompt_id'):
                from .qwen_director import before_job
                if not await before_job(self,current):return
            if job['parameters'].get('operation')=='image_generation':
                from .image_generation import process as image_process
                await image_process(self,job)
                return
            if job['parameters'].get('operation')=='omni_background':
                from .omni_background import process as omni_background_process
                await omni_background_process(self,job)
                return
            if job['parameters'].get('operation')=='upscale_1080':
                from .upscale import process as upscale_process
                await upscale_process(self,job)
                return
            if job['parameters'].get('operation') in ('ninja_shots_plan','ninja_shots_prepare','ninja_shots_render'):
                from .ninja_shots import process as ninja_shots_process
                await ninja_shots_process(self,job)
                return
            if job['parameters'].get('engine')=='h3_secourses':
                from .secourses_swap import process as secourses_process
                await secourses_process(self,job)
                return
            if job['parameters'].get('engine')=='h3_mdmz':
                from .mdmz_swap import process as mdmz_process
                await mdmz_process(self,job)
                return
            if job['parameters'].get('engine')=='h3_alisson':
                from .alisson_swap import process as alisson_process
                await alisson_process(self,job)
                return
            if job['parameters'].get('operation')=='motion_layers':
                from .motion_layers import process as layers_process
                await layers_process(self,job)
                return
            if job['parameters'].get('operation')=='motion_bunny':
                from .motion_bunny import process as bunny_process
                await bunny_process(self,job)
                return
            if job['parameters'].get('engine')=='viggle_motion':
                from .motion_transfer import process as motion_process
                await motion_process(self,job)
                return
            if job['parameters'].get('engine')=='h3_ninja':
                from .ninja_swap import process as ninja_process
                await ninja_process(self,job)
                return
            if job['parameters'].get('operation')=='h3_face_refine':
                from .h3_face_refine import process as h3_face_process
                await h3_face_process(self,job)
                return
            if job['parameters'].get('operation')=='geometry_copy':
                from .geometry_copy import process as geometry_process
                await geometry_process(self,job)
                return
            if job['parameters'].get('operation') in ('long_character','long_assembly'):
                from .long_video import process as long_process
                await long_process(self,job)
                return
            if job['parameters'].get('engine')=='h3_direct' and not job['parameters'].get('operation'):
                from .direct_character import process as direct_process
                await direct_process(self,job)
                return
            if job['parameters'].get('operation') == 'guided_head':
                from .guided_head import process as guided_head_process
                await guided_head_process(self, job)
                return
            if job['parameters'].get('operation') == 'identity_anchor':
                self.store.update(job_id, status='refining', stage='Comprobando seguimiento de cabeza')
                report = await anchor.process(self, job)
                guided = job['parameters'].get('anchor_method')=='guided_composition'
                scene_resolution = f"{report['media']['streams'][0]['width']}x{report['media']['streams'][0]['height']}" if guided else job.get('scene_resolution')
                self.store.update(job_id, status='completed', stage='Composición estable lista para revisión' if guided else 'Copia con identidad lista para revisión', result_available=True, face_result_available=True, progress=None,
                    scene_resolution=scene_resolution,
                    face_review={'applied_count': len(report['applied_frames']), 'skipped_count': 0, 'identity_approved': False, 'review_required': True,
                                 'method':'guided_composition' if guided else 'cpu_identity', 'source_detail_preserved':guided})
                self.event(job_id, 'Corrección local CPU terminada; no se enviaron trabajos a ComfyUI ni se cargaron modelos en la GPU.')
                return
            if job['status'] == 'refining':
                await self.finish_face(job)
                return
            if prompt_id:
                socket, receiver = await self.connect_progress(job.get('client_id'), job_id)
                self.store.update(job_id, stage='Retomando el seguimiento de ComfyUI', progress=None)
            if not prompt_id:
                self.store.update(job_id, status='preparing', stage='Preparando entradas y comprobando nodos')
                if job['parameters'].get('operation') == 'head_detail':
                    job = await head.prepare(self, job)
                payload = job['parameters']
                video = self.store.asset(payload['video_id'])
                reference = self.store.asset(payload['reference_id'])
                if payload.get('preserve_face'):
                    self.store.update(job_id, stage='Comprobando el primer plano del rostro')
                    await refine(self, job, validate_only=True)
                # Fresh filename; overwrite=false preserves every existing ComfyUI input.
                with (self.root / 'inputs' / reference['filename']).open('rb') as stream:
                    uploaded = await self.post(base, '/upload/image', files={'image': (reference['filename'], stream)},
                                               data={'overwrite': 'false', 'subfolder': f'gen_studio/{job_id}'})
                image_name = '/'.join(part for part in [uploaded.get('subfolder', ''), uploaded['name']] if part)
                detail_name = None
                if payload.get('guide_face_reference'):
                    detail = self.store.asset(payload['face_reference_id'])
                    with (self.root / 'inputs' / detail['filename']).open('rb') as stream:
                        detail_upload = await self.post(base, '/upload/image', files={'image': (detail['filename'], stream)},
                                                        data={'overwrite': 'false', 'subfolder': f'gen_studio/{job_id}'})
                    detail_name = '/'.join(part for part in [detail_upload.get('subfolder', ''), detail_upload['name']] if part)
                graph = prepare_character(payload, self.root / 'inputs' / video['filename'], image_name, job_id,
                                          video['audio'], detail_reference_name=detail_name)
                check = await check_graph(self.client, base, graph, character_description()['integrity_ok'])
                if not check['ok']:
                    raise ValueError(' · '.join(check['errors']))
                directory = self.root / 'jobs' / job_id
                directory.mkdir(parents=True, exist_ok=True)
                (directory / 'workflow.api.json').write_text(json.dumps(graph, indent=2), encoding='utf-8')
                if not await self.idle():
                    self.store.update(job_id, status='queued', stage='La cola externa cambió; esperando')
                    return
                self.store.update(job_id, stage='Liberando Ollama según el protocolo existente')
                await self.release_ollama(job_id)
                if not await self.idle():
                    self.store.update(job_id, status='queued', stage='Esperando a otra aplicación de ComfyUI')
                    return
                client_id = str(uuid4())
                socket, receiver = await self.connect_progress(client_id, job_id)
                self.store.update(job_id, status='submitting', stage='Enviando a ComfyUI', client_id=client_id)
                try:
                    result = await self.post(base, '/prompt', json={'prompt': graph, 'client_id': client_id}, timeout=60)
                except httpx.HTTPError as error:
                    self.store.update(job_id, status='submission_unknown', error=f'Envío sin confirmar: {error}. Revisa ComfyUI antes de repetir.')
                    return
                prompt_id = result.get('prompt_id')
                if not prompt_id:
                    raise ValueError('ComfyUI no devolvió prompt_id: ' + json.dumps(result)[:1500])
                self.store.update(job_id, prompt_id=prompt_id, status='running', stage='Generando en ComfyUI')
                self.event(job_id, 'ComfyUI aceptó el workflow después de confirmar la descarga de Ollama.')
            while True:
                if receiver and receiver.done():
                    await asyncio.gather(receiver, return_exceptions=True)
                    receiver = None
                    self.store.update(job_id, stage='Generando; seguimiento por historial', progress=None)
                try:
                    history = await self.get(base, f'/history/{prompt_id}')
                except httpx.HTTPError as error:
                    self.store.update(job_id, stage=f'Conexión interrumpida; esperando el historial: {error}', progress=None, _engine_contact_error=True)
                    await asyncio.sleep(5)
                    continue
                entry = history.get(prompt_id)
                self.record_engine_contact(job_id, 'history' if entry else 'event')
                if entry:
                    terminal_seen = True
                    folder = self.root / 'jobs' / job_id
                    folder.mkdir(parents=True, exist_ok=True)
                    (folder / 'history.json').write_text(json.dumps(entry, indent=2), encoding='utf-8')
                    if entry.get('status', {}).get('status_str') != 'success':
                        raise ValueError('ComfyUI: ' + json.dumps(entry.get('status', {}), ensure_ascii=False)[:4000])
                    self.store.update(job_id, status='downloading', stage='Guardando resultado local', progress=None)
                    result_info = entry.get('outputs', {}).get('16', {})
                    descriptors = [item for value in result_info.values() if isinstance(value, list) for item in value if isinstance(item, dict) and item.get('filename', '').lower().endswith('.mp4')]
                    if not descriptors:
                        raise ValueError('No se encontró el MP4 del nodo SaveVideo.')
                    info = descriptors[0]
                    # Only the descriptor returned for this recorded job is forwarded.
                    params = {k: info[k] for k in ('filename', 'subfolder', 'type') if k in info}
                    directory = self.root / 'results'
                    directory.mkdir(parents=True, exist_ok=True)
                    partial = directory / (job_id + '.part')
                    async with self.client.stream('GET', base + '/view', params=params, timeout=120) as response:
                        response.raise_for_status()
                        with partial.open('wb') as output:
                            async for chunk in response.aiter_bytes():
                                output.write(chunk)
                    target = directory / (job_id + '.mp4')
                    partial.replace(target)
                    self.store.update(job_id, result_available=True)
                    if job['parameters'].get('preserve_face'):
                        masks = entry.get('outputs', {}).get('34', {})
                        descriptors = [item for value in masks.values() if isinstance(value, list) for item in value
                                       if isinstance(item, dict) and item.get('filename', '').lower().endswith('.mp4')]
                        if not descriptors:
                            raise ValueError('Falta el vídeo de máscara del sujeto; se conserva la generación base.')
                        info = descriptors[0]
                        response = await self.client.get(base + '/view', params={k: info[k] for k in ('filename','subfolder','type') if k in info}, timeout=120)
                        response.raise_for_status()
                        (folder / 'subject-mask.mp4').write_bytes(response.content)
                        await self.free_after_terminal(job_id)
                        terminal_seen = False
                        self.store.update(job_id, status='refining', stage='Aplicando corrección facial', progress=None)
                        await self.finish_face(self.store.job(job_id))
                    else:
                        self.store.update(job_id, status='completed', stage='Vídeo listo', result_available=True, progress=None)
                    break
                await asyncio.sleep(2)
        except asyncio.CancelledError:
            # Keep the accepted prompt ID for reconciliation at next startup.
            raise
        except Exception as error:
            logger.exception('Trabajo %s falló', job_id)
            current=self.store.job(job_id)
            if current['parameters'].get('engine')=='viggle_remake' and current.get('status')=='submission_unknown':return
            if current['parameters'].get('engine')=='pika_ref2va' and current.get('status')=='submission_unknown':
                from .pika_ref2va.swap_guide import pending
                if pending(current):return  # Keep an unconfirmed primary submission recoverable/cancellable.
            self.store.update(job_id, status='failed', stage='No se pudo completar el trabajo', error=str(error)[:4000], progress=None)
        finally:
            if receiver:
                receiver.cancel()
                await asyncio.gather(receiver, return_exceptions=True)
            if socket:
                await socket.close()
            if terminal_seen:
                await self.free_after_terminal(job_id)

    async def free_after_terminal(self, job_id):
        try:
            if await self.idle():
                await self.post(self.settings['comfy_url'], '/free', json={'unload_models': True, 'free_memory': True})
                self.event(job_id, 'ComfyUI aceptó la solicitud de liberar memoria después del historial terminal y con cola vacía.')
            else:
                self.event(job_id, 'Liberación diferida: hay trabajos externos en ComfyUI.')
        except Exception as error:
            self.event(job_id, f'No se confirmó la liberación final de ComfyUI: {error}')

    async def finish_face(self, job):
        self.store.update(job['id'], status='refining', stage='Aplicando corrección facial', progress=None)
        is_head = job['parameters'].get('operation') == 'head_detail'
        if is_head and job.get('face_result_available'):
            report = json.loads((self.root / 'jobs' / job['id'] / 'face-report.json').read_text(encoding='utf-8'))
        else:
            report = await refine(self, job)
        summary = {'applied_count': len(report['applied_frames']), 'skipped_count': len(report['skipped_frames']),
                   'scene_cuts': report['scene_cuts'], 'identity_approved': False, 'review_required': True}
        if is_head:
            self.store.update(job['id'], face_result_available=True, face_review=summary)
            composed = await head.compose(self, self.store.job(job['id']))
            self.store.update(job['id'], status='completed', stage='Cabeza afinada en una copia; revisa el resultado',
                head_result_available=True, result_available=True, progress=None,
                head_review={'applied_count':len(composed['applied_frames']), 'skipped_count':len(composed['skipped_frames']),
                    'scene_cuts':composed['scene_cuts'], 'identity_approved':False, 'accessory_approved':False, 'review_required':True})
            self.event(job['id'], f"Afinado de cabeza: {len(composed['applied_frames'])} fotogramas compuestos; la toma original se conserva.")
            return
        self.store.update(job['id'], status='completed', stage='Vídeo con corrección facial; revisa la identidad',
                          face_result_available=True, face_review=summary, result_available=True, progress=None)
        self.event(job['id'], f"Corrección facial CPU: {summary['applied_count']} fotogramas corregidos, {summary['skipped_count']} conservados por controles del sujeto.")
