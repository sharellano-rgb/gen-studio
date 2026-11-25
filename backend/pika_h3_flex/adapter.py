import asyncio
import hashlib
import json
import subprocess
import time
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlencode
import httpx
import websockets
from ..head import digest
from ..job_cancel import checkpoint,requested,thread_checkpoint
from ..store import now
from .graphs import graph
from .profiles import BASE,SIZES,VERSION,dimensions,PROFILE,engine_name
from .runtime import Session,atomic

def prepare_image(source,target,format,profile=PROFILE):
    w,h=dimensions(dict(format=format,profile=profile))
    value=subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-n','-i',str(source),
        '-vf',f'scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=white,setsar=1',
        '-frames:v','1',str(target)],capture_output=True,timeout=90,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if value.returncode:raise ValueError('No se pudo preparar la imagen inicial de Pika: '+value.stderr.decode(errors='replace')[-500:])

def accepted_graph(folder,job):
    path=folder/'pika-workflow.json'
    if not path.is_file() or digest(path)!=job.get('pika_graph_sha256'):
        raise ValueError('Cambió el workflow aceptado de Pika; no se reenviará.')
    return json.loads(path.read_text(encoding='utf-8'))

async def reconcile(service,job):
    """Find the one original POST by its private client/job binding, never resubmit."""
    queue=await service.get(BASE,'/queue')
    matches=[]
    entries=[*queue.get('queue_running',[]),*queue.get('queue_pending',[])]
    history=await service.get(BASE,'/history?max_items=100')
    entries.extend(item.get('prompt',[]) for item in history.values())
    for row in entries:
        if len(row)>3 and isinstance(row[3],dict):
            extra=row[3]
            if extra.get('client_id')==job.get('client_id') and extra.get('gen_studio_job_id')==job['id'] and extra.get('graph_sha256')==job.get('pika_graph_sha256'):
                matches.append(row[1])
    matches=set(matches)
    if len(matches)!=1:raise ValueError('No se pudo identificar un único envío original de Pika; no se repetirá automáticamente.')
    prompt_id=matches.pop();service.store.update(job['id'],prompt_id=prompt_id,status='running')
    return prompt_id

async def collect(service,job,entry):
    folder=service.root/'jobs'/job['id'];atomic(folder/'pika-history.json',entry)
    if entry.get('status',{}).get('status_str')!='success':
        raise ValueError('Pika: '+json.dumps(entry.get('status',{}),ensure_ascii=False)[:3000])
    descriptors=[i for values in entry.get('outputs',{}).get('save',{}).values() if isinstance(values,list) for i in values if isinstance(i,dict) and str(i.get('filename','')).lower().endswith('.mp4')]
    if len(descriptors)!=1:raise ValueError('No se recibió un único vídeo del nodo de salida Pika.')
    service.store.update(job['id'],status='downloading',stage='Guardando vídeo Pika',progress=None)
    info=descriptors[0];params={k:info[k] for k in ('filename','subfolder','type') if k in info}
    target=service.root/'results'/(job['id']+'.mp4');target.parent.mkdir(exist_ok=True)
    partial=target.with_suffix('.part')
    async with service.client.stream('GET',BASE+'/view',params=params,timeout=120) as response:
        response.raise_for_status();size=0
        with partial.open('wb') as output:
            async for chunk in response.aiter_bytes():
                checkpoint(service,job['id']);size+=len(chunk)
                if size>200*1024**2:raise ValueError('La salida excede el límite de la toma Pika.')
                output.write(chunk)
    checkpoint(service,job['id'])
    from ..media import probe
    media=await thread_checkpoint(service,job['id'],probe,partial)
    w,h=dimensions(job['parameters'])
    if (media['width'],media['height'])!=(w,h) or abs(media['duration']-124/24)>.1:
        raise ValueError('La salida no conserva la resolución o duración del perfil Pika.')
    output_w,output_h=dimensions(job['parameters'],output=True)
    if (output_w,output_h)!=(w,h):
        converted=folder/'output-720p.mp4'
        await thread_checkpoint(service,job['id'],resize_output,partial,converted,output_w,output_h)
        converted.replace(partial)
        media=await thread_checkpoint(service,job['id'],probe,partial)
        if (media['width'],media['height'])!=(output_w,output_h) or abs(media['duration']-124/24)>.1:
            raise ValueError('La salida 720p no conserva su resolución o duración.')
    w,h=output_w,output_h
    result_sha=await thread_checkpoint(service,job['id'],digest,partial)
    partial.replace(target)
    service.store.update(job['id'],status='completed',stage='Vídeo '+engine_name(job['parameters'])+' listo para revisar',result_available=True,progress=None,
        scene_resolution=f'{w}x{h}',pika_result_sha256=result_sha,pika_media=media,pika_graph_version=VERSION)

