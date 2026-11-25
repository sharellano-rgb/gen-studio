"""Experimental, resumable character-then-environment motion editing (one shot, up to 10 s)."""
import asyncio,json,math
from pathlib import Path
from typing import Literal
from uuid import UUID,uuid4
import httpx
from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel,ConfigDict,Field
from .head import digest
from .guided_composition import local_path
from .guided_head import phase
from .motion_transfer import cpu,upload
from .motion_bunny import dimensions,frame_count,checked_assets
from .motion_hybrid import verified_copy
from .prompt_language import save,sha
from .workflows import check_graph
from .motion_layers_graphs import MAX_FRAMES

VERSION='motion-layers-v1'
class RenderRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_key:UUID
    video_id:UUID
    reference_id:UUID
    environment_reference_id:UUID|None=None
    frames:int=Field(default=120,ge=5,le=1440,strict=True)
    start_seconds:float=Field(default=0,ge=0,allow_inf_nan=False)
    quality:Literal['balanced','high']='balanced'
    character_direction:str=Field(default='',max_length=1000)
    scene_description:str=Field(default='',max_length=1200)
    interaction_description:str=Field(default='',max_length=1000)
    x:float=Field(ge=0,le=1,allow_inf_nan=False,strict=True)
    y:float=Field(ge=0,le=1,allow_inf_nan=False,strict=True)
    seed:int=Field(default=42,ge=0,le=9007199254740991,strict=True)
    reuse_character_job_id:UUID|None=None

CHARACTER_FIELDS=('layers_version','video_id','video_sha256','reference_id','reference_sha256',
    'frames','start_seconds','quality','width','height','render_frames','character_direction','x','y','seed')
def characteristic_key(p):
    return sha(json.dumps({k:p[k] for k in CHARACTER_FIELDS},sort_keys=True,separators=(',',':')))

def bound_save(path,value):
    if path.exists():
        if json.loads(path.read_text(encoding='utf-8'))!=value:raise ValueError('Cambió un registro de esta generación; crea otra copia.')
    else:save(path,value)


def character_receipt(service,job):
    if not job or job['parameters'].get('operation')!='motion_layers' or not job.get('layers_character_ready'):
        raise ValueError('La primera pasada todavía no está disponible.')
    folder=local_path(service.root,service.root/'jobs'/job['id']);path=folder/'layers-character-receipt.json'
    saved=json.loads(path.read_text(encoding='utf-8'))
    if saved.get('version')!=VERSION or saved.get('character_key')!=characteristic_key(job['parameters']):
        raise ValueError('El vídeo intermedio no corresponde a esta toma.')
    for key,name in [('video','layers-character.mp4'),('padded_video','layers-character-model.mp4')]:
        expected=local_path(folder,folder/name)
        if local_path(folder,saved[key])!=expected or not expected.is_file() or digest(expected)!=saved[key+'_sha256']:
            raise ValueError('Cambió un archivo de la primera pasada.')
    if saved['video_sha256']!=job.get('layers_character_sha256'):raise ValueError('Cambió la copia intermedia registrada.')
    return saved


def reusable(service,prior,p):
    if not prior or prior['parameters'].get('operation')!='motion_layers':raise ValueError('La primera pasada no está en este espacio.')
    if characteristic_key(prior['parameters'])!=characteristic_key(p):
        raise ValueError('Para repetir sólo el escenario conserva vídeo, personaje, intervalo, calidad y selección originales.')
    return character_receipt(service,prior)


def character_graph(p,names,ident,prompt):
    from .direct_character import graph
    from .motion_identity import RECIPE
    # Full-reference v1.5, with the established style-preserving adaptation.
    # Internal graph metadata only: this job has no hybrid parent or child jobs.
    g=graph({**p,'frames':p['render_frames'],'prompt':prompt,'direct_preset':'hands',
        'direct_character_version':'v1.5','direct_vsa':False,'hybrid_identity_recipe':RECIPE,'hybrid_parent_id':ident},names['video'],[names['reference']],ident)
    g['28']['inputs']['filename_prefix']=f'gen_studio/{ident}/layers-character'
    g['27']['inputs'].pop('audio',None);g.pop('26',None)
    return g


