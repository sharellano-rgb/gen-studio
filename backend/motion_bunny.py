"""Bunny native reference-video motion transfer, with two sampling passes."""
import asyncio,json,math
from pathlib import Path
from uuid import UUID,uuid4
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel,Field
from .workflows import ROOT,check_graph
from .head import digest
from .guided_composition import local_path
from .guided_head import phase
from .motion_transfer import cpu,upload

from .bunny_segments import VERSION, TAIL_CONTEXT_VERSION, LEGACY_VERSION, CONTINUITY, MAX_FRAMES, CHUNK_FRAMES
class RenderRequest(BaseModel):
    request_key:UUID
    method:Literal['bunny','tara']='bunny'
    video_id:UUID
    reference_id:UUID
    environment_reference_id:UUID|None=None
    frames:int=Field(default=120,ge=5,le=MAX_FRAMES,strict=True)
    start_seconds:float=Field(default=0,ge=0,allow_inf_nan=False)
    quality:Literal['balanced','high']='balanced'
    scene_mode:Literal['reference','source']='reference'
    direction:str=Field(default='',max_length=1600)
    seed:int=Field(default=42,ge=0,le=9007199254740991)

def dimensions(width,height,quality='balanced'):
    if not width or not height:raise ValueError('El vídeo no tiene dimensiones válidas.')
    area=(960*544 if quality=='balanced' else 1152*640)
    w=max(32,32*round(math.sqrt(area*width/height)/32));h=max(32,32*round(math.sqrt(area*height/width)/32))
    return (w,h),(round(w*1.5/32)*32,round(h*1.5/32)*32)

def frame_count(frames):return 5+17*math.ceil((frames-5)/17)

def checked_assets(service,p):
    result={}
    for role,kind in [('video','video'),('reference','image'),('environment_reference','image')]:
        ident=p.get(role+'_id')
        if not ident:continue
        asset=service.store.asset(ident)
        if not asset or asset['kind']!=kind:raise ValueError('Una entrada no está disponible en este espacio.')
        path=local_path(service.root/'inputs',service.root/'inputs'/asset['filename'])
        if digest(path)!=p[role+'_sha256']:raise ValueError('Cambió una entrada de la toma. Crea otra copia.')
        result[role]=path
    return result

def prompt(p,direction):
    scene=('Keep the complete original environment, props, lighting and other people from <Video 1>.' if p['scene_mode']=='source' else
           'Replace the entire source environment with the setting, visual style, lighting and props from '+('<Picture 2>.' if p.get('environment_reference_id') else '<Picture 1>.'))
    return ('subject_definitions:\n<Subject 1> is the character from <Picture 1>, replacing the main foreground performer in <Video 1>. Preserve the exact face, distinctive hair, anatomy, outfit, colors and accessories in every view.\n<Video 1> supplies the full body performance, head orientation, gaze, timing and camera movement.\n\nsummary:\n[video editing + reference generation] Transfer the source performance to <Subject 1>. '+scene+'\n\nretention_analysis:\n<Subject 1>: fully_preserved - identity, clothing and visual style from <Picture 1>.\n<Video 1>: attribute_transfer - exact body and head motion, camera framing, rhythm and complete final action. Images define appearance; the video defines movement.\n\ndetailed_description:\nFollow the source performance frame by frame from beginning to end. Preserve head tilts, profile and back views, hands, feet, object contacts, scale and screen position. Do not rotate the head merely to reveal the reference face. Avoid added actions, pauses or repeated opening motions. Keep supporting objects compact and stable. '+scene+ ('\nAdditional direction: '+direction if direction else '')+'\n\noverall_soundscape:\nVideo-only generation. The original source audio is retained separately.\n\nnon_diegetic_music:\nN/A.')

