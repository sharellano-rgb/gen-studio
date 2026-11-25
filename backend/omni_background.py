"""Opt-in background restoration copies. Parent renders are immutable."""
import asyncio,json,shutil
from pathlib import Path
from uuid import UUID,uuid4,uuid5
from fastapi import HTTPException
from pydantic import BaseModel,Field,ConfigDict
from .head import digest
from .guided_composition import local_path
from .guided_head import phase
from .direct_character import cpu
from .job_cancel import checkpoint,thread_checkpoint
from .hands_recipe import render_frames

class Request(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_key:UUID
    source_x:float=Field(ge=0,le=1,allow_inf_nan=False)
    source_y:float=Field(ge=0,le=1,allow_inf_nan=False)
    result_x:float=Field(ge=0,le=1,allow_inf_nan=False)
    result_y:float=Field(ge=0,le=1,allow_inf_nan=False)

def inputs(service,parent):
    if not parent or parent['status']!='completed' or parent['parameters'].get('direct_preset')!='fasth3_omni' or parent['parameters'].get('operation'):
        raise ValueError('Selecciona una toma Omni terminada en este espacio.')
    folder=service.root/'jobs'/parent['id']
    prepared=json.loads((folder/'direct-prepare.json').read_text())
    source=local_path(service.root,prepared['video'])
    result=service.root/'results'/(parent['id']+'.mp4')
    if digest(source)!=prepared['files'][str(source)] or digest(result)!=parent.get('direct_result_sha256'):
        raise ValueError('Cambió una entrada del resultado original.')
    return source,result

def register(app,settings):
    @app.post('/api/jobs/{job_id}/omni-background',status_code=202)
    async def submit(job_id:UUID,body:Request):
        service=app.state.jobs
        if not settings.get('generation_enabled'):raise HTTPException(503,'Generación desactivada.')
        async with service.lock:
            parent=service.store.job(str(job_id))
            if not parent:raise HTTPException(404,'Trabajo no encontrado.')
            try:
                source,result=await asyncio.to_thread(inputs,service,parent)
                p=parent['parameters']
                payload={k:p[k] for k in ('video_id','reference_id','frames','resolution','direct_preset')}
                payload.update(engine='h3_direct',operation='omni_background',omni_parent_id=parent['id'],
                    source_sha256=await asyncio.to_thread(digest,source),result_sha256=await asyncio.to_thread(digest,result),
                    background_version='omni-background-v1',**body.model_dump(mode='json',exclude={'request_key'}))
                child=service.store.create_job(str(uuid4()),str(body.request_key),payload)
            except (ValueError,OSError,KeyError) as error:raise HTTPException(409,str(error)) from error
            service.wake.set();return child

def tracking_graph(p,video,ident,label):
    from .ninja_graphs import track_graph
    if label not in ('source','result'):raise ValueError('Origen de máscara no válido.')
    w,h=map(int,p['resolution'].split('x'))
    return track_graph({'recipe':'w2','mode':'body','frames':p['frames'],'render_frames':render_frames(p['frames']),
        'width':w,'height':h,'x':p[label+'_x'],'y':p[label+'_y']},video,str(uuid5(UUID(ident),label)))

async def process(service,job):
    from .motion_layers import uploaded
    from .ninja_graphs import track_graph
    from .ninja_swap import fetch_artifact
    from .upscale import publish
    p=job['parameters'];ident=job['id'];folder=service.root/'jobs'/ident;folder.mkdir(parents=True,exist_ok=True)
    if p.get('background_version')!='omni-background-v1':raise ValueError('Versión de composición no válida.')
    parent=service.store.job(p['omni_parent_id'])
    source,result=await thread_checkpoint(service,ident,inputs,service,parent)
    if digest(source)!=p['source_sha256'] or digest(result)!=p['result_sha256']:raise ValueError('Cambió la toma elegida.')
    w,h=map(int,p['resolution'].split('x'));count=render_frames(p['frames'])
    ready=await cpu(service,job,'omni_background_prepare',{'video':str(result),'video_sha256':p['result_sha256'],
        'frames':p['frames'],'render_frames':count,'width':w,'height':h})
    masks={}
    for label,video in [('source',source),('result',Path(ready['video']))]:
        checkpoint(service,ident)
        names=await uploaded(service,job,folder,label,{'video':video})
        g=tracking_graph(p,names['video'],ident,label)
        g.pop('preview-save');g.pop('preview-save-video')
        service.store.update(ident,stage='Siguiendo silueta '+label)
        tracked=await phase(service,job,'background-'+label,g,'save-mask','.json')
        if tracked is None:return
        audit=json.loads(Path(tracked['path']).read_text())
        if (audit['frames'],audit['width'],audit['height'])!=(count,w,h):raise ValueError('Dimensiones incorrectas de la máscara.')
        mask=folder/(label+'-mask.npz');await fetch_artifact(service,audit['bundle'],mask,audit['sha256'])
        masks[label+'_mask']=str(mask);masks[label+'_mask_sha256']=audit['sha256']
    service.store.update(ident,status='preparing',stage='Recuperando el fondo original fuera de las siluetas')
    final=await cpu(service,job,'omni_background_composite',{'source':str(source),'source_sha256':p['source_sha256'],
        'video':ready['video'],'video_sha256':ready['files'][ready['video']],
        'frames':p['frames'],'render_frames':count,'width':w,'height':h,**masks})
    await thread_checkpoint(service,ident,inputs,service,parent)
    target=service.root/'results'/(ident+'.mp4')
    await thread_checkpoint(service,ident,publish,Path(final['video']),target,final['files'][final['video']])
    service.store.update(ident,status='completed',stage='Fondo original recuperado · Revisa bordes y oclusiones',result_available=True,
        progress=None,scene_resolution=p['resolution'],direct_result_sha256=final['files'][final['video']],
        omni_background_report=final['stats'],direct_review={'identity_approved':False,'review_required':True})
