"""Facial refinement across cuts: independent masks, reviewed before rendering."""
import asyncio
import json
import math
import shutil
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL
from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from . import ninja_swap as ninja
from .head import digest
from .long_video import media
from .media import probe
from .ninja_limits import render_frames
from .studio_editor import _save

OPERATIONS={'ninja_shots_plan','ninja_shots_prepare','ninja_shots_render'}
FINAL={'completed','failed','cancelled','submission_unknown'}


class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid')

class PlanRequest(Strict):
    request_key:UUID
    video_id:UUID
    reference_id:UUID
    start_seconds:float=Field(default=0,ge=0,le=3600,allow_inf_nan=False)
    frames:int=Field(ge=5,le=1440,strict=True)
    mode:Literal['head','body']='head'
    seed:int=Field(default=123456,ge=0,le=9007199254740991,strict=True)

class Selection(Strict):
    index:int=Field(ge=0,strict=True)
    passthrough:bool=Field(default=False,strict=True)
    x:float|None=Field(default=None,ge=0,le=1,allow_inf_nan=False)
    y:float|None=Field(default=None,ge=0,le=1,allow_inf_nan=False)

    @model_validator(mode='after')
    def point(self):
        if not self.passthrough and (self.x is None or self.y is None):raise ValueError('Marca un rostro en cada plano que quieras refinar.')
        return self

class MasksRequest(Strict):
    request_key:UUID
    shots:list[Selection]=Field(min_length=1,max_length=720)

class Approval(Strict):
    index:int=Field(ge=0,strict=True)
    preparation_id:UUID
    mask_sha256:str=Field(pattern=r'^[a-f0-9]{64}$')

class RenderRequest(Strict):
    request_key:UUID
    head_motion:Literal['preserve','original']='preserve'
    approvals:list[Approval]=Field(min_length=1,max_length=720)


def child_id(parent,index):return str(uuid5(NAMESPACE_URL,parent+f'/facial-shot/{index}'))


def old_request(service,key,request,operation,parent=None):
    with service.store.connect() as db:row=db.execute('SELECT state FROM jobs WHERE request_key=?',(str(key),)).fetchone()
    if not row:return None
    job=json.loads(row[0]);p=job['parameters']
    if p.get('operation')!=operation or p.get('ninja_shots_request')!=request or p.get('ninja_sequence_parent_id')!=parent:
        raise ValueError('Esta petición ya se utilizó con otros ajustes.')
    return job


def get_job(service,jid,operation=None):
    job=service.store.job(str(jid))
    if not job or job['parameters'].get('operation') not in OPERATIONS or operation and job['parameters']['operation']!=operation:
        raise ValueError('La secuencia no está disponible en este espacio.')
    return job


def load_plan(service,job):
    p=job['parameters'];plan_job=get_job(service,p.get('ninja_plan_id',job['id']),'ninja_shots_plan')
    path=service.root/'jobs'/plan_job['id']/'facial-plan.json'
    if plan_job['status']!='completed' or not path.is_file() or digest(path)!=plan_job.get('ninja_plan_sha256'):
        raise ValueError('Espera al análisis de cortes; el plan debe conservarse sin cambios.')
    ninja.validate_inputs(service,plan_job['parameters'])
    plan=json.loads(path.read_text(encoding='utf-8'))
    for s in plan['segments']:
        clip=ninja.asset_path(service,s['video_id'],'video')
        if digest(clip)!=s['sha256']:raise ValueError('Cambió un plano analizado.')
    return plan_job,plan


def selections(plan,shots):
    if [s['index'] for s in shots]!=list(range(len(plan['segments']))):raise ValueError('Selecciona todos los planos una sola vez y en orden.')
    for segment,selection in zip(plan['segments'],shots):
        Selection.model_validate(selection)
        if segment['keep']<5 and not selection['passthrough']:raise ValueError('Los planos de menos de cinco fotogramas deben conservarse sin cambios.')
    if all(s['passthrough'] for s in shots):raise ValueError('Selecciona al menos un plano para refinar.')
    return shots