def mask_params(p):
    # The click is measured on the displayed source; fit_filter may add a narrow border.
    w,h=p['width'],p['height'];sw,sh=p['source_width'],p['source_height']
    scale=min(w/sw,h/sh);iw=max(2,2*math.floor(sw*scale/2));ih=max(2,2*math.floor(sh*scale/2))
    return {**p,'recipe':'w2','mode':'body','x':((w-iw)/2+p['x']*iw)/w,'y':((h-ih)/2+p['y']*ih)/h}

async def uploaded(service,job,folder,name,paths):
    receipt=folder/(name+'-uploads.json');signatures={k:digest(v) for k,v in paths.items()}
    if receipt.is_file():
        saved=json.loads(receipt.read_text(encoding='utf-8'))
        if saved['files']!=signatures:raise ValueError('Cambió una entrada entregada al motor.')
        return saved['names']
    names={}
    for key,path in paths.items():names[key]=await upload(service,path,job['id'])
    save(receipt,{'files':signatures,'names':names});return names

async def publish_character(service,job,video,model_video,video_sha,model_sha):
    from .job_cancel import thread_checkpoint
    folder=service.root/'jobs'/job['id'];target=folder/'layers-character.mp4';padded=folder/'layers-character-model.mp4'
    await thread_checkpoint(service,job['id'],verified_copy,video,target,video_sha)
    await thread_checkpoint(service,job['id'],verified_copy,model_video,padded,model_sha)
    record={'version':VERSION,'character_key':characteristic_key(job['parameters']),
        'video':str(target),'video_sha256':video_sha,'padded_video':str(padded),'padded_video_sha256':model_sha}
    bound_save(folder/'layers-character-receipt.json',record)
    service.store.update(job['id'],layers_character_ready=True,layers_character_sha256=video_sha)
    return record

