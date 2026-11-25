"""Viggle motion followed by an independently recoverable H3 identity pass."""
import asyncio
import json
import shutil
from pathlib import Path
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Literal

from .head import digest
from .guided_composition import local_path
from .media import probe, source_resolution
from .store import now

METHOD='viggle_h3_identity'
LEGACY_VERSION='viggle124-h3-v1'
VERSION='viggle124-object-swap-v2'
OPERATION='motion_hybrid'
MAX_FRAMES=360
from .motion_scene import VERSION as SCENE_VERSION,METHOD as SCENE_METHOD,VERSIONS as SCENE_VERSIONS
RESOLUTIONS=('480x864','864x480','640x1152','1152x640','768x1344','1344x768','512x512')
IDENTITY_PROMPT=(
 'Refine only the main transformed character in <Video 1> using the exact identity and appearance in <Picture 1>. '
 'Match the reference facial proportions, eyes, hair or fur, distinctive anatomy, clothing and accessories consistently throughout the clip. '
 'The video determines the exact action timing, head orientation, gaze, expression changes, arm positions, leg bends, body rotation and contact with objects in every frame. '
 'Keep the source camera movement, framing, subject size and position. Do not turn the head to show the reference face when the video shows another orientation. '
 'Preserve the complete environment, its visual rendering style, colors, lighting, all objects and other people from the video. '
 'The reference supplies identity and appearance only; do not copy its pose or background. '
 'Do not add people, props, actions, an opening pause or camera cuts. Preserve the original audio timing.'
)

class RenderRequest(BaseModel):
    request_key: UUID
    approved_anchor_sha256: str = Field(pattern='^[a-f0-9]{64}$')
    identity_reference_id: UUID | None = None
    reuse_motion_job_id: UUID | None = None
    identity_resolution: Literal['480x864','864x480','640x1152','1152x640','768x1344','1344x768','512x512']='768x1344'

class SceneTrialRequest(RenderRequest):
    scene_reference_id:UUID|None=None
    body_source_job_id:UUID|None=None
    head_reference_video_id:UUID|None=None
    scene_goal:str=Field(min_length=10,max_length=800)
    frames:int=Field(default=73,ge=5,le=107,strict=True)

def identity_child(service,parent):
    child=service.store.job(parent.get('hybrid_identity_child') or '')
    if child and child['parameters'].get('hybrid_parent_id')==parent['id']:
        return child
    return None

def active_leaf(service,parent):
    child=identity_child(service,parent)
    if not child:return parent
    if child['parameters'].get('operation')=='long_character':
        leaf=service.store.job(child.get('long_active_child') or '')
        if leaf and leaf['parameters'].get('long_parent_id')==child['id'] and leaf['parameters'].get('hybrid_parent_id')==parent['id']:
            return leaf
    return child

def accepted_child(service,parent):
    if parent['parameters'].get('operation')!=OPERATION:return None
    child=active_leaf(service,parent)
    if child.get('prompt_id') and child['status'] not in ('completed','failed','cancelled'):
        return child
    return None

def submitting(service,parent):
    return active_leaf(service,parent)['status']=='submitting'

def checked_anchor(service,p):
    from .motion_h3 import reviewed_view
    from .motion_transfer import inputs
    anchor,path=reviewed_view(service,p['motion_anchor_id'],p['anchor_sha256'])
    inputs(service,anchor['parameters'])
    for key in ('video_id','reference_id','source_sha256','reference_sha256','frames','resolution','start_seconds','seed','environment_reference_id','environment_reference_sha256'):
        if anchor['parameters'].get(key)!=(p.get('scene_anchor_frames') if key=='frames' and p.get('hybrid_version') in SCENE_VERSIONS else p.get(key)):raise ValueError('Cambió la preparación de esta toma.')
    if anchor['parameters'].get('original_reference'):raise ValueError('Este método requiere un fotograma de escena preparado.')
    ref=service.store.asset(p['hybrid_identity_reference_id'])
    if not ref or ref['kind']!='image':raise ValueError('No se encuentra la referencia original de identidad.')
    ref_path=service.root/'inputs'/ref['filename']
    if digest(ref_path)!=p['hybrid_identity_reference_sha256']:raise ValueError('Cambió la imagen de identidad.')
    return anchor,path,ref_path

