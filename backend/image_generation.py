"""Private MOSS-Image-SFT jobs on the shared ComfyUI engine."""
import asyncio
import json
import shutil
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4, uuid5

from fastapi import HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .head import digest
from .media import probe
from .workflows import check_graph
from .guided_head import phase
from .job_cancel import checkpoint, thread_checkpoint

VERSION = 'moss-image-sft-native-v1'
MODEL = 'moss_image_sft_int8_convrot.safetensors'
CLIP = 'qwen3.5_9b_int8_convrot.safetensors'
VAE = 'flux2-vae.safetensors'
NSFW_LORA = 'MOSS-Image-NSFW-Uncensored-LoRA-ComfyUI.safetensors'
NSFW_LORA_TRIGGER = 'moss_xvstyle'
SIZES = {
    'standard': {'square':(1024,1024), 'portrait':(896,1152), 'landscape':(1152,896), 'vertical':(768,1344), 'wide':(1344,768)},
    'high': {'square':(1440,1440), 'portrait':(1264,1632), 'landscape':(1632,1264), 'vertical':(1088,1920), 'wide':(1920,1088)},
}

class ImageRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_key: UUID
    prompt: str = Field(min_length=3, max_length=12000)
    negative_prompt: str = Field(default='', max_length=3000)
    format: Literal['square','portrait','landscape','vertical','wide'] = 'square'
    quality: Literal['standard','high'] = 'standard'
    steps: int = Field(default=50, ge=4, le=80, strict=True)
    cfg: float = Field(default=3.0, ge=1, le=6, allow_inf_nan=False)
    seed: int = Field(ge=0, le=9007199254740991, strict=True)
    lora_enabled: bool = False
    lora_strength: float = Field(default=0.0, ge=0, le=2, allow_inf_nan=False)
    mode: Literal['generate','edit'] = 'generate'
    edit_reference_id: UUID | None = None
    edit_strength: float = Field(default=1.0, ge=.1, le=2, allow_inf_nan=False)
    edit_profile: Literal['standard','mystic_base8'] = 'standard'
    mystic_strength: float = Field(default=.7, ge=0, le=2, allow_inf_nan=False)
    distill_strength: float = Field(default=.65, ge=.1, le=2, allow_inf_nan=False)
    edit_sampler: Literal['er_sde','euler'] = 'er_sde'
    edit_scheduler: Literal['normal','sgm_uniform'] = 'sgm_uniform'
    realism_lora: Literal['none','lenovo','oldnokia','smartphone'] = 'none'
    realism_strength: float = Field(default=1.0, ge=0, le=2, allow_inf_nan=False)

    @model_validator(mode='after')
    def editing_reference(self):
        from .image_profiles import validate_selection
        validate_selection(self.mode,self.edit_profile,self.lora_enabled)
        from .image_realism import validate
        validate(self.mode,self.edit_profile,self.realism_lora)
        if self.edit_profile=='standard' and self.steps<20:
            raise ValueError('La receta estándar requiere entre 20 y 80 pasos.')
        if self.mode=='edit':
            if self.edit_reference_id is None:raise ValueError('Selecciona una imagen para editar.')
        elif self.edit_reference_id is not None:
            raise ValueError('La imagen original sólo se utiliza en el modo Editar imagen.')
        return self

    @field_validator('prompt')
    @classmethod
    def nonblank(cls, value):
        value=value.strip()
        if len(value)<3:raise ValueError('Describe la imagen con al menos 3 caracteres.')
        return value