def checked_masks(service,masks,approvals=None):
    _,plan=load_plan(service,masks);p=masks['parameters'];shots=selections(plan,p['ninja_selections'])
    expected=[]
    if masks['status']!='completed':raise ValueError('Espera a que terminen todas las máscaras.')
    for s,choice in zip(plan['segments'],shots):
        if choice['passthrough']:continue
        child=service.store.job(child_id(masks['id'],s['index']))
        if not child:raise ValueError('Falta la máscara de un plano.')
        cp=child['parameters']
        if (cp.get('ninja_sequence_owner'),cp.get('ninja_shot_index'),cp.get('video_id'),cp.get('reference_id'),cp.get('frames'),cp.get('x'),cp.get('y'))!=(masks['id'],s['index'],s['video_id'],p['reference_id'],s['render_frames'] if p.get('ninja_segments_version') else s['keep'],choice['x'],choice['y']):
            raise ValueError('La máscara no corresponde al rostro y plano seleccionados.')
        ninja.reviewed(service,child,child.get('ninja_mask_sha256'))
        ninja.validate_inputs(service,cp)
        expected.append(dict(index=s['index'],preparation_id=child['id'],mask_sha256=child['ninja_mask_sha256']))
    if approvals is not None and approvals!=expected:raise ValueError('Revisa y aprueba la máscara actual de cada plano antes de generar.')
    return plan,expected