def intermediate(service,job):
    if not job or job['parameters'].get('operation')!=OPERATION:raise ValueError('Trabajo Motion Transfer no encontrado.')
    info=job.get('hybrid_motion')
    if not info:raise ValueError('La primera pasada todavía no está disponible.')
    path=local_path(service.root,info['path'])
    if not path.is_file() or digest(path)!=info['sha256']:raise ValueError('Cambió la salida de movimiento.')
    return path,info['sha256']

def reusable_motion(service,p):
    ident=p.get('hybrid_reuse_motion_job_id')
    if not ident:return None
    original=service.store.job(ident)
    if not original:raise ValueError('La primera pasada no está disponible en este espacio.')
    old=original['parameters']
    keys=('motion_anchor_id','anchor_sha256','video_id','source_sha256','reference_id','reference_sha256','frames','resolution','start_seconds','seed')
    if any(old.get(key)!=p.get(key) for key in keys):raise ValueError('La primera pasada no corresponde a esta guía, fuente e intervalo.')
    path,sha=intermediate(service,original)
    return path,sha


def identity_parameters(parent,asset,identity_resolution):
    p=parent['parameters'];ref=p['hybrid_identity_reference_id']
    result=dict(engine='h3_direct',video_id=asset['id'],reference_id=ref,face_reference_id=ref,
        direct_reference_ids=[ref],preserve_face=False,guide_face_reference=False,ref_image_size='max',
        prompt=IDENTITY_PROMPT,subject='the main transformed character',
        frames=p['frames'],resolution=identity_resolution,steps=8,seed=p['seed'],lora_strength=1.0,
        direct_preset='turbo8',direct_character_version='v1.5',direct_crop_face=False,direct_reference_type='nonhuman',
        direct_start_seconds=0,direct_binding_mode='manual',direct_targets=[],direct_direction='',direct_high_quality=False,
        direct_scene_cuts=False,direct_dynamic_prompts=False,direct_global_goal='',match_source_orientation=True,
        direct_source_sha256=asset['sha256'],direct_reference_hashes=[p['hybrid_identity_reference_sha256']],
        hybrid_parent_id=parent['id'])
    if p.get('hybrid_version')==VERSION:
        from .motion_identity import RECIPE,prompt
        result.update(direct_preset='hands',hybrid_identity_recipe=RECIPE,prompt=prompt(),
            direct_edit_policy={'version':1,'scope':'character','clothing':'reference','background':'source','background_description':''})
    elif p.get('hybrid_version')!=LEGACY_VERSION:
        raise ValueError('Versión de identidad Motion Transfer desconocida.')
    if p['frames']>240:
        from .long_plan import CONTINUOUS_CHUNK_VERSION
        result.update(operation='long_character',direct_chunk_frames=120,direct_chunk_version=CONTINUOUS_CHUNK_VERSION)
        if p.get('hybrid_version')==VERSION:
            from .continuity import VERSION as continuity_version
            result.update(direct_continuity=True,direct_continuity_version=continuity_version)
    return result

def verified_copy(source,target,sha):
    target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if digest(target)!=sha:raise ValueError('Existe otra copia diferente; se conserva.')
        return
    temporary=target.with_name(target.name+'.'+str(uuid4())+'.part')
    shutil.copy2(source,temporary)
    if digest(temporary)!=sha:raise ValueError('No se pudo verificar la copia temporal.')
    if target.exists():
        if digest(target)!=sha:raise ValueError('Existe otra copia diferente; se conservan los archivos.')
        return
    temporary.replace(target)


def register_intermediate(service,job,path,sha):
    ident=str(uuid5(NAMESPACE_URL,'gen-studio:motion-hybrid:'+job['id']))
    target=service.root/'inputs'/(ident+'.mp4')
    verified_copy(path,target,sha)
    old=service.store.asset(ident)
    if old:
        if old.get('sha256')!=sha:raise ValueError('Cambió el registro de la primera pasada.')
        return old
    return service.store.add_asset(dict(id=ident,kind='video',filename=target.name,
        name='Movimiento para identidad · '+job['id'][:8],sha256=sha,internal=True,
        source_result_parent_id=job['id'],bytes=target.stat().st_size,**probe(target)))