def graph(p, ident):
    if p.get('mode')=='edit':
        from .image_edit import graph as edit_graph
        return edit_graph(p,ident)
    if p.get('edit_profile')=='mystic_base8':
        from .image_creation import graph as base_graph
        return base_graph(p,ident)
    def n(kind, **inputs):return {'class_type':kind,'inputs':inputs}
    w,h=SIZES[p['quality']][p['format']]
    use_lora=bool(p.get('lora_enabled',False)) and float(p.get('lora_strength',0.0))>0
    g={
        'model':n('UNETLoader',unet_name=MODEL,weight_dtype='default'),
        'clip':n('CLIPLoader',clip_name=CLIP,type='moss_image',device='default'),
        'vae':n('VAELoader',vae_name=VAE),
    }
    if use_lora:
        s=float(p['lora_strength'])
        g['lora']=n('LoraLoader',model=['model',0],clip=['clip',0],lora_name=NSFW_LORA,strength_model=s,strength_clip=s)
    model_ref=['lora',0] if use_lora else ['model',0]
    clip_ref=['lora',1] if use_lora else ['clip',0]
    positive_text=NSFW_LORA_TRIGGER+' '+p['prompt'] if use_lora else p['prompt']
    g.update({
        'slg':n('SkipLayerGuidanceDiTSimple',model=model_ref,double_layers='',single_layers='22',start_percent=0.0,end_percent=1.0),
        'positive':n('CLIPTextEncode',clip=clip_ref,text=positive_text),
        'negative':n('CLIPTextEncode',clip=clip_ref,text=p['negative_prompt']),
        'latent':n('EmptyFlux2LatentImage',width=w,height=h,batch_size=1),
        'sample':n('KSampler',model=['slg',0],positive=['positive',0],negative=['negative',0],latent_image=['latent',0],
            seed=p['seed'],steps=p['steps'],cfg=p['cfg'],sampler_name='euler',scheduler='sgm_uniform',denoise=1.0),
        'decode':n('VAEDecode',samples=['sample',0],vae=['vae',0]),
        'save':n('SaveImage',images=['decode',0],filename_prefix=f'gen_studio/{ident}/moss/image'),
    })
    from .image_realism import apply
    apply(g,p,target='slg',source=model_ref)
    return g


async def preflight(service,mode='generate',lora_enabled=False,lora_strength=.7,
        edit_profile='standard',mystic_strength=.7,distill_strength=.65,
        edit_sampler='er_sde',edit_scheduler='sgm_uniform',realism_lora='none',realism_strength=1.0):
    from .image_profiles import PROFILE, validate_selection
    validate_selection(mode,edit_profile,lora_enabled)
    from . import image_realism
    image_realism.validate(mode,edit_profile,realism_lora)
    p=ImageRequest(request_key=uuid4(),prompt='A quiet mountain lake at sunrise.',seed=42).model_dump(mode='json')
    p.update(lora_enabled=lora_enabled,lora_strength=lora_strength)
    if realism_lora!='none':p.update(realism_lora=realism_lora,realism_strength=realism_strength)
    if mode=='edit':p.update(mode='edit',width=1024,height=1024,edit_strength=1,steps=30,cfg=4)
    if edit_profile==PROFILE:
        p.update(edit_profile=edit_profile,mystic_strength=mystic_strength,distill_strength=distill_strength,
            edit_sampler=edit_sampler,edit_scheduler=edit_scheduler,steps=8,cfg=3)
    value=await check_graph(service.client,service.settings['comfy_url'],graph(p,'preflight'))
    value['style_lora']=bool(lora_enabled) and lora_strength>0
    if mode=='edit':
        from .image_edit import VERSION as edit_version, PATCH_SHA256, PLUGIN_REVISION
        response={**value,'mode':'edit','model':'MOSS-Image-Edit','version':edit_version,'default_steps':30,
            'default_cfg':4,'image_edit':True,'reference_required':True,'preserve_aspect':True,
            'edit_patch_sha256':PATCH_SHA256,'edit_plugin_revision':PLUGIN_REVISION}
        if edit_profile==PROFILE:
            from .image_profiles import VERSION as profile_version, pins, MYSTIC_TRIGGER, REFERENCE_MAX_EDGE
            response.update(edit_profile=PROFILE,edit_profile_version=profile_version,edit_profile_weights=pins(),
                default_steps=8,default_cfg=3,default_mystic_strength=.7,default_distill_strength=.65,
                edit_sampler=edit_sampler,edit_scheduler=edit_scheduler,mystic_trigger=MYSTIC_TRIGGER,
                edit_reference_max_edge=REFERENCE_MAX_EDGE)
        if realism_lora!='none':response.update(realism_lora=realism_lora,realism_strength=realism_strength,
            realism_version=image_realism.VERSION,realism_weights=image_realism.weights(p))
        return response
    if edit_profile==PROFILE:
        from .image_creation import VERSION as base_version
        from .image_profiles import pins,MYSTIC_TRIGGER
        response={**value,'mode':'generate','model':'MOSS-Image-BASE','version':base_version,'sizes':SIZES,
            'default_steps':8,'default_cfg':3,'text_to_image':True,'edit_profile':PROFILE,
            'generation_profile_version':base_version,'base_profile_weights':pins(),'mystic_trigger':MYSTIC_TRIGGER,
            'default_mystic_strength':.7,'default_distill_strength':.65,'edit_sampler':edit_sampler,'edit_scheduler':edit_scheduler}
        if realism_lora!='none':response.update(realism_lora=realism_lora,realism_strength=realism_strength,
            realism_version=image_realism.VERSION,realism_weights=image_realism.weights(p))
        return response
    response={**value,'mode':'generate','model':'MOSS-Image-SFT','version':VERSION,'sizes':SIZES,'default_steps':50,'text_to_image':True}
    if realism_lora!='none':
        from .image_sft_photo import VERSION as photo_version
        response.update(version=photo_version,realism_lora=realism_lora,realism_strength=realism_strength,
            realism_version=image_realism.VERSION,realism_weights=image_realism.weights(p))
    return response


