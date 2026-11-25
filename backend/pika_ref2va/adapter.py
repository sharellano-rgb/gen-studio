import asyncio,json,subprocess,time
from pathlib import Path
from uuid import uuid4
import httpx,websockets
from ..head import digest
from ..job_cancel import checkpoint,requested,thread_checkpoint
from ..pika_h3_flex.runtime import Session,atomic
from ..pika_h3_flex.adapter import accepted_graph,reconcile
from .profiles import BASE,dimensions,VERSION,inventory
from .graphs import graph
from . import swap_guide

async def release_idle_engines(service,job):
    session=Session(service,job)
    await session.external_idle()
    bases=[service.settings['comfy_url'],*service.settings.get('pika_external_comfy_urls',['http://127.0.0.1:8199','http://127.0.0.1:8190'])]
    for base in dict.fromkeys(bases):
        checkpoint(service,job['id']);await session.external_idle()
        try:await service.post(base,'/free',json={'unload_models':True,'free_memory':True})
        except httpx.ConnectError:
            if base==service.settings['comfy_url']:raise

def prepare_video(source,target,p):
    w,h=dimensions(p)
    filters=f'fps=24,scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,tpad=stop_mode=clone:stop_duration=6'
    result=subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-n','-i',str(source),'-vf',filters,
        '-frames:v',str(p['frames']),'-an','-c:v','libx264','-crf','18','-pix_fmt','yuv420p',str(target)],
        capture_output=True,timeout=180,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise ValueError('No se pudo preparar el tramo Pika: '+result.stderr.decode(errors='replace')[-700:])

async def upload(service,path,ident):
    with Path(path).open('rb') as file:
        item=await service.post(BASE,'/upload/image',files={'image':(Path(path).name,file)},data={'overwrite':'false','subfolder':f'gen-studio-ref2va/{ident}'},timeout=120)
    return '/'.join(x for x in (item.get('subfolder',''),item['name']) if x)

async def collect(service,job,entry):
    from ..media import probe
    p=job['parameters'];ident=job['id'];folder=service.root/'jobs'/ident
    atomic(folder/'pika-history.json',entry)
    if entry.get('status',{}).get('status_str')!='success':raise ValueError('Pika: '+json.dumps(entry.get('status',{}))[:2500])
    outputs=[i for values in entry.get('outputs',{}).get('save',{}).values() if isinstance(values,list) for i in values if isinstance(i,dict) and str(i.get('filename','')).endswith('.mp4')]
    if len(outputs)!=1:raise ValueError('Falta una única salida Ref2VA.')
    target=service.root/'results'/(ident+'.mp4');target.parent.mkdir(exist_ok=True);partial=target.with_suffix('.part')
    params={k:outputs[0][k] for k in ('filename','subfolder','type') if k in outputs[0]}
    service.store.update(ident,status='downloading',stage='Guardando tramo Pika')
    async with service.client.stream('GET',BASE+'/view',params=params,timeout=120) as response:
        response.raise_for_status();size=0
        with partial.open('wb') as f:
            async for chunk in response.aiter_bytes():
                checkpoint(service,ident);size+=len(chunk)
                if size>200*1024**2:raise ValueError('La salida Pika supera el límite del tramo.')
                f.write(chunk)
    media=await thread_checkpoint(service,ident,probe,partial);w,h=dimensions(p)
    if (media['width'],media['height'])!=(w,h) or abs(media['duration']-p['frames']/24)>.06:
        raise ValueError('El tramo Pika no conserva su resolución o duración.')
    sha=await thread_checkpoint(service,ident,digest,partial)
    if target.exists() and digest(target)!=sha:raise ValueError('Ya existe otro resultado de esta toma.')
    partial.replace(target)
    service.store.update(ident,status='completed',stage='Tramo Pika listo',result_available=True,progress=None,
        pika_result_sha256=sha,pika_media=media,pika_graph_version=VERSION,scene_resolution=p['resolution'])

