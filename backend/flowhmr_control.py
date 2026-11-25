"""Owner-only FlowHMR visual-review-approved SECourses/ControlNet research trial."""
import asyncio,json,shutil,subprocess
from copy import deepcopy
from pathlib import Path
from typing import Literal
from uuid import UUID,uuid4,uuid5,NAMESPACE_URL
from fastapi import HTTPException
from pydantic import BaseModel,ConfigDict,Field
from .head import digest
from .job_cancel import checkpoint,thread_checkpoint
from .flowhmr_research import RUNTIME,owner,load_review,read_json,contained

VERSION='flowhmr-control-1'
PARENT='fe021325-eb75-4d9f-b706-a87f927d9ce6'
REVIEW='78ff2933-33d2-487b-a22f-17e46d4317f2'
PREPARATION_ID='3f4ede62-fb02-4ac1-9755-a6725e2b6bfd'
PREPARATION_SHA='d1f77fbda0c8d7deb3a653c45e41d37e611728f5f3352214a501314d0e2afe7f'
ALIGNMENT_SHA='0bd6086f790500f41ced4a82a1990e368d1c45db155d61bea815103f9e066855'
PATCH='minimax_h3_fun_controlnet_union_pruned_bf16.safetensors'
GUARD='GenStudioFlowHMRMaskCheck'
MASK_REPAIR_VERSION='flowhmr-mask-repair-1'
REPAIR='GenStudioFlowHMRMaskRepair'
SOURCE_ID='64790c34-81ce-4fb0-b8d1-bb9e2b73a1fd'
REFERENCE_ID='7c6494b9-f5ba-4b41-91b6-1ea7d22888f3'
INTERNAL_ID=str(uuid5(NAMESPACE_URL,'genstudio-flowhmr-source/'+PREPARATION_ID))

