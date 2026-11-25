"""Source-identity suppression on primary Comfy, before the isolated Pika engine.

Uses the already installed SECourses/SAM nodes; never changes their workflows.
The accepted guide has its own receipt, binding, reconciliation and cancellation.
"""
import asyncio,json,math,time,subprocess
from fractions import Fraction
from pathlib import Path
from uuid import uuid4
import httpx
from ..head import digest
from ..job_cancel import checkpoint,thread_checkpoint,entries
from ..pika_h3_flex.runtime import atomic
from .. import qwen_director_store as records
from ..qwen_director import context,record_hash
from ..qwen_global import scoped
from ..secourses_target import validate_target_sam
from .swap_direction import enabled

FIELD='pika_swap_guide'
PHASE='pika-swap-source-guide'
SAM='sam3.1_multiplex_fp16.safetensors'

def pending(job):
    state=job.get(FIELD) or {}
    return bool(state.get('attempted') and not state.get('completed') and not state.get('cancelled'))

def binding(service,job,source):
    state=records.read(service.store,job['id']) or {}
    ctx=context(service,job)
    global_context=scoped(service,job)
    if (not global_context or not state.get('plan',{}).get('ready')
            or state.get('fingerprint')!=ctx['fingerprint']
            or state.get('observations',{}).get('global_context')!=global_context):
        raise ValueError('La guía necesita el análisis Qwen aceptado de este tramo y sus entradas originales.')
    return dict(version='masked-source-1',job_id=job['id'],workspace=service.settings.get('workspace_id','owner'),
        fingerprint=ctx['fingerprint'],revision=state['revision'],plan_sha256=record_hash(state['plan']),
        global_sha256=global_context['sha256'],target=validate_target_sam(global_context['target_sam']),
        prepared_sha256=digest(source),frames=job['parameters']['frames'])

def graph(filename,key,ident):
    def n(kind,**values):return dict(class_type=kind,inputs=values)
    return {
        'video':n('LoadVideo',file=filename),'components':n('GetVideoComponents',video=['video',0]),
        'sam':n('CheckpointLoaderSimple',ckpt_name=SAM),
        'guide':n('SECoursesH3GenjutsuMotionGuide',images=['components',0],sam3_model=['sam',0],sam3_clip=['sam',1],
            target=key['target'],background='video (character replacement)',guide_megapixels=.4,threshold=.5,
            mask_grow=0,refine_iterations=2,width=480,height=832,match_source_aspect=False),
        'check':n('GenStudioSECoursesMaskCheck',images=['guide',0],masks=['guide',1],expected=['guide',2],
            target=key['target'],run_id=ident,context_sha256=key['global_sha256']),
        # Guide shape follows source aspect; even dimensions are required by H264.
        'even-size':n('ImageScale',image=['check',0],upscale_method='area',width=480,height=832,crop='disabled'),
        'guide-video':n('CreateVideo',images=['even-size',0],fps=24),
        'save':n('SaveVideo',video=['guide-video',0],filename_prefix='gen-studio-pika-swap-guide/'+ident,format='mp4',codec='h264'),
        'mask-preview':n('SaveImage',images=['guide',6],filename_prefix='gen-studio-pika-swap-guide/'+ident+'-mask')}