def register(app,settings):
    def enabled():
        if not settings.get('ninja_enabled') or not settings['generation_enabled']:raise HTTPException(503,'Refinamiento Facial no está activado.')

    @app.post('/api/ninja/shots/plan',status_code=202)
    async def plan(body:PlanRequest):
        service=app.state.jobs;request=body.model_dump(mode='json',exclude={'request_key'})
        if 'mode' not in body.model_fields_set:request.pop('mode',None)
        async with service.lock:
            try:
                if old:=old_request(service,body.request_key,request,'ninja_shots_plan'):return old
                enabled();src=ninja.asset_path(service,body.video_id,'video');ref=ninja.asset_path(service,body.reference_id,'image')
                info=await asyncio.to_thread(probe,src);start=round(body.start_seconds*24);w,h=ninja.geometry(info)
                if start+body.frames>math.ceil(info['duration']*24):raise ValueError('El intervalo supera la duración del vídeo.')
                p={**request,'engine':'h3_ninja','operation':'ninja_shots_plan','recipe':'w2','mode':body.mode,'start_frame':start,
                    'width':w,'height':h,'resolution':f'{w}x{h}','ninja_shots_request':request,
                    'ninja_source_sha256':await asyncio.to_thread(digest,src),'ninja_reference_sha256':await asyncio.to_thread(digest,ref)}
                if settings.get('video_sequences_enabled'):
                    from .sequence_plan import VERSION
                    p['ninja_segments_version']=VERSION
                elif p['frames']>720:raise ValueError('El método histórico admite hasta 30 segundos.')
                job=service.store.create_job(str(uuid4()),str(body.request_key),p);service.wake.set();return job
            except (ValueError,OSError,KeyError) as error:raise HTTPException(409,str(error)) from error

    @app.post('/api/jobs/{job_id}/ninja-shots/prepare',status_code=202)
    async def prepare(job_id:UUID,body:MasksRequest):
        service=app.state.jobs;request=body.model_dump(mode='json',exclude={'request_key'})
        async with service.lock:
            try:
                if old:=old_request(service,body.request_key,request,'ninja_shots_prepare',str(job_id)):return old
                enabled();parent=get_job(service,job_id,'ninja_shots_plan');_,plan=await asyncio.to_thread(load_plan,service,parent)
                shots=selections(plan,request['shots'])
                p={**parent['parameters'],'operation':'ninja_shots_prepare','ninja_plan_id':parent['id'],
                    'ninja_sequence_parent_id':parent['id'],'ninja_shots_request':request,'ninja_selections':shots}
                job=service.store.create_job(str(uuid4()),str(body.request_key),p);service.wake.set();return job
            except (ValueError,OSError,KeyError) as error:raise HTTPException(409,str(error)) from error

    @app.post('/api/jobs/{job_id}/ninja-shots/render',status_code=202)
    async def render(job_id:UUID,body:RenderRequest):
        service=app.state.jobs;request=body.model_dump(mode='json',exclude={'request_key'})
        async with service.lock:
            try:
                if old:=old_request(service,body.request_key,request,'ninja_shots_render',str(job_id)):return old
                enabled();parent=get_job(service,job_id,'ninja_shots_prepare')
                await asyncio.to_thread(checked_masks,service,parent,request['approvals'])
                p={**parent['parameters'],'operation':'ninja_shots_render','ninja_masks_id':parent['id'],
                    'ninja_sequence_parent_id':parent['id'],'ninja_shots_request':request,'ninja_head_motion':body.head_motion}
                job=service.store.create_job(str(uuid4()),str(body.request_key),p);service.wake.set();return job
            except (ValueError,OSError,KeyError) as error:raise HTTPException(409,str(error)) from error

    @app.get('/api/jobs/{job_id}/ninja-shots')
    async def detail(job_id:UUID):
        service=app.state.jobs
        try:
            job=get_job(service,job_id);p=job['parameters'];plan_id=p.get('ninja_plan_id',job['id'])
            plan_job=get_job(service,plan_id,'ninja_shots_plan');path=service.root/'jobs'/plan_id/'facial-plan.json'
            if plan_job['status']!='completed':return {'job':job,'plan':plan_job,'plan_id':plan_id,'masks_id':None,'segments':[]}
            if digest(path)!=plan_job.get('ninja_plan_sha256'):raise ValueError('El plan guardado cambió.')
            plan=json.loads(path.read_text(encoding='utf-8'))
            masks_id=p.get('ninja_masks_id') or (job['id'] if p['operation']=='ninja_shots_prepare' else None)
            choices=p.get('ninja_selections',[]);segments=[]
            for s in plan['segments']:
                choice=choices[s['index']] if choices else dict(passthrough=s['keep']<5,x=None,y=None)
                segments.append({k:s[k] for k in ('index','start','keep','video_id','shot','context_frames','starts_at_cut') if k in s} | choice | {
                    'thumbnail_url':f'/api/jobs/{plan_id}/ninja-shots/{s["index"]}/thumbnail',
                    'mask_job':service.store.job(child_id(masks_id,s['index'])) if masks_id and not choice['passthrough'] else None,
                    'render_job':service.store.job(child_id(job['id'],s['index'])) if p['operation']=='ninja_shots_render' and not choice['passthrough'] else None})
            return dict(job=job,plan=plan_job,plan_id=plan_id,masks_id=masks_id,segments=segments)
        except (ValueError,OSError,KeyError) as error:raise HTTPException(409,str(error)) from error

    @app.get('/api/jobs/{job_id}/ninja-shots/{index}/thumbnail')
    async def thumbnail(job_id:UUID,index:int):
        service=app.state.jobs
        try:
            plan=get_job(service,job_id,'ninja_shots_plan');path=service.root/'jobs'/plan['id']/'facial-plan.json'
            if plan['status']!='completed' or digest(path)!=plan.get('ninja_plan_sha256'):raise ValueError('Plan no disponible.')
            segments=json.loads(path.read_text(encoding='utf-8'))['segments']
            if not 0<=index<len(segments):raise ValueError('Plano inexistente.')
            image=Path(segments[index]['thumbnail']).resolve()
            if not image.is_relative_to((service.root/'jobs'/plan['id']).resolve()):raise ValueError('Miniatura inválida.')
            return FileResponse(image,media_type='image/jpeg')
        except (ValueError,OSError,KeyError) as error:raise HTTPException(404,str(error)) from error

    @app.post('/api/jobs/{job_id}/ninja-shots/stop')
    async def stop(job_id:UUID):
        service=app.state.jobs
        async with service.lock:
            try:
                job=get_job(service,job_id)
                if job['status'] in FINAL:return job
                result=service.store.update(job['id'],ninja_stop_requested=True,stage='Se detendrá al terminar el plano actual')
                service.wake.set();return result
            except ValueError as error:raise HTTPException(409,str(error)) from error


def accepted_child(service,job):
    current=service.store.job(job['id'])
    child=service.store.job(current.get('ninja_active_child',''))
    return child if child and child['parameters'].get('ninja_sequence_owner')==job['id'] and child.get('prompt_id') and child['status'] not in {'completed','failed','cancelled'} else None