async def process(service,job):
    from .job_cancel import checkpoint,thread_checkpoint
    from .motion_layers_prompts import english_fields,character_prompt,scene_prompt
    from .motion_layers_graphs import track_graph,scene_graph
    from .ninja_swap import continuous_shot,fetch_artifact
    from .character_lora import verify
    p=job['parameters'];ident=job['id'];folder=service.root/'jobs'/ident;folder.mkdir(parents=True,exist_ok=True)
    if service.store.job(ident)['status']=='submission_unknown':return
    if p.get('layers_version')!=VERSION:raise ValueError('Versión de Personaje + Escenario no compatible.')
    checkpoint(service,ident);paths=await asyncio.to_thread(checked_assets,service,p)
    bound_save(folder/'layers-plan.json',p)
    service.store.update(ident,stage='Preparando una toma continua',layers_stage='Preparando toma')
    ready=await cpu(service,job,'layers_prepare',{'source':str(paths['video']),'source_sha256':p['video_sha256'],
        'start_frame':round(p['start_seconds']*24),'frames':p['frames'],'width':p['width'],'height':p['height']})
    try:
        await asyncio.to_thread(continuous_shot,folder,{'video':ready['source'],'files':ready['files']},p,service.settings.get('face_python'))
    except ValueError as error:
        if 'contiene cortes' in str(error):raise ValueError('Esta prueba de Personaje + Escenario requiere una toma continua sin cortes.') from error
        raise
    checkpoint(service,ident)
    # A supplied scene image is described by Qwen; all free text is audited in English before H3.
    english=await english_fields(service,job,folder,paths.get('environment_reference'))
    prompts={'character':character_prompt(p,english),'scene':scene_prompt(p,english)}
    bound_save(folder/'layers-prompts.json',prompts)
    service.store.update(ident,layers_effective_prompts=prompts,layers_scene_description=english.get('scene_observation',''))
    checkpoint(service,ident)
    current=service.store.job(ident)
    if current.get('layers_character_ready'):
        intermediate=await asyncio.to_thread(character_receipt,service,current)
    elif p.get('reuse_character_job_id'):
        previous=service.store.job(p['reuse_character_job_id']);saved=await asyncio.to_thread(reusable,service,previous,p)
        intermediate=await publish_character(service,job,saved['video'],saved['padded_video'],saved['video_sha256'],saved['padded_video_sha256'])
        service.store.update(ident,layers_character_reused_from=previous['id'])
    else:
        await asyncio.to_thread(verify,'v1.5')
        service.store.update(ident,layers_stage='1 de 2 · Reemplazando personaje')
        names=await uploaded(service,job,folder,'character',{'video':ready['video'],'reference':paths['reference']})
        result=await phase(service,job,'layers-identity',character_graph(p,names,ident,prompts['character']),'28','.mp4')
        if result is None:return
        checkpoint(service,ident)
        final=await cpu(service,job,'layers_finalize',{'video':result['path'],'video_sha256':result['sha256'],
            'source':str(paths['video']),'source_sha256':p['video_sha256'],'start_frame':round(p['start_seconds']*24),
            'frames':p['frames'],'render_frames':p['render_frames'],'width':p['width'],'height':p['height'],'keep_model_video':True},'layers-character-finalize')
        intermediate=await publish_character(service,job,final['video'],final['model_video'],final['files'][final['video']],final['files'][final['model_video']])
    checkpoint(service,ident)
    service.store.update(ident,layers_stage='Protegiendo al personaje en movimiento',stage='Siguiendo la silueta del personaje')
    scene_paths={'video':Path(intermediate['padded_video']),'reference':paths['reference']}
    if paths.get('environment_reference'):scene_paths['environment']=paths['environment_reference']
    names=await uploaded(service,job,folder,'scene',scene_paths)
    tracking=await phase(service,job,'layers-track',track_graph(mask_params(p),names['video'],ident),'save-mask','.json')
    if tracking is None:return
    audit=json.loads(Path(tracking['path']).read_text(encoding='utf-8'))
    if (audit['frames'],audit['width'],audit['height'])!=(p['render_frames'],p['width'],p['height']):raise ValueError('La máscara no corresponde al vídeo intermedio.')
    mask=folder/'layers-raw-mask.npz';await fetch_artifact(service,audit['bundle'],mask,audit['sha256'])
    service.store.update(ident,layers_raw_mask_sha256=audit['sha256'])
    protected=await cpu(service,job,'layers_mask',{'mask':str(mask),'sha256':audit['sha256'],'frames':p['render_frames'],
        'width':p['width'],'height':p['height'],'video':intermediate['padded_video'],'video_sha256':intermediate['padded_video_sha256'],'contact_fraction':0})
    if protected.get('preview'):
        target=folder/'layers-mask-preview.mp4';await thread_checkpoint(service,ident,verified_copy,protected['preview'],target,protected['files'][protected['preview']])
        service.store.update(ident,layers_mask_ready=True,layers_mask_preview_sha256=digest(target))
    service.store.update(ident,layers_mask_validation=protected.get('stats',{}))
    checkpoint(service,ident)
    stats=await service.get(service.settings['comfy_url'],'/system_stats');argv=stats['system']['argv']
    if '--input-directory' not in argv:raise ValueError('El motor no declara su carpeta de entrada para la máscara.')
    input_root=Path(argv[argv.index('--input-directory')+1]).resolve();relative=f'gen_studio/{ident}/layers-protection.npz'
    bundle=protected['bundle'];destination=local_path(input_root,input_root/relative);destination.parent.mkdir(parents=True,exist_ok=True)
    await thread_checkpoint(service,ident,verified_copy,bundle['path'],destination,bundle['sha256'])
    service.store.update(ident,layers_stage='2 de 2 · Escenario e interacciones',stage='Generando escenario con el personaje protegido')
    g=scene_graph({**p,'scene_prompt':prompts['scene']},names['video'],names['reference'],names.get('environment'),ident,{'path':relative,'sha256':bundle['sha256'],'frames':p['render_frames'],'width':p['width'],'height':p['height']})
    result=await phase(service,job,'layers-environment',g,'save','.mp4')
    if result is None:return
    checkpoint(service,ident);service.store.update(ident,layers_stage='Validando vídeo y audio',stage='Recuperando el audio original')
    final=await cpu(service,job,'layers_finalize',{'video':result['path'],'video_sha256':result['sha256'],
        'source':str(paths['video']),'source_sha256':p['video_sha256'],'start_frame':round(p['start_seconds']*24),
        'frames':p['frames'],'render_frames':p['render_frames'],'width':p['width'],'height':p['height'],'keep_model_video':False},'layers-scene-finalize')
    await asyncio.to_thread(checked_assets,service,p);await asyncio.to_thread(character_receipt,service,service.store.job(ident))
    target=service.root/'results'/(ident+'.mp4');target.parent.mkdir(exist_ok=True)
    await thread_checkpoint(service,ident,verified_copy,final['video'],target,final['files'][final['video']])
    service.store.update(ident,status='completed',stage='Personaje + Escenario · Listo para revisar',layers_stage='Dos etapas completadas',
        result_available=True,progress=None,scene_resolution=p['resolution'],motion_result_sha256=digest(target),motion_audio=final.get('audio'),
        layers_review_required=True)