def graph(p,names,ident,direction=''):
    g=json.loads((ROOT/'workflows/motion-bunny.api.json').read_text(encoding='utf-8'))
    g['video']['inputs']['file']=names['video'];g['picture']['inputs']['image']=names['reference']
    w,h=map(int,p['base_resolution'].split('x'))
    g['condition']['inputs'].update(prompt=prompt(p,direction),width=w,height=h,length=frame_count(p['frames']))
    if names.get('environment_reference'):
        g['environment']={'class_type':'LoadImage','inputs':{'image':names['environment_reference']}}
        g['condition']['inputs']['ref_images.ref_image_1']=['environment',0]
    g['noise']['inputs']['noise_seed']=p['seed']
    for key in ['checkpoint','checkpoint_audio','save_first','save_final']:g[key]['inputs']['filename_prefix']=f'gen_studio/{ident}/bunny/{key}'
    if names.get('continuity'):
        from .bunny_segments import add_guides
        frame_idx=p.get('bunny_guide_frame_index',0) if p.get('bunny_version')==TAIL_CONTEXT_VERSION else 0
        g=add_guides(g,names['continuity'],frame_idx=frame_idx)
    return g

async def process(service,job):
    if job['parameters'].get('transfer_method')=='tara':
        from .motion_tara import process as tara_process
        return await tara_process(service,job)
    if job['parameters'].get('bunny_version') in (VERSION,TAIL_CONTEXT_VERSION,LEGACY_VERSION):
        from .bunny_segments import process as segmented
        return await segmented(service,job)
    if job['parameters'].get('bunny_version') not in (None,'bunny-v1'):
        raise ValueError('Versión de Bunny no compatible.')
    from .job_cancel import checkpoint,thread_checkpoint
    p=job['parameters'];folder=service.root/'jobs'/job['id'];folder.mkdir(parents=True,exist_ok=True)
    checkpoint(service,job['id']);paths=await asyncio.to_thread(checked_assets,service,p)
    service.store.update(job['id'],stage='Preparando vídeo para Bunny')
    ready=await cpu(service,job,'bunny_prepare',{'source':str(paths['video']),'source_sha256':p['video_sha256'],'frames':p['frames'],'start_frame':round(p['start_seconds']*24),'resolution':p['resolution']})
    direction=p['direction'].strip()
    if direction:
        from .prompt_language import normalize
        record=await normalize(service,job,direction,folder/'bunny-direction.json');direction=record['prompt']
    checkpoint(service,job['id'])
    names_path=folder/'bunny-uploads.json'
    if names_path.exists():names=json.loads(names_path.read_text())
    else:
        names={'video':await upload(service,ready['video'],job['id']),'reference':await upload(service,paths['reference'],job['id'])}
        if paths.get('environment_reference'):names['environment_reference']=await upload(service,paths['environment_reference'],job['id'])
        names_path.write_text(json.dumps(names),encoding='utf-8')
    result=await phase(service,job,'Bunny · Movimiento y refinamiento',graph(p,names,job['id'],direction),'save_final','.mp4')
    if result is None:return
    checkpoint(service,job['id']);service.store.update(job['id'],stage='Validando vídeo y recuperando audio original')
    final=await cpu(service,job,'bunny_finalize',{'video':result['path'],'video_sha256':result['sha256'],'source':str(paths['video']),'source_sha256':p['video_sha256'],'frames':p['frames'],'start_frame':round(p['start_seconds']*24),'resolution':p['resolution']})
    await asyncio.to_thread(checked_assets,service,p)
    target=service.root/'results'/(job['id']+'.mp4');target.parent.mkdir(exist_ok=True)
    from .motion_hybrid import verified_copy
    await thread_checkpoint(service,job['id'],verified_copy,final['video'],target,final['files'][final['video']])
    service.store.update(job['id'],status='completed',stage='Bunny · Motion Transfer listo',result_available=True,progress=None,scene_resolution=p['resolution'],motion_result_sha256=digest(target),motion_audio=final['audio'])