async def generate(service,job,session):
    ident=job['id'];folder=service.root/'jobs'/ident;folder.mkdir(parents=True,exist_ok=True)
    current=service.store.job(ident)
    resuming=bool(current.get('pika_submission_attempted') or current.get('prompt_id'))
    if not resuming:
        if current.get('engine_instance_id') and await session.attach():
            # No POST was attempted: free a pre-submission survivor before Qwen.
            await session.stop()
            session.record=session.process=None
        await session.external_idle()
        from ..qwen_director import before_job,before_phase,asset
        if not await before_job(service,current):return
        checkpoint(service,ident)
        # The existing primary engine remains unchanged; only release its idle models.
        await service.release_ollama(ident)
        await session.external_idle()
        await service.post(service.settings['comfy_url'],'/free',json={'unload_models':True,'free_memory':True})
        current=service.store.job(ident)
        await session.start_or_resume(current)
        source=asset(service,current['parameters']['reference_id'],'image')
        prepared=folder/'pika-first-frame.png'
        if not prepared.exists():await thread_checkpoint(service,ident,prepare_image,source,prepared,current['parameters']['format'],current['parameters'].get('profile',PROFILE))
        if current.get('pika_graph_sha256'):
            workflow=accepted_graph(folder,current)
        else:
            with prepared.open('rb') as file:
                uploaded=await service.post(BASE,'/upload/image',files={'image':(f'{ident}.png',file,'image/png')},data={'type':'input','overwrite':'false'},timeout=120)
            image=uploaded['name'] if not uploaded.get('subfolder') else uploaded['subfolder'].strip('/')+'/'+uploaded['name']
            workflow=graph(current['parameters'],image,ident)
            workflow=await before_phase(service,current,'pika-i2va',workflow)
            if workflow is None:return
            path=folder/'pika-workflow.json';path.write_text(json.dumps(workflow,indent=2,ensure_ascii=False),encoding='utf-8',newline='\n')
            service.store.update(ident,pika_graph_sha256=digest(path),pika_reference_sha256=digest(source),pika_first_frame_sha256=digest(prepared),client_id=str(uuid4()))
    else:
        workflow=accepted_graph(folder,current)
        await session.start_or_resume(current)
    current=service.store.job(ident)
    socket=receiver=None
    try:
        if not service.settings.get('disable_websocket'):
            try:
                socket=await websockets.connect('ws://127.0.0.1:8191/ws?'+urlencode({'clientId':current['client_id']}),open_timeout=5,max_size=8*1024**2)
                receiver=asyncio.create_task(service.listen(socket,ident))
            except Exception:service.event(ident,'Seguimiento Pika por historial; WebSocket no disponible.')
        if resuming:
            prompt_id=current.get('prompt_id') or await reconcile(service,current)
        else:
            checkpoint(service,ident)
            service.store.update(ident,status='submitting',stage='Enviando a Pika',pika_submission_attempted=True)
            try:
                response=await service.post(BASE,'/prompt',json={'prompt':workflow,'client_id':current['client_id'],
                    'extra_data':{'gen_studio_job_id':ident,'graph_sha256':current['pika_graph_sha256']}},timeout=60)
                prompt_id=response['prompt_id']
                service.store.update(ident,prompt_id=prompt_id,status='running',stage='Generando vídeo con '+engine_name(current['parameters']))
            except Exception:
                prompt_id=await reconcile(service,service.store.job(ident))
        previous=current.get('pika_memory') or {}
        started=time.monotonic();last_resource=0;peak=previous.get('peak_global_used_mib',0);minimum=previous.get('min_global_free_mib');disconnected=None
        while True:
            checkpoint(service,ident)
            await session.external_idle()
            if (await service.get(service.settings['ollama_url'],'/api/ps')).get('models'):raise ValueError('Otra tarea cargó Ollama; se detuvo únicamente Pika.')
            if time.monotonic()-last_resource>=3:
                value=await session.resources();last_resource=time.monotonic()
                peak=max(peak,value['gpu_used_mib']);minimum=min(minimum,value['gpu_free_mib']) if minimum is not None else value['gpu_free_mib']
                service.store.update(ident,pika_memory={'peak_global_used_mib':peak,'min_global_free_mib':minimum,'poll_seconds':3,'includes_other_services':True})
                if value['gpu_free_mib']<1024 or value['host_available_bytes']<8*1024**3:
                    raise ValueError('Pika se detuvo por margen de memoria insuficiente; no se cambió el perfil ni se repitió el envío.')
            try:
                history=await service.get(BASE,f'/history/{prompt_id}')
                disconnected=None
            except httpx.TransportError:
                if disconnected is None:disconnected=time.monotonic()
                if time.monotonic()-disconnected>30:raise
                service.store.update(ident,stage='Reconectando con la toma original de Pika')
                await asyncio.sleep(1)
                continue
            service.record_engine_contact(ident,'history' if prompt_id in history else 'running')
            if prompt_id in history:
                await collect(service,service.store.job(ident),history[prompt_id]);return
            if time.monotonic()-started>2700:raise ValueError('La toma Pika excedió el límite de 45 minutos.')
            await asyncio.sleep(1)
    finally:
        if receiver:
            receiver.cancel();await asyncio.gather(receiver,return_exceptions=True)
        if socket:await socket.close()