def register(app,settings):
    @app.get('/api/workflows/motion-layers/preflight')
    async def preflight():
        from .motion_layers_graphs import track_graph,scene_graph
        p={'frames':120,'render_frames':124,'width':544,'height':960,'source_width':544,'source_height':960,
            'resolution':'544x960','seed':42,'x':.5,'y':.5,'scene_prompt':'Keep the performer in <Video 1> with identity from <Picture 1>. Use the setting in <Picture 2>.'}
        ident='00000000-0000-4000-8000-000000000001'
        try:
            checks=[]
            graphs=[character_graph(p,{'video':'source.mp4','reference':'reference.png'},ident,'Replace the performer in <Video 1> using <Picture 1>.'),
                track_graph(mask_params(p),'source.mp4',ident),
                scene_graph(p,'source.mp4','reference.png','scene.png',ident,{'path':'gen_studio/preflight/mask.npz','sha256':'0'*64,'frames':124,'width':544,'height':960})]
            for graph in graphs:
                skip={(k,n) for k,v in graph.items() for n in ('file','image','bundle') if n in v['inputs']}
                checks.append(await check_graph(app.state.jobs.client,settings['comfy_url'],graph,skip_inputs=skip))
            errors=[e for c in checks for e in c['errors']]
            return {'ready':not errors,'ok':not errors,'errors':errors,'version':VERSION,'max_frames':MAX_FRAMES,'fps':24,'experimental':True}
        except (ValueError,OSError,httpx.HTTPError) as error:
            return {'ready':False,'ok':False,'errors':[str(error)],'version':VERSION,'max_frames':MAX_FRAMES,'fps':24}

    @app.post('/api/motion-layers/render',status_code=202)
    async def render(body:RenderRequest):
        if not settings['generation_enabled']:raise HTTPException(503,'Generación desactivada.')
        service=app.state.jobs;p=body.model_dump(mode='json');key=p.pop('request_key')
        try:
            async with service.lock:
                with service.store.connect() as db:existing=db.execute('SELECT payload,state FROM jobs WHERE request_key=?',(key,)).fetchone()
                if existing:
                    prior=json.loads(existing[0])
                    if prior.get('operation')!='motion_layers' or any(prior.get(k)!=v for k,v in p.items()):
                        raise HTTPException(409,'Esta petición ya se usó con otros parámetros.')
                    await asyncio.to_thread(checked_assets,service,prior)
                    return service.store.job(json.loads(existing[1])['id'])
                if p['frames']>MAX_FRAMES and not settings.get('video_sequences_enabled'):raise ValueError('Activa los tramos para vídeos de más de 10 segundos.')
                if not p['scene_description'].strip() and not p['environment_reference_id']:raise ValueError('Describe el nuevo escenario o carga su imagen de referencia.')
                for field in ('character_direction','scene_description','interaction_description'):
                    if '<' in p[field] or '>' in p[field]:raise ValueError('Describe los cambios sin etiquetas de referencias.')
                video=service.store.asset(p['video_id'])
                if not video or video['kind']!='video':raise ValueError('Selecciona un vídeo de este espacio.')
                if round(p['start_seconds']*24)+p['frames']>round(video['duration']*24):raise ValueError('El intervalo supera el final del vídeo.')
                for role,kind in [('video','video'),('reference','image'),('environment_reference','image')]:
                    if not p.get(role+'_id'):continue
                    a=service.store.asset(p[role+'_id'])
                    if not a or a['kind']!=kind:raise ValueError('Selecciona entradas válidas de este espacio.')
                    actual=await asyncio.to_thread(digest,local_path(service.root/'inputs',service.root/'inputs'/a['filename']))
                    if a.get('sha256') and actual!=a['sha256']:raise ValueError('Cambió un archivo cargado; vuelve a cargarlo.')
                    p[role+'_sha256']=actual
                sw,sh=video.get('display_width',video['width']),video.get('display_height',video['height'])
                (w,h),_=dimensions(sw,sh,p['quality'])
                if max(w,h)>2048:raise ValueError('Las proporciones del vídeo superan el límite de esta prueba.')
                p.update(engine='viggle_motion',operation='motion_layers',transfer_method='layers',layers_version=VERSION,
                    width=w,height=h,source_width=sw,source_height=sh,resolution=f'{w}x{h}',base_resolution=f'{w}x{h}',
                    render_frames=frame_count(p['frames']),steps=16,source_sha256=p['video_sha256'])
                await asyncio.to_thread(checked_assets,service,p)
                if p.get('reuse_character_job_id'):
                    try:await asyncio.to_thread(reusable,service,service.store.job(p['reuse_character_job_id']),p)
                    except (ValueError,OSError,KeyError) as error:raise HTTPException(409,str(error)) from error
                job=service.store.create_job(str(uuid4()),key,p)
        except (ValueError,OSError) as error:raise HTTPException(400,str(error)) from error
        service.wake.set();return job

    def own_job(ident):
        service=app.state.jobs;j=service.store.job(str(ident))
        if not j or j['parameters'].get('operation')!='motion_layers':raise HTTPException(404,'Trabajo no encontrado.')
        return service,j

    @app.get('/api/jobs/{job_id}/layers-inputs')
    async def inputs(job_id:UUID):
        service,j=own_job(job_id);result={k:service.store.asset(j['parameters'].get(role+'_id') or '')
            for k,role in [('video','video'),('reference','reference'),('environment','environment_reference')]}
        return {**result,'assets':[a for a in result.values() if a]}

    @app.get('/api/jobs/{job_id}/layers-intermediate')
    async def intermediate(job_id:UUID,download:bool=False):
        service,j=own_job(job_id)
        if not j.get('layers_character_ready'):raise HTTPException(404,'La primera pasada todavía no está disponible.')
        try:saved=await asyncio.to_thread(character_receipt,service,j)
        except (ValueError,OSError,KeyError) as error:raise HTTPException(409,str(error)) from error
        return FileResponse(saved['video'],media_type='video/mp4',filename=f'GEN-STUDIO-{job_id}-personaje.mp4' if download else None)

    @app.get('/api/jobs/{job_id}/layers-mask-preview')
    async def mask_preview(job_id:UUID):
        service,j=own_job(job_id);path=service.root/'jobs'/j['id']/'layers-mask-preview.mp4'
        if not j.get('layers_mask_ready') or not path.is_file():raise HTTPException(404,'La máscara todavía no está disponible.')
        path=local_path(service.root/'jobs'/j['id'],path)
        if await asyncio.to_thread(digest,path)!=j.get('layers_mask_preview_sha256'):raise HTTPException(409,'Cambió la vista de protección.')
        return FileResponse(path,media_type='video/mp4')