def request_parameters(body):
    p=body.model_dump(mode='json');key=p.pop('request_key')
    if p['realism_lora']=='none':
        p.pop('realism_lora');p.pop('realism_strength')
    if p['edit_profile']=='standard':
        from .image_profiles import REQUEST_FIELDS
        for field in REQUEST_FIELDS:p.pop(field)
    if p['mode']=='generate':
        for field in ('mode','edit_reference_id','edit_strength'):p.pop(field)
    return key,p


def image_job(service, ident):
    job=service.store.job(str(ident))
    if not job or job['parameters'].get('operation')!='image_generation':raise HTTPException(404,'Imagen no encontrada en este espacio.')
    return job


def result_path(service, job):
    if job['status']!='completed' or not job.get('result_available'):raise ValueError('La imagen todavía no está lista.')
    asset=service.store.asset(job.get('image_asset_id'))
    if not asset or asset.get('kind')!='image' or asset.get('generated_image',{}).get('job_id')!=job['id']:
        raise ValueError('No se encontró la imagen de este trabajo.')
    root=(service.root/'inputs').resolve();path=(root/asset['filename']).resolve()
    if not path.is_relative_to(root) or not path.is_file() or digest(path)!=job.get('image_result_sha256') or asset.get('sha256')!=job.get('image_result_sha256'):
        raise ValueError('La imagen guardada cambió; se conserva el diagnóstico.')
    return path


def publish(service, job, rendered):
    source=Path(rendered['path']).resolve();folder=(service.root/'jobs'/job['id']).resolve()
    if not source.is_relative_to(folder) or digest(source)!=rendered['sha256']:raise ValueError('Cambió la salida de MOSS.')
    p=job['parameters'];editing=p.get('mode')=='edit'
    info=probe(source);w,h=(p['width'],p['height']) if editing else SIZES[p['quality']][p['format']]
    if (info['width'],info['height'])!=(w,h) or info['codec']!='png':raise ValueError('MOSS no devolvió el tamaño o formato PNG esperado.')
    base=not editing and p.get('edit_profile')=='mystic_base8'
    from .image_sft_photo import selected as sft_photo_selected
    photo=sft_photo_selected(p)
    version=p['image_version'] if editing or base or photo else VERSION
    aid=str(uuid5(UUID(job['id']),version));target=service.root/'inputs'/(aid+'.png');target.parent.mkdir(exist_ok=True)
    if target.exists():
        if digest(target)!=rendered['sha256']:raise ValueError('Existe otra imagen con este identificador.')
    else:
        temporary=target.with_suffix('.moss.part');shutil.copyfile(source,temporary)
        if digest(temporary)!=rendered['sha256']:raise ValueError('No se pudo verificar la copia de la imagen.')
        temporary.replace(target)
    asset={'id':aid,'kind':'image','name':('MOSS Edit · ' if editing else 'MOSS · ')+job['id'][:8]+'.png','filename':target.name,
        'bytes':target.stat().st_size,'sha256':rendered['sha256'],**info,
        'generated_image':{'job_id':job['id'],'model':'MOSS-Image-Edit' if editing else 'MOSS-Image-BASE' if base else 'MOSS-Image-SFT','version':version}}
    if base:
        asset['generated_image'].update(edit_profile=p['edit_profile'],generation_profile_version=p['generation_profile_version'],
            base_profile_weights=p['base_profile_weights'])
        if p.get('realism_lora','none')!='none':
            asset['generated_image'].update(realism_lora=p['realism_lora'],realism_version=p['realism_version'],
                realism_weights=p['realism_weights'],realism_strength=p['realism_strength'])
    if editing:asset['generated_image'].update(source_reference_id=p['edit_reference_id'],source_sha256=p['edit_reference_sha256'],edit_patch_sha256=p['edit_patch_sha256'])
    if editing and p.get('edit_profile')=='mystic_base8':
        asset['generated_image'].update(edit_profile=p['edit_profile'],edit_profile_version=p['edit_profile_version'],
            edit_profile_weights=p['edit_profile_weights'],edit_reference_max_edge=p['edit_reference_max_edge'])
        if p.get('realism_lora','none')!='none':
            asset['generated_image'].update(realism_lora=p['realism_lora'],realism_version=p['realism_version'],
                realism_weights=p['realism_weights'],realism_strength=p['realism_strength'])
    if (editing and not base and p.get('edit_profile','standard')=='standard' or photo) and p.get('realism_lora','none')!='none':
        asset['generated_image'].update(realism_lora=p['realism_lora'],realism_version=p['realism_version'],
            realism_weights=p['realism_weights'],realism_strength=p['realism_strength'])
    previous=service.store.asset(aid)
    if previous:
        if previous!=asset:raise ValueError('La referencia guardada no coincide con esta generación.')
    else:service.store.add_asset(asset)
    return asset