async def generate(service,job,session):
    from ..qwen_director import asset,before_job,before_phase
    p=job['parameters'];ident=job['id'];folder=service.root/'jobs'/ident;folder.mkdir(parents=True,exist_ok=True)
    if p.get('pika_ref2va_version')!=VERSION or not p.get('sequence_parent_id'):raise ValueError('Pika Ref2VA requiere su secuencia y versión registradas.')
    source=asset(service,p['video_id'],'video');reference=asset(service,p['reference_id'],'image')
    for path,expected in [(source,p['source_sha256']),(reference,p['reference_sha256'])]:
        if await thread_checkpoint(service,ident,digest,path)!=expected:raise ValueError('Cambió una entrada de Pika; crea otra toma.')
    current=service.store.job(ident);resuming=bool(current.get('pika_submission_attempted') or current.get('prompt_id') and not swap_guide.pending(current))
    if resuming:
        workflow=accepted_graph(folder,current);await session.start_or_resume(current)
    else:
        if current.get('engine_instance_id') and await session.attach():
            await session.stop();session.record=session.process=None
        if not swap_guide.pending(current):
            await release_idle_engines(service,job)
            if not await before_job(service,current):return
            checkpoint(service,ident)
            await service.release_ollama(ident);await release_idle_engines(service,job)
        check=await asyncio.to_thread(inventory,service.settings)
        if not check['ok']:raise ValueError(' · '.join(check['errors']))
        prepared=folder/'pika-source.mp4'
        if swap_guide.enabled(p):
            if not prepared.exists():await thread_checkpoint(service,ident,prepare_video,source,prepared,p)
            prepared=await swap_guide.prepare(service,service.store.job(ident),prepared)
            if prepared is None:return
        current=service.store.job(ident);await session.start_or_resume(current)
        if current.get('pika_graph_sha256'):workflow=accepted_graph(folder,current)
        else:
            if not prepared.exists():await thread_checkpoint(service,ident,prepare_video,source,prepared,p)
            image=await upload(service,reference,ident);video=await upload(service,prepared,ident)
            workflow=await before_phase(service,current,'pika-ref2va',graph(p,image,video,ident))
            if workflow is None:return
            from ..video_sequences import before_phase as guide
            workflow=await guide(service,current,'pika-ref2va',workflow,upload_fn=upload)
            required={n['class_type'] for n in workflow.values()};info=await service.get(BASE,'/object_info')
            if not required<=info.keys():raise ValueError('Faltan nodos nativos Pika: '+', '.join(required-info.keys()))
            path=folder/'pika-workflow.json';atomic(path,workflow)
            service.store.update(ident,pika_graph_sha256=digest(path),pika_source_sha256=p['source_sha256'],
                pika_reference_sha256=p['reference_sha256'],pika_prepared_sha256=digest(prepared),client_id=str(uuid4()))
    current=service.store.job(ident);socket=receiver=None
    try:
        if not service.settings.get('disable_websocket'):
            try:
                socket=await websockets.connect('ws://127.0.0.1:8191/ws?clientId='+current['client_id'],open_timeout=5,max_size=8*1024**2)
                receiver=asyncio.create_task(service.listen(socket,ident))
            except Exception:service.event(ident,'Seguimiento de Ref2VA por historial.')
        if resuming:prompt_id=current.get('prompt_id') or await reconcile(service,current)
        else:
            checkpoint(service,ident);service.store.update(ident,status='submitting',stage='Enviando tramo Pika',pika_submission_attempted=True)
            try:
                response=await service.post(BASE,'/prompt',json={'prompt':workflow,'client_id':current['client_id'],
                    'extra_data':{'gen_studio_job_id':ident,'graph_sha256':current['pika_graph_sha256']}},timeout=60)
                prompt_id=response['prompt_id'];service.store.update(ident,prompt_id=prompt_id,status='running',stage='Generando con Pika Ref2VA')
            except Exception:prompt_id=await reconcile(service,service.store.job(ident))
        start=time.monotonic();last=0;peak=0;minimum=None;disconnected=None
        while True:
            checkpoint(service,ident);await session.external_idle()
            if (await service.get(service.settings['ollama_url'],'/api/ps')).get('models'):raise ValueError('Otra tarea cargó Ollama; se detuvo únicamente Pika.')
            if time.monotonic()-last>=3:
                resources=await session.resources();last=time.monotonic();peak=max(peak,resources['gpu_used_mib'])
                minimum=min(minimum,resources['gpu_free_mib']) if minimum is not None else resources['gpu_free_mib']
                service.store.update(ident,pika_memory={'peak_global_used_mib':peak,'min_global_free_mib':minimum,'poll_seconds':3,'includes_other_services':True})
                if resources['gpu_free_mib']<1024 or resources['host_available_bytes']<8*1024**3:raise ValueError('Pika se detuvo por margen de memoria insuficiente.')
            try:history=await service.get(BASE,'/history/'+prompt_id);disconnected=None
            except httpx.TransportError:
                if disconnected is None:disconnected=time.monotonic()
                if time.monotonic()-disconnected>30:raise
                await asyncio.sleep(1);continue
            service.record_engine_contact(ident,'history' if prompt_id in history else 'running')
            if prompt_id in history:
                await collect(service,service.store.job(ident),history[prompt_id]);return
            if time.monotonic()-start>2700:raise ValueError('El tramo Pika superó 45 minutos.')
            await asyncio.sleep(1)
    finally:
        if receiver:receiver.cancel();await asyncio.gather(receiver,return_exceptions=True)
        if socket:await socket.close()

async def process(service,job):
    session=Session(service,job);shutdown=False
    try:await generate(service,job,session)
    except asyncio.CancelledError:
        shutdown=not requested(service,job['id'])
        raise
    finally:
        if not shutdown:
            current=service.store.job(job['id'])
            if swap_guide.pending(current):
                try:await swap_guide.cancel(service,current)
                except Exception as error:
                    service.store.update(job['id'],status='submission_unknown',cancel_resources_released=False,
                        stage='Guía pendiente de recuperar · No se reenviará',error=str(error)[:1000])
                    raise
                current=service.store.job(job['id'])
            if current.get('prompt_id') and current['status']!='completed':
                try:await session.interrupt(current['prompt_id'])
                except Exception:pass
            cleanup=asyncio.create_task(session.stop())
            try:await asyncio.shield(cleanup)
            except asyncio.CancelledError:await cleanup;raise
        elif session.log:session.log.close()

async def cancel(service,job):
    from ..store import now
    session=Session(service,job)
    try:
        await swap_guide.cancel(service,job)
        job=service.store.job(job['id'])
        if await session.attach():
            if job.get('prompt_id'):
                try:await session.interrupt(job['prompt_id'])
                except Exception:pass
            await session.stop()
        service.store.update(job['id'],status='cancelled',cancel_requested=True,qwen_waiting=False,
            stage='Pika cancelado · Recursos liberados',progress=None,cancelled_at=now(),cancel_resources_released=True)
    except Exception as error:
        service.store.update(job['id'],status='submission_unknown',cancel_resources_released=False,error=str(error)[:1000])
