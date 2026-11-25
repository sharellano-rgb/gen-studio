"""Two-phase ComfyUI head generation, followed by explicit human review."""
import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field

from .anchor_routes import resolve as anchor_source
from .face import ROOT, available
from .guided_composition import local_path
from .head import digest
from .media import probe
from .workflows import check_graph
from . import nsfw_adapters

TEMPLATE = ROOT / 'workflows/guided-head.api.json'
TEMPLATE_SHA = '71a4b0ad7ca2d5102237d4f56e6fecdafdf2bbb4707747385bfd7a70b22923e1'


class HeadRequest(BaseModel):
    request_key: UUID
    face_reference_id: UUID
    sheet_reference_id: UUID
    source: str = Field(default='original', pattern=r'^(original|parent-final|[a-z0-9-]{1,80})$')
    region: tuple[int, int, int]
    seed: int = Field(default=42, ge=0, le=9007199254740991)
    steps: int = Field(default=20, ge=8, le=30)
    lora_strength: float = Field(default=1, ge=0, le=1.5, allow_inf_nan=False)
    control_strength: float = Field(default=1.1, ge=.5, le=1.5, allow_inf_nan=False)
    direction: str = Field(default='Preserve the source headwear, clothing, lighting and background. Bare cheek, jaw and ear with continuous natural skin texture.', max_length=1000)
    nsfw_h3_motion_booster: bool = Field(default=False, strict=True)
    nsfw_penis_v2: bool = Field(default=False, strict=True)
    nsfw_hm_pussy: bool = Field(default=False, strict=True)
    nsfw_minimax_unlocked: bool = Field(default=False, strict=True)
    nsfw_strength: float = Field(default=1.0, ge=0, le=2, allow_inf_nan=False)


class ReviewRequest(BaseModel):
    approved: bool


def source_for(service, parent, source):
    if source == 'parent-final':
        path = service.root/'results'/(parent['id']+('.face.mp4' if parent.get('face_result_available') else '.mp4'))
        name = 'Resultado de esta toma · cuerpo y movimiento'
    else:
        path, name = anchor_source(service, parent, source)
        if source != 'original':
            report = json.loads((path.parent/'video-report.json').read_text())
            if report.get('scene_composited') is False or report.get('quality') == 'diagnostic_comparison' or report.get('comparison_only'):
                raise ValueError('Selecciona una escena completa, no un detalle aislado.')
    path = local_path(service.root, path)
    if not path.is_file():
        raise ValueError('La escena no está disponible.')
    return path, name


def graph_for(plan, cropped, face_name, sheet_name, payload, job_id):
    if digest(TEMPLATE) != TEMPLATE_SHA:
        raise ValueError('La plantilla de detalle H3 cambió. Se conserva y no se ejecuta.')
    graph = json.loads(TEMPLATE.read_text())
    graph.pop('pose-preview', None)
    graph['6']['inputs']['image'] = face_name
    graph['31']['inputs']['image'] = sheet_name
    for node, key in [('7', 'reference'), ('pose', 'pose')]:
        graph[node]['inputs'].update(video=cropped[key], frame_load_cap=plan['frames'], start_time=0)
    graph['8']['inputs'].update(length=plan['frames'], prompt=
        'Replace the face of the person in the close head crop of <Video 1> with the exact person from <Picture 1>. '
        'Preserve their eyes, nose, facial proportions, beard if present and natural skin. '
        '<Picture 2> is the character appearance reference. Follow the exact head orientation, facial expression, '
        'headwear shape and head framing of <Video 1> throughout the shot. Use the measured facial pose guide '
        'to retain rotation. Do not show reference backgrounds or widen the shot. '+payload['head_direction'])
    graph['2']['inputs']['strength_model'] = payload['lora_strength']
    graph['control']['inputs']['strength'] = payload['control_strength']
    graph['10']['inputs']['steps'] = payload['steps']
    graph['12']['inputs']['noise_seed'] = payload['seed']
    graph['16']['inputs']['filename_prefix'] = f'gen_studio/{job_id}/guided-head'
    graph = nsfw_adapters.apply_optional_adapters(graph, payload)
    return graph