async def process(service, job):
    if job['parameters'].get('engine')=='qwen_image21':
        from .qwen_image21 import process as qwen_process
        return await qwen_process(service,job)
    p=job['parameters'];editing=p.get('mode')=='edit'
    base=not editing and p.get('edit_profile')=='mystic_base8'
    from .image_sft_photo import selected as photo_selected
    photo=photo_selected(p)
    if editing:
        from .image_edit import VERSION as edit_version, PHASE, prepare
        if p.get('image_version')!=edit_version:raise ValueError('Versión de edición de imágenes no compatible.')
    elif base:
        from .image_creation import VERSION as base_version
        if p.get('image_version')!=base_version:raise ValueError('Versión de generación BASE no compatible.')
    elif photo:
        from .image_sft_photo import VERSION as photo_version
        if p.get('image_version')!=photo_version:raise ValueError('Versión SFT fotográfica no compatible.')
    elif p.get('image_version')!=VERSION:raise ValueError('Versión de generación de imágenes no compatible.')
    ident=job['id'];folder=service.root/'jobs'/ident;folder.mkdir(parents=True,exist_ok=True)
    checkpoint(service,ident)
    if base:
        from .image_creation import prepare as base_prepare,PHASE as base_phase
        g=await asyncio.to_thread(base_prepare,service,job)
    elif photo:
        from .image_sft_photo import prepare as photo_prepare,PHASE as photo_phase
        g=await asyncio.to_thread(photo_prepare,service,job)
    else:g=await prepare(service,job) if editing else graph(p,ident)
    rendered=await phase(service,job,PHASE if editing else base_phase if base else photo_phase if photo else 'MOSS-Image-SFT',g,'save','.png')
    if rendered is None:return
    service.store.update(ident,stage='Verificando y guardando tu imagen')
    asset=await thread_checkpoint(service,ident,publish,service,job,rendered)
    service.store.update(ident,status='completed',stage='Imagen lista · '+('MOSS-Image-Edit' if editing else 'MOSS-Image-BASE' if base else 'MOSS-Image-SFT'),result_available=True,progress=None,
        image_asset_id=asset['id'],image_result_sha256=asset['sha256'])


def history(store, before):
    with store.connect() as db:
        rows=db.execute("SELECT rowid,state FROM jobs WHERE json_extract(payload,'$.operation')='image_generation' AND rowid < ? ORDER BY rowid DESC LIMIT 51",(before or 9223372036854775807,)).fetchall()
    return {'jobs':[store.project_state(json.loads(r[1])) for r in rows[:50]],'next_before':rows[49][0] if len(rows)>50 else None}