def register(app,settings):
    @app.get('/api/workflows/motion-tara/preflight')
    async def tara_preflight():
        from .motion_tara import preflight
        return await preflight(app.state.jobs)

    @app.get('/api/workflows/motion-bunny/preflight')
    async def preflight():
        service=app.state.jobs;p={'frames':120,'base_resolution':'544x960','seed':42,'scene_mode':'reference'}
        check=await check_graph(service.client,settings['comfy_url'],graph(p,{'video':'source.mp4','reference':'character.png'},'preflight'))
        guided=await check_graph(service.client,settings['comfy_url'],graph(p,{'video':'source.mp4','reference':'character.png','continuity':'previous.png'},'preflight-guide'))
        return {**check,'ok':check['ok'] and guided['ok'],'errors':check['errors']+guided['errors'],'continuity':guided,'method':'bunny','version':VERSION,'maximum_frames':MAX_FRAMES,'chunk_frames':CHUNK_FRAMES,'default':True}

    @app.post('/api/motion-bunny/render',status_code=202)
    async def render(body:RenderRequest):
        if not settings['generation_enabled']:raise HTTPException(503,'Generación desactivada.')
        service=app.state.jobs;p=body.model_dump(mode='json');key=p.pop('request_key');method=p.pop('method')
        try:
            async with service.lock:
                with service.store.connect() as db:
                    existing=db.execute('SELECT payload,state FROM jobs WHERE request_key=?',(key,)).fetchone()
                if existing:
                    prior=json.loads(existing[0])
                    if prior.get('operation')!='motion_bunny' or prior.get('transfer_method','bunny')!=method or any(prior.get(k)!=v for k,v in p.items()):
                        raise ValueError('Esta clave de petición ya se usó con otros parámetros.')
                    await asyncio.to_thread(checked_assets,service,prior)
                    return service.store.job(json.loads(existing[1])['id'])
                if method=='tara' and p['frames']>120 and not settings.get('video_sequences_enabled'):raise ValueError('TARA-H3 admite pruebas de hasta 5 segundos.')
                video=service.store.asset(p['video_id'])
                if not video or video['kind']!='video':raise ValueError('Selecciona un vídeo de este espacio.')
                if round(p['start_seconds']*24)+p['frames']>round(video['duration']*24):raise ValueError('El intervalo supera el final del vídeo.')
                if p['environment_reference_id'] and p['scene_mode']=='source':raise ValueError('Para usar una imagen de escenario, elige cambiar escenario.')
                if p['direction'].strip() and len(p['direction'].strip())<10:raise ValueError('Describe el cambio con al menos 10 caracteres o deja el campo vacío.')
                for role,kind in [('video','video'),('reference','image'),('environment_reference','image')]:
                    if not p.get(role+'_id'):continue
                    a=service.store.asset(p[role+'_id'])
                    if not a or a['kind']!=kind:raise ValueError('Selecciona entradas válidas de este espacio.')
                    p[role+'_sha256']=await asyncio.to_thread(digest,local_path(service.root/'inputs',service.root/'inputs'/a['filename']))
                base,final=dimensions(video.get('display_width',video['width']),video.get('display_height',video['height']),p['quality'])
                p.update(engine='viggle_motion',operation='motion_bunny',transfer_method='bunny',bunny_version=VERSION,bunny_chunk_frames=CHUNK_FRAMES,bunny_continuity_version=CONTINUITY,base_resolution=f'{base[0]}x{base[1]}',resolution=f'{final[0]}x{final[1]}',steps=12,source_sha256=p['video_sha256'])
                if method=='tara':
                    from .motion_tara import dimensions as tara_dimensions,VERSION as TARA_VERSION,SOURCE_COMMIT,PROFILE,STEPS
                    size=tara_dimensions(video.get('display_width',video['width']),video.get('display_height',video['height']),p['quality'])
                    for field in ('bunny_version','bunny_chunk_frames','bunny_continuity_version'):p.pop(field,None)
                    p.update(transfer_method='tara',steps=STEPS,tara_version=TARA_VERSION,tara_profile=PROFILE,tara_source_commit=SOURCE_COMMIT,base_resolution=f'{size[0]}x{size[1]}',resolution=f'{size[0]}x{size[1]}')
                await asyncio.to_thread(checked_assets,service,p)
                job=service.store.create_job(str(uuid4()),key,p)
        except (ValueError,OSError) as e:raise HTTPException(400,str(e)) from e
        service.wake.set();return job

    @app.get('/api/jobs/{job_id}/bunny-inputs')
    async def inputs(job_id:UUID):
        service=app.state.jobs;j=service.store.job(str(job_id))
        if not j:raise HTTPException(404,'Trabajo no encontrado.')
        return {'assets':[a for role in ('video','reference','environment_reference') if (a:=service.store.asset(j['parameters'].get(role+'_id') or ''))]}