def pose_graph(plan):
    return {
        'wide': {'class_type': 'VHS_LoadVideoFFmpegPath', 'inputs': {
            'video': plan['wide_path'], 'force_rate': 24, 'custom_width': 768, 'custom_height': 768,
            'frame_load_cap': plan['frames'], 'start_time': 0, 'format': 'None'}},
        'pose': {'class_type': 'DWPreprocessor', 'inputs': {'image': ['wide', 0],
            'detect_hand': 'disable', 'detect_body': 'enable', 'detect_face': 'enable', 'resolution': 768,
            'bbox_detector': 'yolox_l.onnx', 'pose_estimator': 'dw-ll_ucoco_384_bs5.torchscript.pt',
            'scale_stick_for_xinsr_cn': 'disable'}},
        'pose-preview': {'class_type': 'PreviewAnimation', 'inputs': {'images': ['pose', 0], 'fps': 24}},
    }


def register(app, settings):
    @app.get('/api/jobs/{job_id}/guided-head-options')
    async def options(job_id: UUID):
        service = app.state.jobs
        parent = service.store.job(str(job_id))
        if not parent:
            raise HTTPException(404, 'Trabajo no encontrado.')
        sources = []
        source_paths = {}
        if parent.get('result_available') and not parent['parameters'].get('operation'):
            choices = ['original', 'parent-final']+[p.parent.name for p in (service.root/'identity-tests').glob('*/video-report.json')]
            for source in choices:
                try:
                    path, name = source_for(service, parent, source)
                    metadata = await asyncio.to_thread(probe, path)
                    source_paths[source] = path
                    sources.append({'id': source, 'name': name, **metadata,
                        'url': f'/api/jobs/{job_id}/result?variant={"original" if source == "original" else "final"}'
                        if source in ('original', 'parent-final') else f'/api/identity-tests/{source}/video'})
                except (ValueError, KeyError, OSError):
                    continue
        resume = None
        latest = next((j for j in service.store.jobs() if j['parameters'].get('operation') == 'guided_head'
            and j['parameters'].get('head_parent_id') == parent['id']), None)
        if latest:
            payload = latest['parameters']
            selected = next((s for s in sources if str(source_paths[s['id']]) == payload.get('head_source')), None)
            if selected and await asyncio.to_thread(digest, source_paths[selected['id']]) == payload.get('head_source_sha256'):
                resume = {'source': selected['id'], 'width': selected['width'], 'height': selected['height'],
                    'region': payload['head_region'], 'face': payload['face_reference_id'], 'sheet': payload['sheet_reference_id'],
                    'params': {k: payload[k] for k in ['seed','steps','lora_strength','control_strength']},
                    'reviewTime': 0}
                resume['params']['direction'] = payload['head_direction']
        return {'enabled': bool(settings.get('guided_head_enabled') and settings['generation_enabled'] and available(settings)),
            'resume': resume,
            'sources': sources, 'defaults': {'steps': 20, 'seed': 42, 'lora_strength': 1, 'control_strength': 1.1}}

    @app.post('/api/jobs/{job_id}/generate-guided-head', status_code=202)
    async def submit(job_id: UUID, body: HeadRequest):
        if not settings.get('guided_head_enabled') or not settings['generation_enabled'] or not available(settings):
            raise HTTPException(503, 'La generación guiada de cabeza aún no está activada.')
        service = app.state.jobs
        async with service.lock:
            parent = service.store.job(str(job_id))
            if not parent:
                raise HTTPException(404, 'Trabajo no encontrado.')
            if parent['parameters'].get('operation') or parent['status'] not in ('completed', 'failed') or not parent.get('result_available'):
                raise HTTPException(409, 'Selecciona una generación de escena terminada.')
            references = [service.store.asset(str(key)) for key in (body.face_reference_id, body.sheet_reference_id)]
            if any(not asset or asset['kind'] != 'image' for asset in references):
                raise HTTPException(400, 'Selecciona una foto real y una referencia del personaje.')
            try:
                path, name = source_for(service, parent, body.source)
                metadata = await asyncio.to_thread(probe, path)
                x, y, size = body.region
                if min(x, y) < 0 or size < 32 or x+size > metadata['width'] or y+size > metadata['height'] or metadata['duration'] > 5.18:
                    raise ValueError('Selecciona una zona dentro de una toma continua de hasta 5,17 s.')
                ref_hashes = [await asyncio.to_thread(digest, local_path(service.root, service.root/'inputs'/a['filename'])) for a in references]
                payload = {**parent['parameters'], 'operation': 'guided_head', 'head_parent_id': parent['id'],
                    'head_source': str(path), 'head_source_name': name, 'head_source_sha256': await asyncio.to_thread(digest, path),
                    'head_source_choice': body.source,
                    'head_region': list(body.region), 'face_reference_id': str(body.face_reference_id),
                    'sheet_reference_id': str(body.sheet_reference_id), 'head_photo_sha256': ref_hashes[0],
                    'reference_id': str(body.sheet_reference_id),
                    'head_sheet_sha256': ref_hashes[1], 'head_direction': body.direction,
                    'seed': body.seed, 'steps': body.steps, 'lora_strength': body.lora_strength,
                    'control_strength': body.control_strength, 'preserve_face': False, 'resolution': '768x768'}
                result = service.store.create_job(str(uuid4()), str(body.request_key), payload)
            except (ValueError, OSError, KeyError) as error:
                raise HTTPException(409, str(error)) from error
            service.wake.set()
            return result

    @app.post('/api/jobs/{job_id}/review-guided-head')
    async def review(job_id: UUID, body: ReviewRequest):
        if not settings.get('guided_head_enabled'):
            raise HTTPException(503, 'La revisión guiada aún no está activada.')
        service = app.state.jobs
        async with service.lock:
            job = service.store.job(str(job_id))
            if not job:
                raise HTTPException(404, 'Trabajo no encontrado.')
            if job['parameters'].get('operation') != 'guided_head' or job['status'] != 'completed':
                raise HTTPException(409, 'Primero espera al detalle generado.')
            folder = local_path(service.root, service.root/'identity-tests'/('h3-'+job['id']))
            try:
                report = json.loads((folder/'video-report.json').read_text())
                recipe = json.loads((folder/'guided-composition.json').read_text())
                if report.get('head_job') != job['id'] or report.get('source_job') != job['parameters']['head_parent_id'] or recipe.get('source_job') != job['parameters']['head_parent_id'] or recipe.get('body_trial') != 'h3-body-'+job['id']:
                    raise ValueError('El detalle registrado no corresponde a este trabajo y su escena.')
                for key, path in [('head', folder/'corrected.mp4'), ('plan', folder/'preparation.json'),
                                  ('body', service.root/'identity-tests'/recipe['body_trial']/'corrected.mp4')]:
                    if digest(local_path(service.root, path)) != recipe[key+'_sha256']:
                        raise ValueError('Una entrada del detalle cambió; no se registra aprobación.')
                report.update(identity_approved=body.approved, accessory_approved=body.approved,
                    human_review='approved' if body.approved else 'rejected',
                    description='Detalle revisado por el usuario.' if body.approved else 'Detalle rechazado; no disponible para componer.')
                (folder/'video-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
                return service.store.update(job['id'], head_review={'identity_approved': body.approved,
                    'accessory_approved': body.approved, 'review_required': not body.approved})
            except (ValueError, KeyError, OSError) as error:
                raise HTTPException(409, str(error)) from error


async def cpu(service, job, mode, inputs):
    folder = service.root/'jobs'/job['id']
    checkpoint = folder/(mode+'-report.json')
    if checkpoint.is_file():
        report = json.loads(checkpoint.read_text())
        keys = {'track': ['wide'], 'crop': ['reference', 'pose'], 'inspect': ['reference', 'comparison']}[mode]
        for key in keys:
            path = report['wide_path'] if key == 'wide' else report[key]
            if digest(local_path(service.root, path)) != report[key+'_sha256']:
                raise ValueError('Cambió un recorte guardado; crea otra copia.')
        return report
    run = folder/(mode+'-'+str(uuid4()))
    run.mkdir(parents=True)
    manifest = {'mode': mode, 'parent_pid': os.getpid(), 'run_root': str(run),
        'model_root': service.settings['face_models'], **inputs}
    path = run/'manifest.json'
    path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    process = None
    with (run/'stderr.log').open('wb') as errors:
        try:
            process = await asyncio.create_subprocess_exec(service.settings['face_python'], '-u',
                str(ROOT/'scripts/prepare_guided_head.py'), str(path), stdout=asyncio.subprocess.PIPE, stderr=errors,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                env={**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'PYTHONIOENCODING': 'utf-8'})
            while line := await process.stdout.readline():
                try:
                    event = json.loads(line.decode('utf-8'))
                    if event.get('type') == 'progress':
                        service.store.update(job['id'], progress={'value': event['frame'], 'max': event['max']}, stage=event['stage'])
                except (ValueError, UnicodeDecodeError):
                    pass
            if await process.wait():
                error = run/'error.json'
                raise ValueError(json.loads(error.read_text())['error'] if error.is_file() else 'Falló la preparación de cabeza; consulta el registro.')
        finally:
            if process and process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except TimeoutError:
                    process.kill()
                    await process.wait()
    report = json.loads((run/'report.json').read_text())
    checkpoint.write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


async def phase(service, job, name, graph, node, extension, *, recorded=False):
    """Persist acceptance before polling; never replay an ambiguous submission."""
    from .job_cancel import checkpoint as cancel_checkpoint
    cancel_checkpoint(service, job['id'])
    folder = service.root/'jobs'/job['id']
    checkpoint = folder/(name+'-complete.json')
    if checkpoint.is_file():
        saved = json.loads(checkpoint.read_text())
        if digest(local_path(service.root, saved['path'])) != saved['sha256']:
            raise ValueError('Cambió una salida de ComfyUI registrada.')
        return saved
    current = service.store.job(job['id'])
    prompt_id = current.get('prompt_id') if current.get('guided_phase') == name else None
    socket = receiver = None
    terminal = False
    try:
        if not prompt_id:
            if not recorded:
                if current.get('qwen_director_version'):
                    from .qwen_director import before_phase
                    graph = await before_phase(service,current,name,graph)
                    if graph is None:return None
                from .video_sequences import before_phase as sequence_phase
                graph = await sequence_phase(service,current,name,graph)
                from .video_acceleration import prepare_phase
                graph = prepare_phase(service, current, name, graph)
            check = await check_graph(service.client, service.settings['comfy_url'], graph)
            if not check['ok']:
                raise ValueError(' · '.join(check['errors']))
            if not await service.idle():
                service.store.update(job['id'], status='queued', stage='Esperando la cola externa antes de '+name)
                return None
            await service.release_ollama(job['id'])
            if not await service.idle():
                service.store.update(job['id'], status='queued', stage='La cola externa cambió; esperando')
                return None
            if job['parameters'].get('motion_h3_segmented') and service.store.job(job['id']).get('motion_h3_stop_after_window'):
                service.store.update(job['id'],status='cancelled',stage='Detenido antes del siguiente tramo · Diagnóstico conservado',progress=None)
                return None
            client_id = str(uuid4())
            cancel_checkpoint(service, job['id'])
            (folder/(name+'-workflow.api.json')).write_text(json.dumps(graph, indent=2), encoding='utf-8')
            service.store.update(job['id'], status='submitting', guided_phase=name, prompt_id=None,
                client_id=client_id, stage='Enviando '+name+' a ComfyUI', progress=None)
            try:
                result = await service.post(service.settings['comfy_url'], '/prompt', json={'prompt': graph, 'client_id': client_id}, timeout=60)
            except httpx.HTTPError as error:
                service.store.update(job['id'], status='submission_unknown', error=f'Envío de {name} sin confirmar: {error}. Revisa ComfyUI antes de repetir.')
                return None
            prompt_id = result.get('prompt_id')
            if not prompt_id:
                service.store.update(job['id'], status='submission_unknown', error='ComfyUI no devolvió prompt_id; no se repetirá automáticamente.')
                return None
            service.store.update(job['id'], status='running', prompt_id=prompt_id, stage='Generando '+name+' en ComfyUI')
            cancel_checkpoint(service, job['id'])
            service.event(job['id'], name+': ComfyUI aceptó el workflow tras liberar Ollama.')
        socket, receiver = await service.connect_progress(service.store.job(job['id']).get('client_id'), job['id'])
        connection_failures=missing_history=0
        reconnect_stage='El motor no responde; intentando recuperar el estado…'
        while True:
            try:
                history = await service.get(service.settings['comfy_url'], '/history/'+prompt_id)
            except httpx.HTTPError:
                connection_failures+=1
                if connection_failures>=12:
                    service.store.update(job['id'],status='submission_unknown',progress=None,
                        stage='Motor sin conexión; generación pendiente de recuperar',
                        error='ComfyUI dejó de responder. Se conserva el identificador y no se reenvía la generación automáticamente.')
                    return None
                service.store.update(job['id'], stage=reconnect_stage, _engine_contact_error=True)
                await asyncio.sleep(5)
                continue
            entry = history.get(prompt_id)
            service.record_engine_contact(job['id'], 'history' if entry else 'event')
            if not entry:
                try:
                    queue=await service.get(service.settings['comfy_url'],'/queue')
                except httpx.HTTPError:
                    queue=None
                    connection_failures+=1
                    if connection_failures>=12:
                        service.store.update(job['id'],status='submission_unknown',progress=None,
                            stage='No se pudo verificar la cola de ComfyUI',error='No se reenvía la generación sin confirmar su estado.')
                        return None
                    service.store.update(job['id'],stage=reconnect_stage, _engine_contact_error=True)
                if queue is not None:
                    connection_failures=0
                    running=any(isinstance(item,(list,tuple)) and len(item)>1 and item[1]==prompt_id
                        for item in queue.get('queue_running',[]))
                    pending=any(isinstance(item,(list,tuple)) and len(item)>1 and item[1]==prompt_id
                        for item in queue.get('queue_pending',[]))
                    present=running or pending
                    service.record_engine_contact(job['id'], 'running' if running else 'queued' if pending else 'event')
                    current=service.store.job(job['id'])
                    if (present and current.get('prompt_id')==prompt_id and current.get('status')=='running'
                            and not current.get('cancel_requested')
                            and current.get('stage') in {reconnect_stage,'Reconectando al historial de '+name}):
                        service.store.update(job['id'],stage='Generando · Conexión con el motor recuperada')
                    missing_history=0 if present else missing_history+1
                    if missing_history>=3:
                        service.store.update(job['id'],status='submission_unknown',progress=None,
                            stage='El motor perdió el historial de esta generación',
                            error='El identificador no aparece en el historial ni en la cola. Posible reinicio de ComfyUI; no se repite automáticamente.')
                        return None
            if entry:
                terminal = True
                (folder/(name+'-history.json')).write_text(json.dumps(entry, indent=2), encoding='utf-8')
                if entry.get('status', {}).get('status_str') != 'success':
                    raise ValueError('ComfyUI: '+json.dumps(entry.get('status', {}), ensure_ascii=False)[:3000])
                descriptors = [item for values in entry.get('outputs', {}).get(node, {}).values() if isinstance(values, list)
                    for item in values if isinstance(item, dict) and item.get('filename', '').lower().endswith(extension)]
                if len(descriptors) != 1:
                    raise ValueError('ComfyUI no devolvió una única salida '+extension+' para '+name)
                info = descriptors[0]
                service.store.update(job['id'], stage='Guardando la salida de '+name, progress=None)
                path = folder/(name+extension)
                temporary = folder/(name+'-'+str(uuid4())+'.part')
                async with service.client.stream('GET', service.settings['comfy_url']+'/view',
                    params={k: info[k] for k in ('filename', 'subfolder', 'type') if k in info}, timeout=180) as response:
                    response.raise_for_status()
                    with temporary.open('wb') as out:
                        async for chunk in response.aiter_bytes():
                            out.write(chunk)
                if path.exists():
                    if digest(path) != digest(temporary):
                        raise ValueError('Ya existe otra salida diferente. Se conservan las dos copias.')
                    temporary.unlink()
                else:
                    temporary.replace(path)
                saved = {'path': str(path), 'sha256': digest(path), 'prompt_id': prompt_id}
                checkpoint.write_text(json.dumps(saved, indent=2), encoding='utf-8')
                service.store.update(job['id'], status='preparing', prompt_id=None, progress=None)
                return saved
            await asyncio.sleep(2)
    finally:
        if receiver:
            receiver.cancel()
            await asyncio.gather(receiver, return_exceptions=True)
        if socket:
            await socket.close()
        if terminal:
            await service.free_after_terminal(job['id'])


def preserve_copy(source, target):
    if target.is_file():
        if digest(source) != digest(target):
            raise ValueError('Existe otra copia registrada; no se sobrescribe.')
    else:
        shutil.copy2(source, target)


async def process(service, job):
    payload = job['parameters']
    folder = service.root/'jobs'/job['id']
    folder.mkdir(parents=True, exist_ok=True)
    source = local_path(service.root, payload['head_source'])
    references = [service.store.asset(payload[k]) for k in ('face_reference_id', 'sheet_reference_id')]
    paths = [local_path(service.root, service.root/'inputs'/a['filename']) for a in references]
    for path, key in [(source, 'head_source_sha256'), (paths[0], 'head_photo_sha256'), (paths[1], 'head_sheet_sha256')]:
        if digest(path) != payload[key]:
            raise ValueError('Una entrada cambió desde el envío del detalle H3.')
    plan = await cpu(service, job, 'track', {'source': str(source), 'source_sha256': payload['head_source_sha256'],
        'photo': str(paths[0]), 'photo_sha256': payload['head_photo_sha256'], 'region': payload['head_region']})
    pose = await phase(service, job, 'pose', pose_graph(plan), 'pose-preview', '.webp')
    if pose is None:
        return
    plan_path = folder/'track-report.json'
    cropped = await cpu(service, job, 'crop', {'plan': str(plan_path), 'plan_sha256': digest(plan_path),
        'pose': pose['path'], 'pose_sha256': pose['sha256']})
    names = []
    for asset, path in zip(references, paths):
        with path.open('rb') as stream:
            uploaded = await service.post(service.settings['comfy_url'], '/upload/image',
                files={'image': (asset['filename'], stream)}, data={'overwrite': 'false', 'subfolder': f'gen_studio/{job["id"]}'})
        names.append('/'.join(part for part in (uploaded.get('subfolder', ''), uploaded['name']) if part))
    head = await phase(service, job, 'head', graph_for(plan, cropped, *names, payload, job['id']), '16', '.mp4')
    if head is None:
        return
    metadata = await asyncio.to_thread(probe, Path(head['path']))
    if (metadata['width'], metadata['height']) != (768, 768) or abs(metadata['duration']-plan['frames']/24) > .05:
        raise ValueError('La salida H3 no conserva tamaño o duración. Se conserva para diagnóstico.')
    inspection = await cpu(service, job, 'inspect', {'head': head['path'], 'head_sha256': head['sha256'],
        'appearance': cropped['reference'], 'appearance_sha256': cropped['reference_sha256'],
        'photo': str(paths[0]), 'photo_sha256': payload['head_photo_sha256'], 'frames': plan['frames']})
    for path, key in [(source, 'head_source_sha256'), (paths[0], 'head_photo_sha256'), (paths[1], 'head_sheet_sha256')]:
        if digest(path) != payload[key]:
            raise ValueError('Cambió una entrada durante la generación; no se publica el detalle.')
    candidate = 'h3-'+job['id']
    output = service.root/'identity-tests'/candidate
    body = service.root/'identity-tests'/('h3-body-'+job['id'])
    output.mkdir(parents=True, exist_ok=True)
    body.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(preserve_copy, source, body/'corrected.mp4')
    await asyncio.to_thread(preserve_copy, Path(head['path']), output/'corrected.mp4')
    await asyncio.to_thread(preserve_copy, plan_path, output/'preparation.json')
    await asyncio.to_thread(preserve_copy, Path(inspection['reference']), output/'reference-face.png')
    await asyncio.to_thread(preserve_copy, Path(inspection['comparison']), output/'face-comparison.png')
    results = service.root/'results'
    results.mkdir(exist_ok=True)
    await asyncio.to_thread(preserve_copy, Path(head['path']), results/(job['id']+'.mp4'))
    parent_id = payload['head_parent_id']
    (body/'video-report.json').write_text(json.dumps({'source_job': parent_id, 'review_eligible': False}), encoding='utf-8')
    recipe = {'version': 1, 'source_job': parent_id, 'body_trial': body.name,
        'head_sha256': head['sha256'], 'body_sha256': payload['head_source_sha256'],
        'plan_sha256': digest(output/'preparation.json'), 'name': 'Detalle H3 · '+job['id'][:8]}
    (output/'guided-composition.json').write_text(json.dumps(recipe, indent=2), encoding='utf-8')
    report_path = output/'video-report.json'
    if not report_path.exists():
        report_path.write_text(json.dumps({'source_job': parent_id, 'head_job': job['id'],
            'name': recipe['name'], 'description': 'Nueva cabeza H3 con foto real, referencia de personaje y guía de pose. Revisa identidad, giro y accesorios antes de aprobar.',
            'frames': plan['frames'], 'processed_frames': plan['frames'], 'review_eligible': True,
            'scene_composited': False, 'identity_approved': False, 'cpu_face_swap': False}, indent=2), encoding='utf-8')
    return service.store.update(job['id'], status='completed', result_available=True, progress=None,
        stage='Detalle H3 generado · Revisa rostro y accesorios', guided_candidate=candidate,
        scene_resolution='768x768', head_review={'identity_approved': False, 'review_required': True})