def stopped(service,job):
    if service.store.job(job['id']).get('ninja_stop_requested'):
        if accepted_child(service,job):return False  # Reconcile the accepted shot before stopping.
        for child in service.store.unfinished_jobs():
            if child['parameters'].get('ninja_sequence_owner')==job['id'] and child['status']=='queued' and not child.get('prompt_id'):
                service.store.update(child['id'],status='cancelled',stage='Detenido antes de enviar el plano',progress=None)
        service.store.update(job['id'],status='cancelled',stage='Secuencia detenida; los planos terminados se conservan',progress=None)
        return True
    return False


async def run_child(service,parent,index,p):
    jid=child_id(parent['id'],index)
    child=service.store.create_job(jid,jid,{**p,'ninja_sequence_owner':parent['id'],'ninja_shot_index':index})
    if child['status'] not in FINAL:
        service.store.update(parent['id'],status='running',stage=f'Procesando plano {index+1}',ninja_active_child=jid,progress=None)
        # The parent owns the engine lease. Acquiring it again would deadlock.
        await service._run(child)
        child=service.store.job(jid)
    if child['status']=='submission_unknown':
        service.store.update(parent['id'],status='submission_unknown',stage=f'Plano {index+1}: envío pendiente de confirmar',error=child.get('error'))
        return None
    if child['status']=='queued':
        service.store.update(parent['id'],status='queued',stage='Esperando el motor para continuar');return None
    if child['status']!='completed':raise ValueError(f'Plano {index+1}: '+(child.get('error') or child['status']))
    return child


