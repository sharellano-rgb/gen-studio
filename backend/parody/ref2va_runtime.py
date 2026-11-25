"""Private Pika execution under Parody's existing shared GPU lease."""
import asyncio
import copy
import json
import shutil
import time
from uuid import uuid4
from . import ref2va_director as director
from .analysis_media import sha256
from .voice_runtime import local_input,atomic_json
from .speech import voice_path,fingerprint
from .speech_runtime import upload,generate,Cancelled,Uncertain
from .montage_runtime import command,inspect,CleanupFailed
from ..pika_h3_flex.runtime import Session
from ..pika_h3_flex.profiles import BASE
from ..workflows import check_graph


class PlanJobs:
    """Adapt process ownership/checkpoints; never create a native generation job."""
    def __init__(self,store,project,ident):self.store,self.project,self.ident=store,project,ident
    def job(self,ident):
        if ident!=self.ident:return None
        value=dict(self.store.read(self.project,self.ident),parameters={})
        # Let an accepted private job reconnect and reach its own terminal state.
        if value.get('submission_intent') and not value.get('comfy_finished'):value['cancel_requested']=False
        return value
    def update(self,ident,**patch):
        if ident!=self.ident:raise ValueError('El proceso pertenece a otro plan.')
        return self.store.update(self.project,self.ident,**patch)


class ProcessService:
    def __init__(self,service,store,record):
        self.service=service;self.store=PlanJobs(store,record['project_id'],record['id'])
    def __getattr__(self,name):return getattr(self.service,name)


class PrivateComfy:
    def __init__(self,service,session):
        self.service=service;self.session=session;self.settings=dict(service.settings,comfy_url=BASE)
    def __getattr__(self,name):return getattr(self.service,name)
    async def idle(self):
        await self.session.external_idle()
        q=await self.service.get(BASE,'/queue')
        return not q.get('queue_running') and not q.get('queue_pending')


async def prepare(service,record,folder,stopped,save):
    manifest=folder/'ref2va-prepared.json'
    if manifest.exists():
        meta=json.loads(manifest.read_text())
        if meta['plan_hash']!=record['plan_hash']:raise ValueError('Cambió el plan preparado.')
        work=(folder/meta['work']).resolve()
        if not work.is_relative_to(folder.resolve()):raise ValueError('Preparación fuera del plan.')
        for name,digest in meta['files'].items():
            path=(work/name).resolve()
            if not path.is_relative_to(work) or sha256(path)!=digest:raise ValueError('Cambió la preparación aceptada de Ref2VA.')
        return work
    stopped();plan=record['plan'];work=folder/uuid4().hex;work.mkdir()
    scene=local_input(service,plan['scene']);identity=local_input(service,plan['references'][0])
    voice=voice_path(service,plan['voice']['id'],plan['voice']['sha256'])
    for source,name in [(scene,'scene'),(identity,'identity')]:
        await command(['ffmpeg','-v','error','-nostdin','-n','-i',source,'-vf',"scale='min(768,iw)':-2",'-frames:v',1,work/(name+'-observation.png')],stopped)
    # Lossless format normalization only; the full scene stays the generation reference.
    await command(['ffmpeg','-v','error','-nostdin','-n','-i',scene,'-frames:v',1,work/'scene.png'],stopped)
    shutil.copyfile(voice,work/'voice.wav')
    local_input(service,plan['scene']);local_input(service,plan['references'][0]);voice_path(service,plan['voice']['id'],plan['voice']['sha256'])
    save(stage='Qwen revisa la escena preparada y propone la actuación')
    images=[work/'scene-observation.png',work/'identity-observation.png']
    if clip:=plan['speech'].get('phrase_clip'):
        from .phrase_reference import observe
        sheet,evidence=await observe(service,clip,work,stopped)
        images.append(sheet)
        atomic_json(work/'phrase-observations.json',evidence)
    direction=await director.propose(service,plan,work,images,stopped)
    atomic_json(work/'direction.json',direction);save(direction=direction)
    if direction['needs_review']:return None
    (work/'prompt.txt').write_text(director.prompt(plan,direction),encoding='utf-8')
    atomic_json(manifest,dict(plan_hash=record['plan_hash'],work=work.name,files={p.name:sha256(p) for p in work.iterdir() if p.is_file()}))
    return work


async def workflow(service,record,work,stopped):
    path=work/'workflow.json'
    if path.exists():
        expected=(record.get('submission_intent') or {}).get('workflow_sha256') or record.get('prepared_workflow_sha256')
        if not expected or sha256(path)!=expected:
            raise ValueError('Cambió el grafo enviado de Ref2VA.')
        return
    if record.get('submission_intent'):raise Uncertain('Falta el grafo del envío anterior; no se vuelve a enviar.')
    plan=record['plan'];graph=copy.deepcopy(plan['speech']['recipe'])
    if fingerprint(graph)!=plan['speech']['recipe_sha256']:raise ValueError('Cambió la receta aceptada de Ref2VA.')
    stopped()
    graph['image']['inputs']['image']=await upload(service,work/'scene.png',record['id'])
    graph['speech_audio']['inputs']['audio']=await upload(service,work/'voice.wav',record['id'])
    graph['conditioning']['inputs']['prompt']=(work/'prompt.txt').read_text(encoding='utf-8')
    graph['save']['inputs']['filename_prefix']='gen_studio/parody/'+record['id']+'/ref2va'
    report=await check_graph(service.client,BASE,graph);atomic_json(work/'preflight.json',report)
    if not report['ok']:raise ValueError('Los modelos/nodos de Ref2VA no superaron la validación; no se envía.')
    atomic_json(path,graph)
    from .render import RenderStore
    RenderStore(service.root).update(record['project_id'],record['id'],prepared_workflow_sha256=sha256(path))