async def process(service,job):
    from .motion_transfer import cpu,upload,transfer_graph
    from .guided_head import phase
    from .job_cancel import checkpoint,thread_checkpoint
    from .studio_results import verified_result
    p=job['parameters']
    if p.get('hybrid_version') not in (VERSION,LEGACY_VERSION,*SCENE_VERSIONS):raise ValueError('Versión de Motion Transfer desconocida.')
    checkpoint(service,job['id'])
    anchor,anchor_path,_=await asyncio.to_thread(checked_anchor,service,p)
    folder=service.root/'jobs'/job['id'];folder.mkdir(parents=True,exist_ok=True)
    ready=json.loads((service.root/'jobs'/anchor['id']/'motion-prepare.json').read_text())
    if digest(Path(ready['video']))!=ready['files'][ready['video']]:raise ValueError('Cambió el vídeo de movimiento preparado.')
    if p.get('hybrid_version') in SCENE_VERSIONS:
        from . import motion_scene
        service.store.update(job['id'],hybrid_stage='scene',stage='H3 · Reconstruyendo la escena completa sin Viggle')
        body=await asyncio.to_thread(motion_scene.body_source,service,p)
        body_path,body_sha=body or (Path(ready['video']),ready['files'][ready['video']])
        asset=await thread_checkpoint(service,job['id'],register_intermediate,service,job,body_path,body_sha)
        scene=await thread_checkpoint(service,job['id'],motion_scene.register_scene,service,job,anchor_path)
        child_p=motion_scene.parameters(job,asset,scene)
    else:
        service.store.update(job['id'],hybrid_stage='motion')
        reuse=await asyncio.to_thread(reusable_motion,service,p)
        if reuse:
            source,sha=reuse
            target=folder/'reused-motion.mp4'
            await thread_checkpoint(service,job['id'],verified_copy,source,target,sha)
            motion=dict(video=str(target),files={str(target):sha})
            service.store.update(job['id'],hybrid_motion_reused_from=p['hybrid_reuse_motion_job_id'])
        else:
            driving=await cpu(service,job,'hybrid_input',dict(source=ready['video'],source_sha256=ready['files'][ready['video']],
                frames=p['frames'],resolution=p['resolution']))
            names_path=folder/'hybrid-uploads.json'
            if names_path.exists():names=json.loads(names_path.read_text())
            else:
                names=dict(video=await upload(service,driving['video'],job['id']),anchor=await upload(service,anchor_path,job['id']))
                names_path.write_text(json.dumps(names),encoding='utf-8')
            rendered=await phase(service,job,'hybrid-motion',transfer_graph(p,names['video'],names['anchor'],job['id']),'save_video','.mp4')
            if rendered is None:return
            checkpoint(service,job['id'])
            motion=await cpu(service,job,'finalize',dict(video=rendered['path'],video_sha256=rendered['sha256'],
                audio_source=ready['video'],audio_source_sha256=ready['files'][ready['video']],frames=p['frames'],resolution=p['resolution']),
                checkpoint_name='hybrid-motion-final')
        service.store.update(job['id'],hybrid_motion=dict(path=motion['video'],sha256=motion['files'][motion['video']]),
            hybrid_stage='identity',stage='2 de 2 · Preparando identidad con H3',progress=None)
        asset=await thread_checkpoint(service,job['id'],register_intermediate,service,job,Path(motion['video']),motion['files'][motion['video']])
        child_p=identity_parameters(job,asset,p['hybrid_identity_resolution'])
    child=service.store.create_job(str(uuid4()),str(uuid5(NAMESPACE_URL,job['id']+'/hybrid-identity')),child_p)
    service.store.update(job['id'],hybrid_identity_child=child['id'],status='running')
    if child['status']=='submission_unknown':
        service.store.update(job['id'],status='submission_unknown',stage='H3: envío sin confirmar',error=child.get('error'));return
    if child['status'] in ('failed','cancelled'):raise ValueError('La pasada H3 no terminó: '+(child.get('error') or child['status']))
    if child['status']!='completed':
        await service._run(child)
        child=service.store.job(child['id'])
    checkpoint(service,job['id'])
    if child['status']=='queued':
        service.store.update(job['id'],status='queued',stage='Esperando el motor para continuar con H3');return
    if child['status']=='submission_unknown':
        service.store.update(job['id'],status='submission_unknown',stage='H3: envío sin confirmar',error=child.get('error'));return
    if child['status']!='completed':raise ValueError('H3: '+(child.get('error') or child['status']))
    output,sha=await asyncio.to_thread(verified_result,service,child)
    await asyncio.to_thread(checked_anchor,service,p)
    def publish():
        target=service.root/'results'/(job['id']+'.mp4');target.parent.mkdir(exist_ok=True)
        if target.exists() and digest(target)!=sha:raise ValueError('Ya existe otro resultado; se conserva.')
        verified_copy(output,target,sha)
    await thread_checkpoint(service,job['id'],publish)
    service.store.update(job['id'],status='completed',stage=('H3 · Escena completa lista · Revisa escenario, identidad y movimiento' if p.get('hybrid_version') in SCENE_VERSIONS else 'Motion Transfer listo · Revisa identidad y movimiento'),
        result_available=True,hybrid_stage='completed',motion_result_sha256=sha,
        scene_resolution=p['hybrid_identity_resolution'],progress=None,
        hybrid_review=dict(identity_approved=False,timing_approved=False,review_required=True))