def video_info(path):
    result=subprocess.run(['ffprobe','-v','error','-select_streams','v:0','-show_entries',
        'stream=width,height,nb_frames,r_frame_rate','-of','json',str(path)],capture_output=True,text=True,timeout=60,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise ValueError('No se pudo verificar el video de guia.')
    stream=json.loads(result.stdout)['streams'][0]
    return {'width':stream['width'],'height':stream['height'],'frames':int(stream['nb_frames']),
        'fps':float(Fraction(stream['r_frame_rate']))}

def validate_report(report,key):
    values=report.get('coverage_fraction',[])
    if (report.get('context_sha256')!=key['global_sha256'] or report.get('target')!=key['target']
            or report.get('frames')!=key['frames'] or len(values)!=key['frames']
            or not all(isinstance(x,(int,float)) and math.isfinite(x) and 0<=x<=1 for x in values)
            or not any(x>0 for x in values)):
        raise ValueError('La máscara no detectó al personaje correcto en este tramo; revisa la selección con Qwen.')
    # Partial occlusion is legitimate; do not require every frame to be nonempty.

async def reconcile(service,job,state):
    base=service.settings['comfy_url'];queue=await service.get(base,'/queue')
    history=await service.get(base,'/history');matches=set()
    for row in [*entries(queue),*(v.get('prompt',[]) for v in history.values())]:
        if not isinstance(row,(list,tuple)) or len(row)<4:continue
        extra=row[3]
        if (isinstance(extra,dict) and extra.get('client_id')==state['client_id']
                and extra.get('gen_studio_pika_guide_job')==job['id']
                and extra.get('workspace')==state['binding']['workspace']
                and extra.get('graph_sha256')==state['graph_sha256']
                and record_hash(row[2])==state['graph_sha256']):matches.add(row[1])
    if len(matches)!=1:raise ValueError('Envío de guía sin confirmar. Se conserva su identificador privado y no se reenvía automáticamente.')
    return matches.pop()

async def idle_others(service,own=None):
    primary=service.settings['comfy_url']
    for base in dict.fromkeys([primary,*service.settings.get('pika_external_comfy_urls',['http://127.0.0.1:8199','http://127.0.0.1:8190'])]):
        try:queue=await service.get(base,'/queue')
        except httpx.ConnectError:
            if base==primary:raise
            continue
        if any(base!=primary or row[1]!=own for row in entries(queue)):
            raise ValueError('Otro trabajo necesita la GPU; se detuvo únicamente la guía de esta toma.')
    if (await service.get(service.settings['ollama_url'],'/api/ps')).get('models'):
        raise ValueError('Qwen debe liberar memoria antes de preparar la guía.')

async def free_if_idle(service):
    base=service.settings['comfy_url']
    if not entries(await service.get(base,'/queue')):
        await service.post(base,'/free',json={'unload_models':True,'free_memory':True})

async def cancel(service,job):
    state=job.get(FIELD) or {}
    if not pending(job):return
    pid=state.get('prompt_id') or await reconcile(service,job,state)
    base=service.settings['comfy_url']
    # Atomic prompt-specific cancellation on the installed primary Comfy API.
    await service.post(base,'/api/jobs/'+pid+'/cancel')
    for _ in range(60):
        queue=await service.get(base,'/queue')
        if not any(row[1]==pid for row in entries(queue)):
            service.store.update(job['id'],**{FIELD:{**state,'prompt_id':pid,'cancelled':True}},prompt_id=None)
            await free_if_idle(service);return
        await asyncio.sleep(.5)
    raise ValueError('No se confirmó la cancelación de la guía; conserva esta toma para recuperar su estado.')

async def download(service,job,entry,node,suffix,target):
    items=[i for values in entry.get('outputs',{}).get(node,{}).values() if isinstance(values,list)
        for i in values if isinstance(i,dict) and str(i.get('filename','')).endswith(suffix)]
    if len(items)!=1:raise ValueError('Falta una salida única de la guía: '+node)
    part=target.with_suffix('.part');base=service.settings['comfy_url'];item=items[0];size=0
    async with service.client.stream('GET',base+'/view',params={k:item[k] for k in ('filename','subfolder','type')},timeout=120) as response:
        response.raise_for_status()
        with part.open('wb') as out:
            async for chunk in response.aiter_bytes():
                checkpoint(service,job['id']);size+=len(chunk)
                if size>200*1024**2:raise ValueError('La salida de la guía supera el límite del tramo.')
                out.write(chunk)
    if target.exists() and digest(target)!=digest(part):raise ValueError('Cambió una salida aceptada de la guía.')
    part.replace(target)

async def prepare(service,job,source):
    if not enabled(job['parameters']):return source
    ident=job['id'];base=service.settings['comfy_url'];folder=service.root/'jobs'/ident
    target=folder/'pika-swap-guide.mp4';report_path=folder/'pika-swap-mask.json'
    key=await thread_checkpoint(service,ident,binding,service,job,source)
    state=service.store.job(ident).get(FIELD)
    if state and state['binding']!=key:raise ValueError('Cambió la selección, referencia, intervalo o revisión de la guía aceptada.')
    if state and state.get('cancelled'):raise ValueError('Esta guía fue cancelada; crea una toma nueva.')
    if state and state.get('completed'):
        if digest(target)!=state['video_sha256'] or digest(report_path)!=state['mask_sha256']:
            raise ValueError('Cambió la guía guardada; no se regenerará silenciosamente.')
        return target
    path=folder/'pika-swap-guide-workflow.json'
    if not state:
        await idle_others(service)
        with Path(source).open('rb') as file:
            item=await service.post(base,'/upload/image',files={'image':(Path(source).name,file)},
                data={'overwrite':'false','subfolder':'gen-studio-pika-swap-guide/'+ident},timeout=120)
        filename='/'.join(x for x in (item.get('subfolder',''),item['name']) if x)
        workflow=graph(filename,key,ident)
        from .profiles import dimensions
        w,h=dimensions(job['parameters'])
        # Bound reference token budget to the proven 0.4 MP area for both profiles.
        scale=(480*832/(w*h))**.5
        workflow['even-size']['inputs'].update(width=max(2,round(w*scale/2)*2),height=max(2,round(h*scale/2)*2))
        info=await service.get(base,'/object_info')
        if not {n['class_type'] for n in workflow.values()}<=info.keys():raise ValueError('Faltan los nodos locales SAM/SECourses para Object Swap Pika.')
        checkpoints=info['CheckpointLoaderSimple']['input']['required']['ckpt_name'][0]
        if SAM not in checkpoints:raise ValueError('Falta el modelo SAM3 local para aislar al personaje.')
        atomic(path,workflow)
        state=dict(binding=key,graph_sha256=record_hash(workflow),client_id=str(uuid4()),attempted=True)
        checkpoint(service,ident)
        service.store.update(ident,status='submitting',guided_phase=PHASE,stage='Separando al personaje del escenario',**{FIELD:state})
        try:
            result=await service.post(base,'/prompt',json={'prompt':workflow,'client_id':state['client_id'],
                'extra_data':{'gen_studio_pika_guide_job':ident,'workspace':key['workspace'],'graph_sha256':state['graph_sha256']}},timeout=60)
            pid=result['prompt_id']
        except Exception:
            try:pid=await reconcile(service,job,state)
            except Exception as error:
                service.store.update(ident,status='submission_unknown',error=str(error));return None
        state={**state,'prompt_id':pid}
        service.store.update(ident,**{FIELD:state},prompt_id=pid,status='running')
    else:
        if record_hash(json.loads(path.read_text()))!=state['graph_sha256']:raise ValueError('Cambió el workflow de guía aceptado.')
        pid=state.get('prompt_id') or await reconcile(service,job,state)
        state={**state,'prompt_id':pid};service.store.update(ident,**{FIELD:state},prompt_id=pid,status='running')
    started=time.monotonic();missing=0
    while True:
        checkpoint(service,ident);await idle_others(service,pid)
        history=await service.get(base,'/history/'+pid)
        if pid in history:
            entry=history[pid];atomic(folder/'pika-swap-guide-history.json',entry)
            if entry.get('status',{}).get('status_str')!='success':raise ValueError('No se pudo aislar al personaje; revisa la selección de Qwen y la máscara guardada.')
            await download(service,job,entry,'check','.json',report_path)
            report=json.loads(report_path.read_text());validate_report(report,key)
            await download(service,job,entry,'save','.mp4',target)
            media=await thread_checkpoint(service,ident,video_info,target)
            saved_graph=json.loads(path.read_text());dims=saved_graph['even-size']['inputs']
            if ((media['width'],media['height'])!=(dims['width'],dims['height'])
                    or media['frames']!=key['frames'] or abs(media['fps']-24)>.001):
                raise ValueError('La guía no conserva el intervalo o las dimensiones aceptadas.')
            state={**state,'completed':True,'video_sha256':digest(target),'mask_sha256':digest(report_path)}
            checkpoint(service,ident)
            service.store.update(ident,**{FIELD:state},prompt_id=None,status='preparing',stage='Guía lista · Liberando SAM para Pika')
            await free_if_idle(service);return target
        queue=await service.get(base,'/queue');missing=0 if any(r[1]==pid for r in entries(queue)) else missing+1
        if missing>=3:raise ValueError('El motor perdió la guía aceptada; no se reenviará automáticamente.')
        service.record_engine_contact(ident,'running')
        if time.monotonic()-started>900:raise ValueError('La preparación de la guía superó 15 minutos.')
        await asyncio.sleep(2)