async def finalize(raw,work,stopped):
    # Stream-copy BOTH native tracks. No WAV substitution, lip patch or time shift.
    original=await inspect(raw,124,1344,768,stopped)
    output=work/('native-'+uuid4().hex+'.mp4')
    await command(['ffmpeg','-v','error','-nostdin','-n','-i',raw,'-map','0:v:0','-map','0:a:0','-c','copy','-movflags','+faststart',
        '-metadata','comment=GEN STUDIO - PARODIA SINTETICA - AUDIO NATIVO - REVISION PENDIENTE',output],stopped)
    meta=await inspect(output,124,1344,768,stopped)
    clocks=[]
    for path in (raw,output):
        probe=json.loads(await command(['ffprobe','-v','error','-show_entries','stream=codec_type,start_time,duration','-of','json',path],stopped))
        streams={s['codec_type']:s for s in probe['streams'] if s['codec_type'] in ('audio','video')}
        clocks.append(dict(offset=float(streams['audio'].get('start_time',0))-float(streams['video'].get('start_time',0)),
                           audio_duration=float(streams['audio']['duration']),video_duration=float(streams['video']['duration'])))
    if any(abs(clocks[0][key]-clocks[1][key])>.001 for key in clocks[0]):
        raise ValueError('La entrega alteró los tiempos relativos de audio y vídeo nativos.')
    meta['native_timing']=clocks[1]
    # Compare decoded audio samples and frame pixels, independent of container metadata.
    for label,mapping,codec in [('video','0:v:0','rawvideo'),('audio','0:a:0','pcm_s16le')]:
        values=[]
        for path in (raw,output):
            value=await command(['ffmpeg','-v','error','-nostdin','-i',path,'-map',mapping,'-c',codec,'-f','hash','-hash','sha256','-'],stopped)
            values.append(value.strip())
        if values[0]!=values[1]:raise ValueError('La entrega alteró la pista nativa de '+label+'.')
        meta[label+'_decoded_hash']=values[0].decode()
    meta.update(audio_origin='native_generated',raw_sha256=original['sha256'])
    return output,meta


async def execute(service,record):
    from .render import RenderStore
    store=RenderStore(service.root);p=record['project_id'];ident=record['id'];folder=store.directory(ident);folder.mkdir(parents=True,exist_ok=True)
    session=Session(ProcessService(service,store,record),dict(id=ident));private=PrivateComfy(service,session)
    released=True;started=time.monotonic()
    def save(**patch):return store.update(p,ident,**patch)
    def stopped():
        if store.read(p,ident)['cancel_requested']:raise Cancelled()
    try:
        # Recover accepted work without re-running Qwen or mutating the accepted graph.
        work=await prepare(service,record,folder,stopped,save)
        if work is None:save(status='direction_review',stage='Revisa la escena o las indicaciones señaladas por Qwen');return
        record=store.read(p,ident)
        if record.get('submission_intent') and not record.get('prompt_id'):
            # Session must attach to its original process; it may not launch a replacement.
            job=dict(record,pika_submission_attempted=True)
        else:job=record
        if not record.get('comfy_finished') or not (work/'raw.mp4').is_file():
            from ..pika_ref2va.profiles import inventory
            check=await asyncio.to_thread(inventory,service.settings)
            if not check['ok']:raise ValueError(' · '.join(check['errors']))
            await session.start_or_resume(job)
            if not record.get('submission_intent'):stopped()
            await workflow(private,record,work,stopped)
            raw=await generate(private,record,work,stopped,save)
        else:
            await session.attach()
            raw=work/'raw.mp4'
        stopped();save(stage='Verificando vídeo y audio nativos sin sustituir la voz')
        output,meta=await finalize(raw,work,stopped);stopped()
        meta['file']=output.relative_to(folder).as_posix()
        save(status='review_pending',stage='Revisa diálogo, timbre, personaje y sincronización de Ref2VA',artifact=meta,review_required=True,error=None)
    except Cancelled:save(status='cancelled',stage='Ref2VA cancelado; evidencia conservada')
    except asyncio.CancelledError:
        latest=store.read(p,ident)
        released=not latest.get('submission_intent') or bool(latest.get('comfy_finished'))
        save(status='cancelled' if latest.get('cancel_requested') and released else 'interrupted',stage='Trabajo interrumpido; recuperar el mismo envío')
        raise
    except Uncertain as error:
        released=False;save(status='submission_unknown',stage='Recupera el mismo envío de Ref2VA, sin duplicarlo',error=str(error))
    except CleanupFailed as error:
        released=False;save(status='cleanup_failed',stage='No se confirmó limpieza; turno retenido',error=str(error))
    except Exception as error:
        latest=store.read(p,ident)
        released=not latest.get('submission_intent') or bool(latest.get('comfy_finished'))
        save(status='failed' if released else 'submission_unknown',stage='Ref2VA no completado; conserva evidencia y revisa el mismo plan',error=str(error)[:800])
    finally:
        if released:
            try:await asyncio.shield(session.stop())
            except BaseException:
                released=False;save(status='cleanup_failed',stage='No se confirmó el cierre del proceso privado; turno retenido')
        save(resources_released=released,elapsed_seconds=round(time.monotonic()-started,3))
        if released:
            service.enhancer_active=False;service.parody_speech_owner=None;service.release_analysis();service.wake.set()
