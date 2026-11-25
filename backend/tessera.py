"""One persistent upscaling service for library, final results and visible segments."""
import asyncio, json, os, shutil, time
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4
import httpx, websockets
from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from . import tessera_media as media
from .tessera_graph import VERSION, COMMIT, PROFILES, graph, ladder
from .store import now

OPS={'video_upscale','video_upscale_assembly'}
TERMINAL={'completed','failed','cancelled','submission_unknown'}
class Advanced(BaseModel):
    model_config=ConfigDict(extra='forbid')
    tile_size: Literal[512,768,1024]=768
    temporal_window: Literal[39,56,73,124]=73
    encode_window: Literal[39,56]=39
    seed: int=Field(default=786110,ge=0,le=2**53-1,strict=True)
class Request(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_key: UUID
    source_kind: Literal['asset','job']
    source_id: UUID
    segment_index: int|None=Field(default=None,ge=0,le=1000,strict=True)
    profile: Literal['balanced','high','maximum']='balanced'
    resolution: Literal['1080p','2k','4k']='1080p'
    advanced: Advanced=Field(default_factory=Advanced)
class Assembly(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_key: UUID
    confirm: Literal[True]

def cfg(service):return service.settings.get('tessera') or {}
def runtime(service):
    p=Path(cfg(service).get('root','')).resolve()
    if not cfg(service).get('root') or not (p/'vendor-manifest.json').is_file():raise ValueError('Falta el entorno aislado de Tessera.')
    return p
def base(service):return cfg(service).get('url','http://127.0.0.1:8197')
def checked(root,path):
    p=Path(path).resolve(strict=True)
    if not p.is_relative_to(Path(root).resolve()) or not p.is_file():raise ValueError('Archivo fuera de este espacio.')
    return p
def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.'+uuid4().hex+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(tmp,path)
def record(service,ident):return service.root/'jobs'/ident
def is_job(job):return bool(job and job['parameters'].get('operation') in OPS)
def cancelled(service,ident):return bool(service.store.job(ident).get('cancel_requested'))

def segments(service,job):
    p=job['parameters'];folder=record(service,job['id'])
    if job.get('video_sequence_version'):
        from .video_sequences import load
        r=load(service,job,False)
        return r['segments'] if r else []
    if p.get('operation')=='motion_bunny' and (folder/'bunny-plan.json').is_file():
        return json.loads((folder/'bunny-plan.json').read_text())['segments']
    # Historical long-take records are read, never reconstructed from guessed durations.
    for filename in ('long-plan.json','plan.json'):
        if (folder/filename).is_file():
            r=json.loads((folder/filename).read_text());ss=r.get('segments',[])
            if ss and all(all(k in s for k in ('index','start','keep')) for s in ss):return ss
    return []

def resolve(service,body):
    ident=str(body.source_id);parent=None;start=0;segment=None
    if body.source_kind=='asset':
        if body.segment_index is not None:raise ValueError('Los archivos de biblioteca no tienen tramos registrados.')
        asset=service.store.asset(ident)
        if not asset or asset['kind']!='video':raise ValueError('Vídeo no encontrado en este espacio.')
        path=checked(service.root,service.root/'inputs'/asset['filename']);expected=asset['sha256']
    else:
        parent=service.store.job(ident)
        if not parent:raise ValueError('Selecciona una toma de este espacio.')
        result_job=parent
        if body.segment_index is not None:
            segment=next((s for s in segments(service,parent) if s['index']==body.segment_index),None)
            if not segment:raise ValueError('Tramo no encontrado en el plan original.')
            start=segment['start']
            if not parent.get('result_available') and parent.get('video_sequence_version'):
                from .video_sequences import child_id
                result_job=service.store.job(child_id(parent,body.segment_index))
                start=segment.get('context_frames',0)
        if not result_job or result_job['status']!='completed' or not result_job.get('result_available'):raise ValueError('El tramo o resultado todavía no está terminado.')
        path=checked(service.root,service.root/'results'/(result_job['id']+('.face.mp4' if result_job.get('face_result_available') else '.mp4')))
        expected=next((result_job.get(k) for k in ('tessera_result_sha256','motion_result_sha256','direct_result_sha256','pika_result_sha256','alisson_result_sha256','secourses_result_sha256') if result_job.get(k)),None)
        if result_job.get('face_result_available'):expected=None
        if (result_job.get('motion_review') or {}).get('approved'):
            from .motion_versions import approved_result
            path=checked(service.root,approved_result(service,result_job));expected=result_job['motion_review'].get('sha256')
    # A short registered segment may come from an older, longer montage.
    # Only that selected interval reaches Tessera; the full montage is CPU I/O.
    info=media.inspect(path,max_frames=14400 if segment else 1440);sha=media.digest(path)
    if expected and sha!=expected:raise ValueError('El original cambió; se conserva sin procesarlo.')
    count=segment['keep'] if segment else info['frames']
    if not 0<=start<start+count<=info['frames']:raise ValueError('Intervalo fuera del vídeo original.')
    target=media.dimensions(info,body.resolution)
    return dict(path=str(path),sha256=sha,info=info,start=start,count=count,target=target)

def validate_capacity(service,source):
    need=media.estimate_disk({**source['info'],'frames':source['count']},source['target'])
    for root in {runtime(service),service.root}:
        if shutil.disk_usage(root).free<need:raise ValueError(f'Se requieren aproximadamente {need/1024**3:.1f} GiB libres para temporales y salida.')
    quota=int(cfg(service).get('max_result_bytes',100*1024**3))
    used=sum(p.stat().st_size for p in (service.root/'results').glob('*.mp4'))
    if used+source['count']*source['target']['width']*source['target']['height']*.2>quota:
        raise ValueError('Se alcanzó la cuota de resultados de este espacio.')
    return need

async def external_idle(service):
    if service.settings.get('pika_h3_flex_enabled'):
        from .pika_h3_flex.runtime import running
        if running(service.settings):return False
    try:
        q=await service.get(service.settings['comfy_url'],'/queue')
        if q.get('queue_running') or q.get('queue_pending'):return False
        await service.post(service.settings['comfy_url'],'/free',json={'unload_models':True,'free_memory':True})
    except httpx.ConnectError:
        # The configured local engine is stopped; never start or kill it here.
        pass
    return True

async def busy(service):
    if not cfg(service).get('enabled'):return False
    try:
        q=await service.get(base(service),'/queue')
        return bool(q.get('queue_running') or q.get('queue_pending'))
    except httpx.ConnectError:return False

async def progress(service,ident,client_id):
    url=base(service).replace('http://','ws://').replace('https://','wss://')+'/ws?clientId='+client_id
    try:
        async with websockets.connect(url,max_size=8*1024**2) as ws:
            async for raw in ws:
                if not isinstance(raw,str):continue
                event=json.loads(raw);data=event.get('data',{});job=service.store.job(ident)
                if data.get('prompt_id') and data['prompt_id']!=job.get('prompt_id'):continue
                if event['type']=='executing' and data.get('node') and job['status']=='running' and not job.get('cancel_requested'):
                    stages={'reference':'Codificando la referencia de vídeo','encode':'Tessera · codificando ventanas temporales','refine':'Tessera · refinando tiles','pixels':'Decodificando la salida de Tessera','video':'Preparando el vídeo de salida','save':'Guardando el vídeo del motor'}
                    service.store.update(ident,stage=stages.get(data['node'],'Preparando los modelos de Tessera'),progress=None)
                if event['type']=='progress' and job['status']=='running' and not job.get('cancel_requested'):
                    service.store.update(ident,stage='Tessera · progreso del nodo actual',progress={'value':data.get('value',0),'max':data.get('max',0)})
    except (OSError,ValueError,websockets.WebSocketException):pass

async def cancel_engine(service,job):
    pid=job.get('prompt_id')
    if not pid:return True
    q=await service.get(base(service),'/queue')
    pending=[x[1] for x in q.get('queue_pending',[])]
    running=[x[1] for x in q.get('queue_running',[])]
    if pid in pending:await service.post(base(service),'/queue',json={'delete':[pid]})
    if pid in running:
        if running!=[pid]:raise ValueError('No se interrumpe un motor con trabajo ajeno.')
        route='/gen-studio/tessera-interrupt' if cfg(service).get('memory_bridge') else '/interrupt'
        await service.post(base(service),route,json={'prompt_id':pid})
        return False
    return True

async def run(service,job):
    ident=job['id'];p=job['parameters'];folder=record(service,ident);folder.mkdir(parents=True,exist_ok=True)
    began=job.get('tessera_started_at') or (datetime.fromisoformat(job['created_at']).timestamp() if job.get('prompt_id') else time.time())
    service.store.update(ident,tessera_started_at=began)
    listener=None;engine_verified=False;native_summary=None;engine_seconds=None
    try:
        if cancelled(service,ident) and not job.get('prompt_id'):
            service.store.update(ident,status='cancelled',stage='Cancelado',progress=None);return
        source=p['tessera_source'];path=checked(service.root,source['path'])
        from .tessera_runtime import installation
        installed=await asyncio.to_thread(installation,service)
        if p.get('tessera_installation_sha256') and p['tessera_installation_sha256']!=installed:raise ValueError('La instalación ya no coincide con la asignada a este trabajo.')
        if await asyncio.to_thread(media.digest,path)!=source['sha256']:raise ValueError('Cambió el original registrado.')
        validate_capacity(service,{**source,'count':source['info']['frames']} if p['operation']=='video_upscale_assembly' else source)
        output=service.root/'results'/(ident+'.mp4');output.parent.mkdir(exist_ok=True)
        final=folder/'delivered.mp4'
        stop=lambda:cancelled(service,ident)
        if p['operation']=='video_upscale_assembly':
            replacement=checked(service.root,service.root/'results'/(p['tessera_replacement_id']+'.mp4'))
            if media.digest(replacement)!=p['tessera_replacement_sha256']:raise ValueError('Cambió la versión del tramo.')
            if not final.exists():
                service.store.update(ident,status='running',stage='Creando una nueva versión del montaje')
                await media.assemble(path,replacement,final,source['target'],source['start'],source['count'],source['info']['frames'],folder/'media.log',stop)
            count=source['info']['frames']
        else:
            count=source['count'];rt=runtime(service);pid=job.get('prompt_id')
            from .tessera_runtime import ensure,verify
            if pid:await verify(service);engine_verified=True
            if not pid:
                if not await external_idle(service):
                    service.store.update(ident,status='queued',stage='Esperando al motor compartido');await asyncio.sleep(2);return
                if await busy(service):
                    service.store.update(ident,status='queued',stage='Esperando a Tessera');await asyncio.sleep(2);return
                await service.release_ollama(ident)
                await ensure(service)
                engine_verified=True
                if stop():raise asyncio.CancelledError()
                inp=rt/'input'/('studio-'+ident+'.mp4')
                prepared=folder/'prepared.json'
                if not prepared.exists():
                    # Retry remnants belong solely to this job; use a fresh preparation path.
                    temp=rt/'input'/('studio-'+ident+'-'+uuid4().hex+'.mp4')
                    service.store.update(ident,status='running',stage='Preparando vídeo y conservando audio original',progress=None)
                    await media.prepare(path,temp,source['info'],source['target'],source['start'],count,folder/'media.log',stop)
                    os.replace(temp,inp);save(prepared,{'sha256':media.digest(inp)})
                elif not inp.is_file() or media.digest(inp)!=json.loads(prepared.read_text())['sha256']:
                    raise ValueError('Cambió el vídeo preparado; se requiere un trabajo nuevo.')
                s=source['target'];g=graph(inp.name,ident,s['canvas_width'],s['canvas_height'],ladder(count),p['tessera_profile'],p['tessera_advanced'])
                save(folder/'workflow.api.json',g)
                if stop():raise asyncio.CancelledError()
                client_id=ident
                service.store.update(ident,status='submitting',stage='Enviando a Tessera',client_id=client_id)
                try:r=await service.post(base(service),'/prompt',json={'prompt':g,'client_id':client_id,'extra_data':{'gen_studio_job_id':ident}},timeout=60)
                except httpx.HTTPError as error:
                    service.store.update(ident,status='submission_unknown',stage='Envío sin confirmar',error=str(error));return
                pid=r.get('prompt_id')
                if not pid:raise ValueError('Tessera no devolvió un identificador de ejecución.')
                service.store.update(ident,status='running',prompt_id=pid,stage='Procesando el vídeo con Tessera')
            listener=asyncio.create_task(progress(service,ident,job.get('client_id') or ident))
            failed_contacts=0
            while True:
                current=service.store.job(ident)
                try:
                    if stop():
                        service.store.update(ident,status='cancelling',stage='Cancelando Tessera y esperando liberación',progress=None)
                        if await cancel_engine(service,current):
                            service.store.update(ident,status='cancelled',stage='Cancelado',progress=None);return
                    history=await service.get(base(service),'/history/'+pid);failed_contacts=0
                    service.record_engine_contact(ident,'history' if pid in history else 'running')
                except httpx.HTTPError:
                    failed_contacts+=1
                    if failed_contacts>=30:raise ValueError('Tessera dejó de responder. Se conserva prompt_id; no se reenvía automáticamente.')
                    await asyncio.sleep(2);continue
                entry=history.get(pid)
                if entry:break
                q=await service.get(base(service),'/queue')
                if pid not in [x[1] for key in ('queue_running','queue_pending') for x in q.get(key,[])]:
                    raise ValueError('El motor perdió el trabajo aceptado; no se vuelve a generar automáticamente.')
                await asyncio.sleep(2)
            save(folder/'history.json',entry)
            native_summary=' '.join(str(v) for v in entry.get('outputs',{}).get('pixels',{}).get('text',[]))[:1200] or None
            events={e[0]:e[1].get('timestamp') for e in entry.get('status',{}).get('messages',[]) if isinstance(e,list) and len(e)==2 and isinstance(e[1],dict)}
            if isinstance(events.get('execution_start'),(int,float)) and isinstance(events.get('execution_success'),(int,float)):
                engine_seconds=round((events['execution_success']-events['execution_start'])/1000,3)
            if stop():service.store.update(ident,status='cancelled',stage='Cancelado',progress=None);return
            if entry.get('status',{}).get('status_str')!='success':
                error=json.dumps(entry.get('status',{}),ensure_ascii=False)
                if 'out of memory' in error.lower():error+=' Reduce tiles, ventana temporal o resolución; no se reintentó.'
                raise ValueError(error[-3500:])
            descriptors=[x for v in entry.get('outputs',{}).get('save',{}).values() if isinstance(v,list) for x in v if isinstance(x,dict) and x.get('filename','').endswith('.mp4')]
            if len(descriptors)!=1:raise ValueError('Falta una salida de vídeo única de Tessera.')
            d=descriptors[0];raw=checked(rt/'output',rt/'output'/d.get('subfolder','')/d['filename'])
            if not raw.is_relative_to((rt/'output'/'gen_studio'/ident).resolve()):raise ValueError('Salida no vinculada al trabajo.')
            if not final.exists():
                service.store.update(ident,status='running',stage='Verificando fotogramas y recuperando audio original',progress=None)
                await media.finish(raw,path,final,source['target'],source['start'],count,folder/'media.log',stop,
                    lambda value:service.store.update(ident,progress={'value':min(count,value),'max':count}))
        result=await asyncio.to_thread(media.check_output,final,source['info'],source['target'],count)
        if await asyncio.to_thread(media.digest,path)!=source['sha256']:raise ValueError('El original cambió durante el proceso; no se publica una versión con otra procedencia.')
        if p['operation']=='video_upscale_assembly' and await asyncio.to_thread(media.digest,replacement)!=p['tessera_replacement_sha256']:
            raise ValueError('El tramo cambió durante el montaje; no se publica la copia.')
        if stop():raise asyncio.CancelledError()
        warnings=['El refinamiento generativo puede alterar texto, identidad y detalles en movimiento. Revisa el vídeo completo.'];edges=[]
        if p.get('tessera_segment_index') is not None:
            edges=await asyncio.to_thread(media.continuity,path,final,source['start'],source['count'],source['info']['frames'])
            warnings.append('El tramo tiene una resolución distinta al original. Revisa color, detalle y continuidad antes de incorporarlo.')
        report={**result,'source_sha256':source['sha256'],'version':VERSION,'commit':COMMIT,'processing_seconds':round(time.time()-began,3),
            'parameters':p,'continuity':edges,'warnings':warnings,'native_summary':native_summary,'engine_seconds':engine_seconds,'audio':'Audio original recodificado a AAC' if result['audio_tracks'] else 'Origen sin audio'}
        save(folder/'tessera-report.json',report)
        from .upscale import publish
        await asyncio.to_thread(publish,final,output,result['sha256'])
        from .history_thumbnails import create_preview
        await asyncio.to_thread(create_preview,output,service.root/'thumbnails')
        service.store.update(ident,status='completed',result_available=True,tessera_result_sha256=result['sha256'],tessera_metadata={k:v for k,v in report.items() if k!='parameters'},stage='Nueva versión guardada; el original se conserva',progress=None)
    except asyncio.CancelledError:
        if cancelled(service,ident):service.store.update(ident,status='cancelled',stage='Cancelado',progress=None)
        else:raise
    except Exception as error:
        service.store.update(ident,status='failed',stage='No se pudo completar el upscaling',error=str(error)[:4000],progress=None)
    finally:
        if listener:listener.cancel();await asyncio.gather(listener,return_exceptions=True)
        try:
            if engine_verified and not await busy(service):
                await service.post(base(service),'/free',json={'unload_models':True,'free_memory':True})
                from .tessera_runtime import release_idle
                await release_idle(service)
        except Exception as error:
            service.event(ident,'No se confirmó la descarga de Tessera: '+str(error)[:500])

async def recover(service,job):
    # Accepted submissions are reconciled, never replaced by a fresh POST.
    if job['status']=='submitting' and not job.get('prompt_id'):
        service.store.update(job['id'],status='submission_unknown',error='Reinicio durante envío; confirma el ID antes de reintentar.')
    else:service.store.update(job['id'],status='running' if job.get('prompt_id') else 'queued')

async def request_cancel(service,job):
    if job['status'] not in TERMINAL or job['status']=='submission_unknown':
        if job['status']=='submission_unknown' and not job.get('prompt_id'):raise ValueError('Primero reconcilia el envío incierto; no se interrumpe trabajo sin identificar.')
        if job['status']=='queued' and not job.get('prompt_id'):
            return service.store.update(job['id'],cancel_requested=True,status='cancelled',stage='Cancelado antes de iniciar',progress=None)
        job=service.store.update(job['id'],cancel_requested=True,status='cancelling' if job.get('prompt_id') else job['status'],stage='Cancelación solicitada')
        service.wake.set()
    return job

async def reconcile(service,job):
    """Find only this job's accepted graph; never POST a replacement prompt."""
    from .tessera_runtime import verify
    await verify(service)
    q=await service.get(base(service),'/queue');history=await service.get(base(service),'/history')
    candidates=[]
    for key in ('queue_running','queue_pending'):candidates+=q.get(key,[])
    candidates += [e.get('prompt',[]) for e in history.values()]
    ids={p[1] for p in candidates if len(p)>3 and isinstance(p[3],dict) and p[3].get('gen_studio_job_id')==job['id']}
    if job.get('prompt_id'):ids.add(job['prompt_id'])
    if len(ids)!=1:raise ValueError('No se encontró una ejecución única verificable. No se reenvió ni interrumpió ningún trabajo; consulta el registro del motor.')
    result=service.store.update(job['id'],prompt_id=ids.pop(),status='running',error=None,stage='Reconectando con la ejecución original')
    service.wake.set();return result

def register(app,settings):
    @app.get('/api/upscaling/capabilities')
    async def capabilities():
        service=app.state.jobs;conf=cfg(service);online=False
        try:
            catalog=await service.get(base(service),'/object_info/TesseraRefine');online='TesseraRefine' in catalog
        except httpx.HTTPError:pass
        installed=bool(conf.get('python') and (Path(conf.get('root',''))/'tessera_bootstrap.py').is_file())
        return {'enabled':bool(conf.get('enabled') and (online or installed) and settings.get('generation_enabled')),'online':online,
            'reason':('Upscaling está deshabilitado en esta instalación.' if not conf.get('enabled') or not settings.get('generation_enabled') else '' if online else 'El motor aislado se iniciará al recibir un trabajo.' if installed else 'El motor aislado de Tessera no está disponible.'),
            'profiles':[{'id':k,**v} for k,v in PROFILES.items() if k in conf.get('profiles',['balanced'])],
            'resolutions':conf.get('resolutions',['1080p']),'fps':[24],'max_seconds':conf.get('max_seconds',60),'max_upload_mb':200,
            'version':VERSION,'estimate':None,'limits':f"SDR · 24 fps constantes · duración máxima {conf.get('max_seconds',60)} s. El tiempo depende del contenido y los tiles."}

    @app.get('/api/upscaling/jobs/{job_id}/segments')
    async def list_segments(job_id:UUID):
        service=app.state.jobs;job=service.store.job(str(job_id))
        if not job:raise HTTPException(404,'Toma no encontrada.')
        try:return {'segments':[{k:s[k] for k in ('index','start','keep')} for s in segments(service,job)]}
        except (ValueError,OSError,KeyError) as e:raise HTTPException(409,str(e))

    @app.post('/api/upscaling/jobs',status_code=202)
    async def submit(body:Request):
        service=app.state.jobs;conf=cfg(service)
        if not conf.get('enabled') or not settings.get('generation_enabled'):raise HTTPException(503,'Tessera no está habilitado.')
        if body.profile not in conf.get('profiles',['balanced']) or body.resolution not in conf.get('resolutions',['1080p']):raise HTTPException(422,'Perfil o resolución pendiente de validación.')
        async with service.lock:
            try:
                source=await asyncio.to_thread(resolve,service,body);validate_capacity(service,source)
                from .tessera_runtime import installation
                installed=await asyncio.to_thread(installation,service)
                if source['count']>24*conf.get('max_seconds',60):raise ValueError('La duración supera el límite validado de esta instalación.')
                params={'engine':'tessera','operation':'video_upscale','tessera_version':VERSION,'tessera_source':source,
                    'tessera_installation_sha256':installed,
                    'tessera_request':json.loads(body.model_dump_json(exclude={'request_key'})), 'tessera_profile':body.profile,
                    'tessera_resolution':body.resolution,'tessera_advanced':body.advanced.model_dump(),
                    'tessera_segment_index':body.segment_index,'frames':source['count'],'resolution':f"{source['target']['width']}x{source['target']['height']}"}
                if body.source_kind=='job':params['upscale_parent_id']=str(body.source_id)
                else:params['video_id']=str(body.source_id)
                job=service.store.create_job(str(uuid4()),str(body.request_key),params)
            except (ValueError,OSError,KeyError) as e:raise HTTPException(409,str(e))
            service.wake.set();return job

    @app.get('/api/upscaling/jobs/{job_id}/original')
    async def original(job_id:UUID):
        service=app.state.jobs;job=service.store.job(str(job_id))
        if not is_job(job):raise HTTPException(404,'Trabajo no encontrado.')
        try:
            source=job['parameters']['tessera_source'];path=checked(service.root,source['path'])
            if await asyncio.to_thread(media.digest,path)!=source['sha256']:raise ValueError('Cambió el original.')
            return FileResponse(path,media_type='video/mp4')
        except (ValueError,OSError) as e:raise HTTPException(409,str(e))

    @app.post('/api/upscaling/jobs/{job_id}/assemble',status_code=202)
    async def use_segment(job_id:UUID,body:Assembly):
        service=app.state.jobs
        if not cfg(service).get('enabled') or not settings.get('generation_enabled'):raise HTTPException(503,'Upscaling no está habilitado.')
        async with service.lock:
            job=service.store.job(str(job_id))
            if not is_job(job) or job['status']!='completed' or job['parameters'].get('tessera_segment_index') is None:raise HTTPException(409,'Selecciona un tramo mejorado terminado.')
            p=job['parameters']
            parent=service.store.job(p.get('upscale_parent_id',''))
            if not parent or parent['status']!='completed' or not parent.get('result_available'):raise HTTPException(409,'Espera a que termine el montaje original.')
            try:
                source=await asyncio.to_thread(resolve,service,Request(request_key=body.request_key,**p['tessera_request']))
                validate_capacity(service,{**source,'count':source['info']['frames']})
            except (ValueError,OSError) as e:raise HTTPException(409,str(e))
            if source['count']!=p['tessera_source']['count']:raise HTTPException(409,'Cambió la duración del tramo original.')
            params={**p,'tessera_source':source,'operation':'video_upscale_assembly',
                'tessera_replacement_id':job['id'],'tessera_replacement_sha256':job['tessera_result_sha256'],
                'tessera_segment_index':None,'frames':source['info']['frames']}
            try:result=service.store.create_job(str(uuid4()),str(body.request_key),params)
            except ValueError as e:raise HTTPException(409,str(e))
            service.wake.set();return result

    @app.post('/api/upscaling/jobs/{job_id}/preferred')
    async def preferred(job_id:UUID):
        service=app.state.jobs;job=service.store.job(str(job_id))
        if not is_job(job) or job['status']!='completed' or job['parameters'].get('tessera_segment_index') is not None:raise HTTPException(409,'Solo un vídeo completo terminado puede ser la versión preferida.')
        output=checked(service.root,service.root/'results'/(job['id']+'.mp4'))
        if await asyncio.to_thread(media.digest,output)!=job.get('tessera_result_sha256'):raise HTTPException(409,'La versión cambió; se conserva para diagnóstico.')
        from .studio_results import scene_root
        with service.store.connect() as db:root=scene_root(db,job,{})
        return service.store.update_scene_metadata(root,{'selected_job_id':job['id']})

    @app.get('/api/upscaling/jobs/{job_id}/report')
    async def report(job_id:UUID):
        service=app.state.jobs;job=service.store.job(str(job_id))
        if not is_job(job):raise HTTPException(404,'Trabajo no encontrado.')
        # Public report omits absolute paths and private model details.
        return job.get('tessera_metadata') or {'status':job['status'],'error':job.get('error')}

    @app.post('/api/upscaling/jobs/{job_id}/reconcile')
    async def reconnect(job_id:UUID):
        service=app.state.jobs
        async with service.lock:
            job=service.store.job(str(job_id))
            if not is_job(job) or job['status'] not in {'failed','submission_unknown'}:raise HTTPException(409,'Este trabajo no necesita reconciliación.')
            try:return await reconcile(service,job)
            except (ValueError,httpx.HTTPError) as e:raise HTTPException(409,str(e))

    @app.get('/api/upscaling/jobs/{job_id}/logs')
    async def logs(job_id:UUID):
        service=app.state.jobs;job=service.store.job(str(job_id))
        if not is_job(job):raise HTTPException(404,'Trabajo no encontrado.')
        folder=record(service,job['id']);parts=[job.get('error') or '']
        for name in ('media.log','history.json'):
            path=folder/name
            if path.is_file():parts.append(path.read_text(encoding='utf-8',errors='replace')[-16000:])
        value='\n'.join(parts)
        for path in (str(service.root),str(runtime(service))):value=value.replace(path,'[privado]')
        return {'log':value}