def register(app, settings):
    from .qwen_image21 import register as register_qwen
    register_qwen(app,settings)
    @app.get('/api/images/preflight')
    async def check(mode:Literal['generate','edit']='generate',lora_enabled:bool=False,
            lora_strength:float=Query(default=.7,ge=0,le=2,allow_inf_nan=False),
            edit_profile:Literal['standard','mystic_base8']='standard',
            mystic_strength:float=Query(default=.7,ge=0,le=2,allow_inf_nan=False),
            distill_strength:float=Query(default=.65,ge=.1,le=2,allow_inf_nan=False),
            edit_sampler:Literal['er_sde','euler']='er_sde',
            edit_scheduler:Literal['normal','sgm_uniform']='sgm_uniform',
            realism_lora:Literal['none','lenovo','oldnokia','smartphone']='none',
            realism_strength:float=Query(default=1.0,ge=0,le=2,allow_inf_nan=False)):
        try:
            return await preflight(app.state.jobs,mode,lora_enabled,lora_strength,edit_profile,
                mystic_strength,distill_strength,edit_sampler,edit_scheduler,realism_lora,realism_strength)
        except ValueError as e:raise HTTPException(422,str(e)) from e

    @app.get('/api/images/history')
    async def listing(before:int|None=Query(default=None,ge=1)):
        return await asyncio.to_thread(history,app.state.jobs.store,before)

    @app.post('/api/images/generate',status_code=202)
    async def generate(body:ImageRequest):
        if not settings['generation_enabled']:raise HTTPException(503,'Generación desactivada.')
        service=app.state.jobs;key,requested=request_parameters(body)
        async with service.lock:
            with service.store.connect() as db:old=db.execute('SELECT state FROM jobs WHERE request_key=?',(key,)).fetchone()
            if old:
                saved=json.loads(old[0])
                prior=saved['parameters']
                if (prior.get('operation')!='image_generation' or prior.get('engine')!='moss_image' or
                        prior.get('mode','generate')!=requested.get('mode','generate') or
                        prior.get('edit_profile','standard')!=requested.get('edit_profile','standard') or
                        prior.get('realism_lora','none')!=requested.get('realism_lora','none') or
                        any(prior.get(k)!=v for k,v in requested.items())):
                    raise HTTPException(409,'Esta clave ya se usó con otros parámetros.')
                return service.store.job(saved['id'])
            p={**requested,'engine':'moss_image','operation':'image_generation'}
            if p.get('mode')=='edit':
                from .image_edit import bind_source
                try:p=await asyncio.to_thread(bind_source,service,p)
                except (ValueError,OSError) as e:raise HTTPException(422,str(e)) from e
            else:
                w,h=SIZES[p['quality']][p['format']]
                p.update(image_version=VERSION,model='MOSS-Image-SFT',resolution=f'{w}x{h}')
                if p.get('edit_profile')=='mystic_base8':
                    from .image_creation import bind
                    try:p=await asyncio.to_thread(bind,p)
                    except (ValueError,OSError) as e:raise HTTPException(422,str(e)) from e
                elif p.get('realism_lora','none')!='none':
                    from .image_sft_photo import bind
                    try:p=await asyncio.to_thread(bind,p)
                    except (ValueError,OSError) as e:raise HTTPException(422,str(e)) from e
            job_id=str(uuid4())
            try:check=await check_graph(service.client,service.settings['comfy_url'],graph(p,job_id))
            except Exception as e:raise HTTPException(503,'No se pudo comprobar MOSS en el motor local.') from e
            if not check['ok']:raise HTTPException(503,' · '.join(check['errors']))
            job=service.store.create_job(job_id,key,p)
        service.wake.set();return job

    @app.get('/api/images/{job_id}/result')
    async def result(job_id:UUID,download:bool=False):
        service=app.state.jobs;job=image_job(service,job_id)
        try:path=await asyncio.to_thread(result_path,service,job)
        except (ValueError,OSError) as e:raise HTTPException(409,str(e)) from e
        return FileResponse(path,media_type='image/png',filename='Gen-Studio-'+('Qwen-' if job['parameters'].get('engine')=='qwen_image21' else 'MOSS-')+job['id'][:8]+'.png' if download else None,
            content_disposition_type='attachment' if download else 'inline')

    @app.get('/api/images/{job_id}/reference')
    async def reference(job_id:UUID):
        service=app.state.jobs;job=image_job(service,job_id)
        try:await asyncio.to_thread(result_path,service,job)
        except (ValueError,OSError) as e:raise HTTPException(409,str(e)) from e
        return service.store.asset(job['image_asset_id'])