async def finish_cancel(service,job_id):
    from .job_cancel import finish
    root=service.store.job(job_id)
    target=active_leaf(service,root)
    if target['id']==job_id or target['status'] in ('completed','failed','cancelled'):
        for candidate in service.store.unfinished_jobs():
            if candidate['parameters'].get('hybrid_parent_id')==job_id:
                service.store.update(candidate['id'],status='cancelled',stage='Cancelado antes de continuar',progress=None)
        return await finish(service,job_id,_hybrid=False)
    service.store.update(target['id'],cancel_requested=True)
    await finish(service,target['id'])
    stopped=service.store.job(target['id'])
    if stopped['status']=='submission_unknown':
        return service.store.update(job_id,status='submission_unknown',stage='Cancelación de H3 sin confirmar',
            error=stopped.get('error') or 'No se pudo identificar el envío; se conserva el registro y no se repetirá.',
            cancel_resources_released=False,progress=None)
    child=identity_child(service,root)
    if child and child['id']!=target['id'] and child['status']!='completed':
        service.store.update(child['id'],status='cancelled',stage='Identidad detenida por el usuario',progress=None)
    # Unsent attached segments must never start after this cancellation.
    for candidate in service.store.unfinished_jobs():
        if candidate['parameters'].get('hybrid_parent_id')==job_id and candidate['id']!=target['id']:
            service.store.update(candidate['id'],status='cancelled',stage='Cancelado antes de continuar',progress=None)
    return service.store.update(job_id,status='cancelled',stage='Motion Transfer cancelado · Se conserva el trabajo anterior',
        cancelled_at=now(),progress=None,error=None,cancel_resources_released=stopped.get('cancel_resources_released',False),
        cancel_cleanup_warning=stopped.get('cancel_cleanup_warning'))