async def process(service,job):
    """Own cleanup stays inside the existing cross-workspace generation lease."""
    ident=job['id'];session=Session(service,job);task=waiter=None;shutdown=False;error=None
    service.active_job_id=ident;service.cancel_wake.clear()
    try:
        if requested(service,ident):
            await session.attach()
        else:
            task=asyncio.create_task(generate(service,job,session));waiter=asyncio.create_task(service.cancel_wake.wait())
            await asyncio.wait({task,waiter},return_when=asyncio.FIRST_COMPLETED)
            if requested(service,ident):
                while not task.done() and service.store.job(ident)['status']=='submitting':
                    await asyncio.wait({task},timeout=.1)
                if not task.done():task.cancel()
                await asyncio.gather(task,return_exceptions=True)
            else:await task
    except asyncio.CancelledError:
        if not requested(service,ident):shutdown=True
        if shutdown:raise
    except Exception as failure:
        error=str(failure)[:3500]
        service.store.update(ident,status='failed',stage='No se pudo completar Pika',error=error,progress=None)
    finally:
        for pending in (task,waiter):
            if pending and not pending.done():pending.cancel()
        await asyncio.gather(*(t for t in (task,waiter) if t),return_exceptions=True)
        if not shutdown:
            try:
                current=service.store.job(ident)
                if current.get('prompt_id') and (error or requested(service,ident)):
                    try:await session.interrupt(current['prompt_id'])
                    except Exception:pass
                cleanup=asyncio.create_task(session.stop())
                try:await asyncio.shield(cleanup)
                except asyncio.CancelledError:await cleanup;raise
                if requested(service,ident):service.store.update(ident,status='cancelled',stage='Pika cancelado · Recursos liberados',cancelled_at=now(),cancel_resources_released=True,progress=None,qwen_waiting=False)
            except Exception as failure:
                service.store.update(ident,cancel_resources_released=False,cancel_cleanup_warning=str(failure)[:1000])
        elif session.log:session.log.close()
        service.active_job_id=None


def resize_output(source,target,width,height):
    value=subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-y','-i',str(source),'-map','0:v:0','-map','0:a?',
        '-vf',f'scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1',
        '-c:v','libx264','-crf','18','-pix_fmt','yuv420p','-c:a','copy','-movflags','+faststart',str(target)],capture_output=True,timeout=180,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if value.returncode:raise ValueError('No se pudo ajustar la salida a 720p: '+value.stderr.decode(errors='replace')[-500:])