class Request(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_key:UUID
    alignment_approved:Literal[True]
    approved_alignment_sha256:str=Field(pattern='^[0-9a-f]{64}$')
    frames:Literal[120,336]=336
    flowhmr_mask_repair_version:Literal['flowhmr-mask-repair-1']|None=None


def preparation(service):
    if service.settings.get('workspace_id')!='owner':raise ValueError('La prueba pertenece al espacio del propietario.')
    review=load_review(service,PARENT,REVIEW)
    item=next((a for a in review['artifacts'] if a['path']=='hybrid/aligned-joints.npz'),None)
    if not item or item['sha256']!=ALIGNMENT_SHA:raise ValueError('Cambio la alineacion revisada.')
    folder=contained(RUNTIME,RUNTIME/'research-controls'/PREPARATION_ID)
    path=contained(folder,folder/'preparation.json')
    if digest(path)!=PREPARATION_SHA:raise ValueError('Cambio la preparacion de la guia corporal.')
    report=read_json(path)
    for role,kind in (('original','video'),('reference','image')):
        ident=SOURCE_ID if role=='original' else REFERENCE_ID
        asset=service.store.asset(ident)
        if not asset or asset['kind']!=kind:raise ValueError('Las entradas no pertenecen a este espacio.')
        source=contained(service.root/'inputs',service.root/'inputs'/asset['filename'])
        expected=report['inputs'][role]
        if source!=Path(expected['path']).resolve() or digest(source)!=expected['sha256']:
            raise ValueError('Cambio una entrada original de la prueba.')
    if report['inputs']['aligned']['sha256']!=ALIGNMENT_SHA:raise ValueError('La guia no deriva de la alineacion aprobada.')
    for key in ('guide','source_internal'):
        item=report[key];file=contained(folder,Path(item['path']))
        if digest(file)!=item['sha256']:raise ValueError('Cambio la guia o su fuente normalizada.')
    guide=report['guide'];source=report['source_internal']
    if any(guide.get(k)!=v for k,v in {'frames':336,'fps':24,'width':576,'height':832}.items()):raise ValueError('Formato de guia inesperado.')
    if any(source.get(k)!=v for k,v in {'frames':420,'fps':30,'width':576,'height':822}.items()):raise ValueError('Formato de fuente inesperado.')
    boxes=report['boxes_24_source_normalized']
    if len(boxes)!=336:raise ValueError('Falta el seguimiento de un fotograma.')
    for box in boxes:
        if len(box)!=4 or not all(type(x) in (int,float) and 0<=x<=1 for x in box) or not (box[0]<box[2] and box[1]<box[3]):
            raise ValueError('Caja de seguimiento no valida.')
    return report


def binding(report,mask_repair_version=None):
    if mask_repair_version not in (None,MASK_REPAIR_VERSION):raise ValueError('Version de reparacion de mascara desconocida.')
    value=dict(parent_id=PARENT,review_id=REVIEW,review_npz_sha256=ALIGNMENT_SHA,
        preparation_id=PREPARATION_ID,preparation_sha256=PREPARATION_SHA,
        guide_sha256=report['guide']['sha256'],source_original_id=SOURCE_ID,
        source_original_sha256=report['inputs']['original']['sha256'],reference_id=REFERENCE_ID,
        reference_sha256=report['inputs']['reference']['sha256'],normalized_source_id=INTERNAL_ID,
        normalized_source_sha256=report['source_internal']['sha256'],width=576,height=832,frames=336,fps=24,
        body_only=True,control_strength=1.0,source_actor='foreground bearded man in beige T-shirt and dark pants')
    # Historical receipts and Qwen bindings remain exactly unchanged without opt-in.
    if mask_repair_version is not None:value['flowhmr_mask_repair_version']=mask_repair_version
    return value


def ensure_source(service,report):
    from .media import probe
    path=contained(service.root/'inputs',service.root/'inputs'/(INTERNAL_ID+'.mp4'))
    if path.exists():
        if digest(path)!=report['source_internal']['sha256']:raise ValueError('Cambio la fuente interna revisada.')
    else:
        path.parent.mkdir(parents=True,exist_ok=True)
        with Path(report['source_internal']['path']).open('rb') as source,path.open('xb') as output:shutil.copyfileobj(source,output)
    if not service.store.asset(INTERNAL_ID):
        service.store.add_asset(dict(id=INTERNAL_ID,filename=path.name,kind='video',name='Baile · Fuente FlowHMR sincronizada',
            sha256=report['source_internal']['sha256'],bytes=path.stat().st_size,sequence_internal=True,
            original_source_id=SOURCE_ID,flowhmr_preparation_sha256=PREPARATION_SHA,**probe(path)))
    return path


def check_parameters(service,p,report):
    if p.get('flowhmr_control_version')!=VERSION or p.get('flowhmr_control_binding')!=binding(report,p.get('flowhmr_mask_repair_version')):
        raise ValueError('La toma no conserva la guia FlowHMR aceptada.')
    if (p.get('engine')!='h3_secourses' or p.get('operation')!='secourses_swap' or p.get('variant')!='standard'
            or p.get('visual_method') or p.get('background')!='video' or p.get('reference_id')!=REFERENCE_ID
            or p.get('width')!=576 or p.get('height')!=832 or p.get('flowhmr_alignment_approved') is not True):
        raise ValueError('La prueba requiere el perfil corporal revisado y su referencia original.')
    if p.get('sequence_parent_id'):
        parent=service.store.job(p['sequence_parent_id'])
        if not parent or parent['parameters'].get('flowhmr_control_binding')!=p['flowhmr_control_binding']:
            raise ValueError('El tramo no pertenece a la prueba FlowHMR.')
    elif p.get('video_id')!=INTERNAL_ID or p.get('start_frame',0)!=0 or p.get('start_seconds',0)!=0:
        raise ValueError('La prueba debe comenzar en el primer fotograma revisado.')


def window(p,report):
    start=p.get('sequence_start_frame',0)
    keep=p.get('sequence_input_keep',p['frames'])
    count=p['render_frames']
    if any(type(x) is not int for x in (start,keep,count)) or not (0<=start<336 and 5<=keep<=count<=141 and start+keep<=336):
        raise ValueError('El tramo de control excede la secuencia revisada.')
    if count!=5+17*((max(48,keep)-5+16)//17):raise ValueError('La guia no conserva la rejilla temporal nativa H3.')
    boxes=report['boxes_24_source_normalized'][start:start+keep]
    boxes=boxes+[boxes[-1]]*(count-keep)
    return {'start':start,'keep':keep,'frames':count,'boxes':boxes}


async def ffmpeg_window(service,job,args,log_path):
    from .process_cleanup import stop_process_tree
    spawn=process=None
    with log_path.open('xb') as log:
        try:
            spawn=asyncio.create_task(asyncio.create_subprocess_exec(*args,stdout=asyncio.subprocess.DEVNULL,
                stderr=log,stdin=asyncio.subprocess.DEVNULL,creationflags=getattr(__import__('subprocess'),'CREATE_NO_WINDOW',0)))
            process=await asyncio.shield(spawn)
            if await asyncio.wait_for(process.wait(),120):raise ValueError('No se pudo preparar el tramo de control FlowHMR.')
            checkpoint(service,job['id'])
        finally:
            if process is None and spawn is not None:
                from .flowhmr_research import finish_task
                try:process,_=await finish_task(spawn)
                except Exception:process=None
            if process is not None and process.returncode is None:
                from .flowhmr_research import finish_task
                await finish_task(asyncio.create_task(stop_process_tree(process)))


def verify_window(path,count):
    result=subprocess.run(['ffprobe','-v','error','-select_streams','v:0','-count_frames',
        '-show_entries','stream=width,height,avg_frame_rate,nb_read_frames:format=duration',
        '-of','json',str(path)],capture_output=True,text=True,timeout=60,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise ValueError('No se pudo verificar el tramo de control.')
    value=json.loads(result.stdout);stream=value['streams'][0]
    if (stream['width']!=576 or stream['height']!=832 or stream['avg_frame_rate']!='24/1'
            or int(stream['nb_read_frames'])!=count or abs(float(value['format']['duration'])-count/24)>.01):
        raise ValueError('El tramo de guia perdio sus dimensiones, fotogramas o duracion.')


async def prepare(service,job,prepared):
    from .motion_transfer import upload
    from .media import probe
    p=job['parameters'];ident=job['id']
    report=await thread_checkpoint(service,ident,preparation,service)
    await thread_checkpoint(service,ident,check_parameters,service,p,report)
    segment=window(p,report);folder=service.root/'jobs'/ident
    receipt=folder/'flowhmr-control-window.json'
    expected={'version':VERSION,'binding':binding(report,p.get('flowhmr_mask_repair_version')),'start':segment['start'],'keep':segment['keep'],'frames':segment['frames']}
    if receipt.is_file():
        saved=read_json(receipt)
        if saved['input']!=expected or digest(contained(folder,Path(saved['path'])))!=saved['sha256']:
            raise ValueError('Cambio un tramo de control ya preparado.')
    else:
        path=folder/'flowhmr-pose-window.mkv'
        if path.exists():raise ValueError('Existe una preparacion de control incompleta; crea otra toma.')
        vf=f"trim=start_frame={segment['start']}:end_frame={segment['start']+segment['keep']},setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop={segment['frames']-segment['keep']}"
        await ffmpeg_window(service,job,['ffmpeg','-v','error','-nostdin','-n','-i',report['guide']['path'],'-an','-vf',vf,
            '-frames:v',str(segment['frames']),'-c:v','ffv1','-pix_fmt','bgr0',str(path)],folder/'flowhmr-control-prepare.log')
        await thread_checkpoint(service,ident,verify_window,path,segment['frames'])
        saved={'input':expected,'path':str(path),'sha256':await thread_checkpoint(service,ident,digest,path)}
        saved['upload']=await upload(service,path,ident)
        checkpoint(service,ident)
        with receipt.open('x',encoding='utf-8') as stream:json.dump(saved,stream,indent=2)
    return {'flowhmr_control_video':saved['upload'],'flowhmr_control_boxes':json.dumps(segment['boxes'],separators=(',',':')),
            'flowhmr_control_frames':segment['frames']}


def adapt_graph(graph,p,ident):
    if p.get('flowhmr_control_version')!=VERSION:raise ValueError('Version de control corporal desconocida.')
    if p.get('variant')!='standard' or p.get('visual_method') or p.get('background')!='video':raise ValueError('Perfil incompatible con este ensayo.')
    if p['flowhmr_control_frames']!=p['render_frames']:raise ValueError('La guia no conserva todos los fotogramas del tramo.')
    repair_version=p.get('flowhmr_mask_repair_version')
    if repair_version not in (None,MASK_REPAIR_VERSION):raise ValueError('Version de reparacion de mascara desconocida.')
    if repair_version != p.get('flowhmr_control_binding',{}).get('flowhmr_mask_repair_version'):
        raise ValueError('La reparacion de mascara no conserva su vinculacion revisada.')
    g=deepcopy(graph)
    if any(key.startswith('flowhmr-') for key in g):raise ValueError('El control ya fue aplicado.')
    model=g['guider']['inputs']['model']
    if g['schedule']['inputs']['model']!=model:raise ValueError('Guider y scheduler no comparten el modelo esperado.')
    def node(kind,**inputs):return {'class_type':kind,'inputs':inputs}
    g['flowhmr-video']=node('LoadVideo',file=p['flowhmr_control_video'])
    g['flowhmr-components']=node('GetVideoComponents',video=['flowhmr-video',0])
    g['flowhmr-patch']=node('ModelPatchLoader',name=PATCH)
    g['flowhmr-control']=node('MiniMaxH3FunControlNetApply',model=model,model_patch=['flowhmr-patch',0],
        vae=['vae',0],control_video=['flowhmr-components',0],strength=1.0,start_percent=0.0,end_percent=1.0)
    g['guider']['inputs']['model']=['flowhmr-control',0];g['schedule']['inputs']['model']=['flowhmr-control',0]
    images=g['condition']['inputs']['ref_videos.ref_video_0']
    masks=['guide',1]
    if repair_version:
        guide=g.get('guide',{})
        target=g.get('target-mask-check',{})
        if (guide.get('class_type')!='SECoursesH3GenjutsuMotionGuide'
                or target.get('class_type')!='GenStudioSECoursesMaskCheck'
                or images!=['target-mask-check',0]
                or target.get('inputs',{}).get('images')!=['guide',0]
                or target.get('inputs',{}).get('masks')!=['guide',1]):
            raise ValueError('La reparacion requiere la guia nativa y ambas validaciones de mascara.')
        native=guide['inputs']
        g['flowhmr-mask-repair']=node(REPAIR,originalimages=native['images'],masks=['guide',1],
            sam3_model=native['sam3_model'],expected_frames=p['flowhmr_control_frames'],
            expected_boxes=p['flowhmr_control_boxes'],guide_megapixels=native['guide_megapixels'],
            background=native['background'],mask_grow=native['mask_grow'],threshold=native['threshold'],
            refine_iterations=native.get('refine_iterations',2),run_id=ident,segment_index=p.get('sequence_index',0))
        target['inputs']['images']=['flowhmr-mask-repair',0]
        target['inputs']['masks']=['flowhmr-mask-repair',1]
        masks=['flowhmr-mask-repair',1]
    g['flowhmr-mask-check']=node(GUARD,images=images,masks=masks,expected_frames=p['flowhmr_control_frames'],
        expected_boxes=p['flowhmr_control_boxes'],run_id=ident,segment_index=p.get('sequence_index',0))
    g['condition']['inputs']['ref_videos.ref_video_0']=['flowhmr-mask-check',0]
    return g


def register(app,settings):
    @app.post('/api/research/flowhmr/{parent_id}/reviews/{review_id}/generate',status_code=202)
    async def generate(parent_id:UUID,review_id:UUID,body:Request):
        service=owner(app)
        if str(parent_id)!=PARENT or str(review_id)!=REVIEW:raise HTTPException(404,'Esta prueba solo admite la alineacion revisada.')
        if body.approved_alignment_sha256!=ALIGNMENT_SHA:raise HTTPException(409,'Confirma la misma alineacion que revisaste.')
        if not all(settings.get(k) for k in ('generation_enabled','qwen_director_enabled','video_sequences_enabled')):
            raise HTTPException(503,'La prueba requiere generacion, Qwen y tramos activados.')
        request=body.model_dump(mode='json',exclude_none=True)
        async with service.lock:
            with service.store.connect() as db:row=db.execute('SELECT state FROM jobs WHERE request_key=?',(str(body.request_key),)).fetchone()
            if row:
                prior=json.loads(row[0])
                if prior['parameters'].get('flowhmr_trial_request')!=request:raise HTTPException(409,'La clave corresponde a otra solicitud.')
                return service.store.job(prior['id'])
            if service.enhancer_active:raise HTTPException(409,'Espera a que termine el analisis activo.')
            try:
                from .secourses_swap import Request as SECRequest,options,VERSION as SEC_VERSION,REVISION
                from .visual_methods import request_payload
                ready=await options(service)
                if not ready['available']:raise ValueError('Faltan componentes SECourses.')
                required=(GUARD,'MiniMaxH3FunControlNetApply','ModelPatchLoader')
                if body.flowhmr_mask_repair_version:required+=(REPAIR,)
                for kind in required:
                    info=await service.get(settings['comfy_url'],'/object_info/'+kind)
                    if kind not in info:raise ValueError('Falta el componente '+kind)
                    if kind=='ModelPatchLoader' and PATCH not in info[kind]['input']['required']['name'][0]:raise ValueError('Falta el ControlNet H3 instalado.')
                report=await asyncio.to_thread(preparation,service)
                source=await asyncio.to_thread(ensure_source,service,report)
                native=SECRequest(request_key=body.request_key,video_id=UUID(INTERNAL_ID),reference_id=UUID(REFERENCE_ID),
                    variant='standard',background='video',frames=body.frames,megapixels=.5,guide_megapixels=1.,
                    target='bearded man in beige shirt',auto_describe=True,qwen_adaptive_zoom=True,qwen_motion_grounding=True,
                    prompt='Reemplaza unicamente al hombre barbado de camiseta beige y pantalon oscuro del primer plano con Goku de la referencia. Conserva a los demas bailarines y el estudio. Sigue la secuencia completa sin reiniciar los pasos.',seed=42)
                req=request_payload(native)
                p={**req,'engine':'h3_secourses','operation':'secourses_swap','secourses_version':SEC_VERSION,'secourses_request':req,
                    'upstream_revision':REVISION,'width':576,'height':832,'resolution':'576x832','steps':20,'start_frame':0,
                    'render_frames':body.frames+(5-body.frames)%17,'preserve_face':False,
                    'source_sha256':report['source_internal']['sha256'],'reference_sha256':report['inputs']['reference']['sha256'],
                    'flowhmr_control_version':VERSION,'flowhmr_control_binding':binding(report,body.flowhmr_mask_repair_version),'flowhmr_alignment_approved':True,
                    'flowhmr_trial_request':request,'flowhmr_research_only':True}
                if body.flowhmr_mask_repair_version:p['flowhmr_mask_repair_version']=body.flowhmr_mask_repair_version
                check_parameters(service,p,report)
                if not service.store.video_sequences_enabled or not service.store.qwen_director_enabled:
                    raise ValueError('La biblioteca no tiene Qwen y tramos activados.')
                result=service.store.create_job(str(uuid4()),str(body.request_key),p)
                if not result.get('qwen_director_version') or not result.get('video_sequence_version'):
                    service.store.update(result['id'],status='failed',stage='Preparacion incompleta',error='Faltan Qwen o tramos; no se envio al motor.')
                    raise ValueError('La toma no recibio Qwen y continuidad; no se inicia.')
                service.store.update(result['id'],title='FlowHMR · Control corporal · Baile completo' if body.frames==336 else 'FlowHMR · Control corporal · Primeros 5 s')
                service.wake.set();return service.store.job(result['id'])
            except (OSError,ValueError,KeyError,TypeError) as error:raise HTTPException(409,str(error)) from error