def register(app,settings):
    @app.get('/api/workflows/motion-scene/preflight')
    async def scene_preflight(anchor_editor:Literal['klein','qwen_image_21']='klein',with_environment:bool=False):
        from . import motion_scene, motion_transfer
        from .direct_character import graph
        from .character_lora import verify
        from .workflows import check_graph
        import httpx
        anchor={'ok':False,'errors':['No se pudo comprobar la preparación de la guía.']}
        scene={'ok':False,'errors':['No se pudo comprobar H3 para la escena completa.']}
        result=dict(version=SCENE_VERSION,maximum_frames=motion_scene.MAX_FRAMES,
                    continuous_only=True,anchor_editor=anchor_editor,with_environment=with_environment)
        try:
            # Check the selected still-image editor only. No Viggle graph,
            # conditioning, video model or LoRA belongs to this creation path.
            p=dict(resolution='640x1152',seed=42,scene_mode='restyle',
                   direction='Reconstruct the destination scene while preserving the source pose.',
                   anchor_editor=anchor_editor,qwen_recipe='pose_v2')
            if anchor_editor=='qwen_image_21':
                from .motion_qwen import VENDOR,VENDOR_SHA
                skipped={('sgopt_ref_image_1__load','image'),('sgopt_ref_image_2__load','image')}
            else:
                VENDOR,VENDOR_SHA=motion_transfer.VENDOR,motion_transfer.VENDOR_SHA
                skipped={('frame0','image'),('char_load','image')}
            if with_environment:
                skipped.add(('sgopt_ref_image_3__load' if anchor_editor=='qwen_image_21' else 'environment_load','image'))
            anchor=await check_graph(app.state.client,settings['comfy_url'],
                motion_transfer.anchor_graph(p,'frame.png','identity.png','preflight-scene','environment.png' if with_environment else None),
                digest(VENDOR)==VENDOR_SHA,skip_inputs=skipped)
            await asyncio.to_thread(verify,'v1.5')
            parent={'id':'preflight-scene','parameters':{
                'frames':73,'seed':42,'hybrid_identity_resolution':'768x1344',
                'hybrid_identity_reference_id':'identity','hybrid_identity_reference_sha256':'a'*64,
                'motion_scene_goal':'Rebuild the full destination environment and character from the scene guide.'}}
            payload=motion_scene.parameters(parent,{'id':'motion','sha256':'b'*64},
                                             {'id':'scene','sha256':'c'*64})
            scene=await check_graph(app.state.client,settings['comfy_url'],
                graph(payload,'motion.mp4',['identity.png','scene.png'],'preflight-scene'),
                skip_inputs={('14','file'),('16','image'),('reference_1','image')})
            return dict(result,ok=anchor['ok'] and scene['ok'],anchor=anchor,scene=scene)
        except (ValueError,OSError,httpx.HTTPError) as error:
            return dict(result,ok=False,anchor=anchor,scene=scene,detail=str(error))

    @app.get('/api/workflows/motion-hybrid/preflight')
    async def preflight():
        from .motion_transfer import transfer_graph,VENDOR,VENDOR_SHA
        from .direct_character import graph
        from .character_lora import verify
        from .workflows import check_graph
        import httpx
        try:
            await asyncio.to_thread(verify,'v1.5')
            p=dict(resolution='640x1152',seed=42,prompt=IDENTITY_PROMPT,frames=73,steps=8,
                lora_strength=1,direct_preset='hands',direct_character_version='v1.5',hybrid_parent_id='preflight',hybrid_identity_recipe='object-swap-style-v1')
            a=await check_graph(app.state.client,settings['comfy_url'],transfer_graph(p,'preflight.mp4','preflight.png','preflight'),
                digest(VENDOR)==VENDOR_SHA,skip_inputs={('load_video','file'),('anchor','image')})
            from .continuity import apply_guide
            from .motion_identity import prompt
            p['prompt']=prompt()
            import copy
            identity_graph=graph(p,'preflight.mp4',['preflight.png'],'preflight')
            b=await check_graph(app.state.client,settings['comfy_url'],identity_graph,skip_inputs={('14','file'),('16','image')})
            c=await check_graph(app.state.client,settings['comfy_url'],apply_guide(copy.deepcopy(identity_graph),'continuity.png'),
                skip_inputs={('14','file'),('16','image'),('continuity-image','image')})
            return dict(ok=a['ok'] and b['ok'] and c['ok'],motion=a,identity=b,continuity=c,version=VERSION,maximum_frames=MAX_FRAMES,continuous_only=True)
        except (ValueError,OSError,httpx.HTTPError) as error:
            return dict(ok=False,detail=str(error),version=VERSION,maximum_frames=MAX_FRAMES,continuous_only=True)

    @app.post('/api/jobs/{anchor_id}/motion-transfer-hybrid',status_code=202)
    async def create(anchor_id:UUID,body:RenderRequest):
        return await create_bound(anchor_id,body,False)

    @app.post('/api/jobs/{anchor_id}/motion-transfer-scene-trial',status_code=202)
    async def scene_trial(anchor_id:UUID,body:SceneTrialRequest):
        return await create_bound(anchor_id,body,True)

    async def create_bound(anchor_id,body,scene_trial):
        if not settings['generation_enabled']:raise HTTPException(503,'Generación desactivada.')
        from .motion_h3 import reviewed_view
        from .motion_transfer import inputs
        service=app.state.jobs
        try:
            async with service.lock:
                anchor,_=await asyncio.to_thread(reviewed_view,service,str(anchor_id),body.approved_anchor_sha256)
                p=anchor['parameters']
                if p.get('original_reference') or not p.get('reference_id'):raise ValueError('Prepara un fotograma de escena con una referencia de personaje.')
                if p.get('environment_reference_id') and not scene_trial:raise ValueError('La guía con imagen de escenario requiere H3 · Escena completa.')
                if not 5<=p['frames']<=MAX_FRAMES:raise ValueError('Viggle + H3 admite hasta 15 segundos de una toma continua.')
                if scene_trial:
                    if p.get('environment_reference_id') and body.scene_reference_id:
                        raise ValueError('La imagen de escenario ya está incorporada en la guía revisada; no se sustituye antes de H3.')
                    if p.get('anchor_index')!=0 or p.get('scene_mode')!='restyle' or body.frames>p['frames'] or body.reuse_motion_job_id:
                        raise ValueError('La prueba requiere una escena completa inicial y hasta 107 fotogramas del mismo intervalo, sin reutilizar Viggle.')
                await asyncio.to_thread(inputs,service,p)
                ref_id=str(body.identity_reference_id) if body.identity_reference_id else p['reference_id']
                ref=service.store.asset(ref_id)
                if not ref or ref['kind']!='image':raise ValueError('Selecciona la imagen original para la identidad H3.')
                video=service.store.asset(p['video_id'])
                resolution=source_resolution(video,body.identity_resolution)
                params={**p,'engine':'viggle_motion','operation':OPERATION,'transfer_method':METHOD,'hybrid_version':VERSION,
                    'motion_anchor_id':anchor['id'],'anchor_sha256':body.approved_anchor_sha256,
                    'hybrid_identity_reference_id':ref_id,'hybrid_identity_reference_sha256':await asyncio.to_thread(digest,service.root/'inputs'/ref['filename']),
                    'hybrid_identity_resolution':resolution}
                if scene_trial:
                    params.update(hybrid_version=SCENE_VERSION,transfer_method=SCENE_METHOD,scene_anchor_frames=p['frames'],frames=body.frames,motion_scene_goal=body.scene_goal)
                    from .motion_scene import configure_refinements
                    await asyncio.to_thread(configure_refinements,service,params,body)
                if body.reuse_motion_job_id:
                    params['hybrid_reuse_motion_job_id']=str(body.reuse_motion_job_id)
                    await asyncio.to_thread(reusable_motion,service,params)
                # A lost reply may predate this release. Reusing its key must
                # retain the already accepted recipe, never upgrade/replay it.
                with service.store.connect() as db:
                    previous=db.execute('SELECT payload FROM jobs WHERE request_key=?',(str(body.request_key),)).fetchone()
                if previous:
                    version=json.loads(previous[0]).get('hybrid_version')
                    if (version in (VERSION,LEGACY_VERSION) and not scene_trial) or (version in SCENE_VERSIONS and scene_trial):params['hybrid_version']=version
                result=service.store.create_job(str(uuid4()),str(body.request_key),params)
            service.wake.set()
            return result
        except (ValueError,OSError,KeyError) as error:raise HTTPException(409,str(error)) from error

    @app.get('/api/jobs/{job_id}/motion-hybrid-inputs')
    async def bound_inputs(job_id:UUID):
        service=app.state.jobs;job=service.store.job(str(job_id))
        if not job or job['parameters'].get('engine')!='viggle_motion':raise HTTPException(404,'Trabajo no encontrado.')
        p=job['parameters'];ids=dict.fromkeys(p.get(key) for key in ('video_id','reference_id','hybrid_identity_reference_id','environment_reference_id'))
        return {'assets':[item for ident in ids if ident for item in [service.store.asset(ident)] if item]}

    @app.get('/api/jobs/{job_id}/motion-hybrid-intermediate')
    async def motion_result(job_id:UUID,download:bool=False):
        service=app.state.jobs;job=service.store.job(str(job_id))
        if not job:raise HTTPException(404,'Trabajo no encontrado.')
        try:path,_=await asyncio.to_thread(intermediate,service,job)
        except (ValueError,OSError,KeyError) as error:raise HTTPException(409,str(error)) from error
        return FileResponse(path,media_type='video/mp4',filename=f'GEN-STUDIO-{job_id}-movimiento.mp4' if download else None)