async def process(service,job):
    p=job['parameters'];op=p['operation'];folder=service.root/'jobs'/job['id'];folder.mkdir(parents=True,exist_ok=True)
    if stopped(service,job):return
    src,ref=await asyncio.to_thread(ninja.validate_inputs,service,p)
    if op=='ninja_shots_plan':
        path=folder/'facial-plan.json'
        if path.exists():plan=json.loads(path.read_text(encoding='utf-8'))
        else:
            service.store.update(job['id'],status='preparing',stage='Detectando cortes del vídeo')
            plan=await media(service,job,'facial-plan',dict(source=str(src),source_sha256=p['ninja_source_sha256'],
                frames=p['frames'],start_frame=p['start_frame'],width=p['width'],height=p['height'],job_id=job['id'],face_python=service.settings.get('face_python'),sequence_version=p.get('ninja_segments_version')))
            _save(path,plan)
        for s in plan['segments']:
            clip=service.root/'inputs'/(s['video_id']+'.mp4')
            if not clip.exists():shutil.copy2(s['path'],clip)
            if digest(clip)!=s['sha256']:raise ValueError('Cambió un plano preparado.')
            if not service.store.asset(s['video_id']):service.store.add_asset(dict(id=s['video_id'],filename=clip.name,kind='video',
                name=f'Rostro · Plano {s["index"]+1} · {(p["start_frame"]+s["start"])/24:.2f} s',
                width=p['width'],height=p['height'],duration=(s['render_frames'] if p.get('ninja_segments_version') else s['keep'])/24,sha256=s['sha256'],sequence_internal=bool(p.get('ninja_segments_version'))))
        await asyncio.to_thread(ninja.validate_inputs,service,p)
        if stopped(service,job):return
        return service.store.update(job['id'],status='completed',stage=f'{len(plan["segments"])} planos detectados · Marca el rostro en cada uno',
            ninja_plan_sha256=digest(path),progress=None)
    plan_job,plan=await asyncio.to_thread(load_plan,service,job)
    choices=selections(plan,p['ninja_selections']);outputs=[];previous_render=None
    masks=None
    if op=='ninja_shots_render':
        masks=get_job(service,p['ninja_masks_id'],'ninja_shots_prepare')
        await asyncio.to_thread(checked_masks,service,masks,p['ninja_shots_request']['approvals'])
    for s,choice in zip(plan['segments'],choices):
        if stopped(service,job):return
        if choice['passthrough']:
            outputs.append(dict(path=str(ninja.asset_path(service,s['video_id'],'video')),sha256=s['sha256'],keep=s['keep'],context_frames=s.get('context_frames',0)));previous_render=None;continue
        if op=='ninja_shots_prepare':
            cp=dict(video_id=s['video_id'],reference_id=p['reference_id'],recipe='w2',mode=p.get('mode','head'),start_seconds=0,start_frame=0,
                frames=s['render_frames'] if p.get('ninja_segments_version') else s['keep'],render_frames=s['render_frames'] if p.get('ninja_segments_version') else render_frames(s['keep']),x=choice['x'],y=choice['y'],seed=p['seed'],engine='h3_ninja',
                operation='ninja_prepare',ninja_version=ninja.VERSION,width=p['width'],height=p['height'],resolution=p['resolution'],
                source_width=p['width'],source_height=p['height'],steps=8,preserve_face=False,prompt=ninja.prompt(p.get('mode','head')),
                ninja_source_sha256=s['sha256'],ninja_reference_sha256=p['ninja_reference_sha256'])
            cp['ninja_request']={k:cp[k] for k in ('video_id','reference_id','recipe','mode','start_seconds','frames','x','y','seed')}
        else:
            mask=service.store.job(child_id(masks['id'],s['index']))
            cp={k:v for k,v in mask['parameters'].items() if k not in ('ninja_sequence_owner','ninja_shot_index')}
            cp.update(operation='ninja_render',ninja_parent_id=mask['id'],ninja_mask_sha256=mask['ninja_mask_sha256'],
                ninja_head_motion=p['ninja_head_motion'],ninja_request={'mask_sha256':mask['ninja_mask_sha256'],'head_motion':p['ninja_head_motion']})
        if p.get('ninja_segments_version'):
            cp.update(sequence_index=s['index'],sequence_shot=s['shot'],sequence_start_frame=s['input_start'],sequence_input_keep=s['input_keep'],sequence_keep=s['keep'],sequence_context=s['context_frames'],sequence_guide_version=p['ninja_segments_version'])
            if op=='ninja_shots_render' and s['context_frames'] and previous_render:
                pp=previous_render['parameters']
                cp.update(sequence_previous_id=previous_render['id'],sequence_previous_frame=pp['sequence_context']+pp['sequence_keep']-1)
            if cp.get('mode')=='body':cp['ninja_head_motion']='original'
        child=await run_child(service,job,s['index'],cp)
        if not child:return
        if op=='ninja_shots_render':
            output=service.root/'results'/(child['id']+'.mp4')
            if not child.get('result_available') or digest(output)!=child.get('ninja_result_sha256'):raise ValueError('Cambió el resultado de un plano.')
            outputs.append(dict(path=str(output),sha256=child['ninja_result_sha256'],keep=s['keep'],context_frames=s.get('context_frames',0)));previous_render=child
    if stopped(service,job):return
    if op=='ninja_shots_prepare':
        return service.store.update(job['id'],status='completed',stage='Máscaras listas · Revisa cada plano antes de generar',progress=None)
    checkpoint=folder/'facial-result.json'
    if checkpoint.exists():result=json.loads(checkpoint.read_text(encoding='utf-8'))
    else:
        if digest(Path(plan['normalized']))!=plan['normalized_sha256']:raise ValueError('Cambió el intervalo original para el audio.')
        from .continuity import VERSION as CONTEXT_VERSION
        policy={'chunk_frames':120,'continuity_version':CONTEXT_VERSION} if p.get('ninja_segments_version') else {'scene_cuts':True}
        result=await media(service,job,'join',dict(frames=p['frames'],width=p['width'],height=p['height'],**policy,
            segments=outputs,audio_source=plan['normalized'],audio_source_sha256=plan['normalized_sha256']))
        _save(checkpoint,result)
    await asyncio.to_thread(checked_masks,service,masks,p['ninja_shots_request']['approvals'])
    target=service.root/'results'/(job['id']+'.mp4');target.parent.mkdir(exist_ok=True)
    if digest(Path(result['path']))!=result['sha256']:raise ValueError('Cambió el montaje guardado.')
    if target.exists() and digest(target)!=result['sha256']:raise ValueError('Ya existe otra copia; se conserva.')
    if stopped(service,job):return
    if not target.exists():shutil.copy2(result['path'],target)
    service.store.update(job['id'],status='completed',stage='Refinamiento por planos listo · Revisa el resultado',result_available=True,
        ninja_result_sha256=result['sha256'],scene_resolution=p['resolution'],progress=None)
