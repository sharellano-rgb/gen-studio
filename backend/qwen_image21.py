"""Qwen Image 2.1: SimpliGen recipe under the shared image-job coordinator.

The editing latent comes from TextEncodeQwenImage21 output 2, as in the
installed official Comfy template, to preserve reference-1 aspect/placement.
"""
import asyncio
import json
import math
import shutil
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import UUID, uuid4, uuid5
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .head import digest
from .media import probe
from .workflows import check_graph
from .guided_head import phase
from .job_cancel import checkpoint, thread_checkpoint
from .image_edit import checked_asset, save_json
from . import qwen_image21_upscale as upscale

VERSION='qwen-image-21-native-v1'
PHASE='Qwen-Image-2.1'
MODEL='qwen_image_2.1_int8_convrot.safetensors'
CLIP='qwen3vl_8b_int8_convrot.safetensors'
VAE='qwen_image_2.1_vae_bf16.safetensors'
LICENSE_URL='https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE'
NEGATIVE='artifacts, gpt-image, washed-out colors, low quality, low resolution, AI slop, deviantart, sloppy lines, rough sketch, blurry, indistinct, missing fingers, badly drawn hands'
SIZES={'standard':{'square':(1024,1024),'portrait':(896,1152),'landscape':(1152,896),'vertical':(768,1344),'wide':(1344,768)},
       'high':{'square':(1440,1440),'portrait':(1280,1632),'landscape':(1632,1280),'vertical':(1088,1920),'wide':(1920,1088)}}
RECIPE={'version':VERSION,'model':MODEL,'clip':CLIP,'vae':VAE,'sampler':'seeds_2','scheduler':'sgm_uniform',
        'apg':[1.,10.,.3],'fresca':[1.,2.,8],'cache':['auto','default'],'denoise':1.,
        'generation_template_sha256':'5f2644be391dea7d21d1f129cf9211a80f4973446eb300ed9ed89461496ecbf2',
        'edit_template_sha256':'4f8c95512c57408746a2c1c3e227cf58abcc54b8f2c77182801c1226c1534492',
        'edit_latent':'TextEncodeQwenImage21 output 2; reference-1 geometry'}

class Request(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_key:UUID
    image_model:Literal['qwen_image21']='qwen_image21'
    mode:Literal['generate','edit','upscale']='generate'
    prompt:str=Field(default='',max_length=12000)
    negative_prompt:str=Field(default='',max_length=3000)
    format:Literal['square','portrait','landscape','vertical','wide']='square'
    quality:Literal['standard','high']='standard'
    steps:int=Field(default=20,ge=4,le=80,strict=True)
    cfg:float=Field(default=3.,ge=1,le=6,allow_inf_nan=False)
    seed:int=Field(ge=0,le=9007199254740991,strict=True)
    edit_reference_id:UUID|None=None
    extra_reference_ids:list[UUID]=Field(default_factory=list,max_length=3)
    upscale_strength:float|None=Field(default=None,ge=0.1,le=1.5,allow_inf_nan=False)
    upscale_denoise:float|None=Field(default=None,ge=0.3,le=1.,allow_inf_nan=False)

    @field_validator('prompt')
    @classmethod
    def nonblank(cls,value):
        value=value.strip()
        return value

    @model_validator(mode='after')
    def references(self):
        if self.mode!='upscale' and len(self.prompt)<3:raise ValueError('Describe la imagen con al menos 3 caracteres.')
        if self.mode=='upscale':
            if self.extra_reference_ids:raise ValueError('Upscale utiliza una sola imagen de origen.')
            if 'steps' not in self.model_fields_set:self.steps=25
            if 'cfg' not in self.model_fields_set:self.cfg=1.
            if 'quality' not in self.model_fields_set:self.quality='high'
            if not 12<=self.steps<=40 or self.cfg>4:raise ValueError('Upscale admite de 12 a 40 pasos y adherencia de 1 a 4.')
            self.upscale_strength=1. if self.upscale_strength is None else self.upscale_strength
            self.upscale_denoise=.8 if self.upscale_denoise is None else self.upscale_denoise
        elif self.upscale_strength is not None or self.upscale_denoise is not None:
            raise ValueError('Los ajustes de upscale solo se aplican en ese modo.')
        if self.mode in ('edit','upscale'):
            if self.edit_reference_id is None:raise ValueError('Selecciona la imagen principal para editar.')
            ids=[self.edit_reference_id,*self.extra_reference_ids]
            if len(set(ids))!=len(ids):raise ValueError('Selecciona referencias distintas.')
        elif self.edit_reference_id or self.extra_reference_ids:
            raise ValueError('Las referencias se utilizan en Editar imagen.')
        return self


def dimensions(width,height,quality):
    resolution=1024 if quality=='standard' else 1440
    ratio=width/height
    values=tuple(max(32,round(math.sqrt(resolution**2*r)/32)*32) for r in (ratio,1/ratio))
    if any(n<64 or n>4096 for n in values):raise ValueError('Esta proporción es demasiado extrema. Usa otra imagen o recórtala.')
    return values


def graph(p,ident,names=None):
    if p['mode']=='upscale':return upscale.graph(p,ident,names or ['pending-upload.png'],MODEL,CLIP,VAE)
    def n(kind,**inputs):return {'class_type':kind,'inputs':inputs}
    editing=p['mode']=='edit'
    g={'model':n('UNETLoader',unet_name=MODEL,weight_dtype='default'),
       'clip':n('CLIPLoader',clip_name=CLIP,type='qwen_image',device='default'),
       'vae':n('VAELoader',vae_name=VAE),
       'negative':n('StringConcatenate',string_a=NEGATIVE,string_b=p['negative_prompt'],delimiter=', '),
       'encode':n('TextEncodeQwenImage21',clip=['clip',0],vae=['vae',0],prompt=p['prompt'],negative_prompt=['negative',0],resolution=1024 if p['quality']=='standard' else 1440),
       'apg':n('APG',model=['model',0],eta=1.,norm_threshold=10.,momentum=.3),
       'fresca':n('FreSca',model=['apg',0],scale_low=1.,scale_high=2.,freq_cutoff=8)}
    if editing:
        names=names or ['pending-upload.png']*(1+len(p.get('extra_reference_ids',[])))
        if len(names)!=1+len(p.get('extra_reference_ids',[])):raise ValueError('Falta una referencia de edición.')
        for index,name in enumerate(names,1):
            g[f'reference_{index}']=n('LoadImage',image=name)
            g['encode']['inputs'][f'images.image_{index}']=[f'reference_{index}',0]
        g['cache']=n('QwenImage21Cache',model=['fresca',0],device='auto',dtype='default')
        model=['cache',0];latent=['encode',2]
    else:
        g['latent']=n('EmptyLatentImage',width=p['width'],height=p['height'],batch_size=1)
        model=['fresca',0];latent=['latent',0]
    g['sample']=n('KSampler',model=model,positive=['encode',0],negative=['encode',1],latent_image=latent,
        seed=p['seed'],steps=p['steps'],cfg=p['cfg'],sampler_name='seeds_2',scheduler='sgm_uniform',denoise=1.)
    g['decode']=n('VAEDecode',samples=['sample',0],vae=['vae',0])
    g['save']=n('SaveImage',images=['decode',0],filename_prefix=f'gen_studio/{ident}/qwen_image21/image')
    return g


def bind(service,p):
    refs=[]
    if p['mode'] in ('edit','upscale'):
        for ident in [p['edit_reference_id'],*p['extra_reference_ids']]:
            asset,path,w,h=checked_asset(service,ident)
            refs.append({'id':str(ident),'sha256':asset['sha256'],'bytes':asset['bytes'],'width':w,'height':h})
        w,h=(upscale.dimensions if p['mode']=='upscale' else dimensions)(refs[0]['width'],refs[0]['height'],p['quality'])
    else:w,h=SIZES[p['quality']][p['format']]
    enlarged=p['mode']=='upscale'
    return {**p,'prompt':p['prompt'] or 'Ampliar la imagen conservando su composición.',
        'engine':'qwen_image21','operation':'image_generation','image_version':upscale.VERSION if enlarged else VERSION,'model':'Qwen Image 2.1',
        'width':w,'height':h,'resolution':f'{w}x{h}','qwen_image_recipe':{**upscale.RECIPE,'model':MODEL,'clip':CLIP,'vae':VAE} if enlarged else RECIPE,'qwen_image_references':refs,
        **({'edit_reference_width':refs[0]['width'],'edit_reference_height':refs[0]['height']} if refs else {})}


def binding(p):
    enlarged=p.get('mode')=='upscale'
    version=upscale.VERSION if enlarged else VERSION
    recipe={**upscale.RECIPE,'model':MODEL,'clip':CLIP,'vae':VAE} if enlarged else RECIPE
    if p.get('image_version')!=version or p.get('qwen_image_recipe')!=recipe:raise ValueError('La receta de Qwen Image 2.1 cambió; crea una nueva imagen.')
    fields=('image_version','mode','prompt','negative_prompt','format','quality','steps','cfg','seed','width','height','qwen_image_recipe','qwen_image_references')
    if enlarged:fields+=('upscale_strength','upscale_denoise')
    return {k:p[k] for k in fields}


def copies(service,job,folder,recover=False):
    p=job['parameters'];result=[]
    for index,ref in enumerate(p['qwen_image_references'],1):
        meta=folder/f'qwen-source-{index}.json'
        if meta.exists():
            value=json.loads(meta.read_text(encoding='utf-8'));path=(folder/value['filename']).resolve()
            if not path.is_relative_to(folder.resolve()) or value['reference']!=ref:raise ValueError('Cambió la referencia preparada de Qwen.')
        else:
            if recover:raise ValueError('Falta una referencia de la generación aceptada; no se vuelve a preparar.')
            a,source,w,h=checked_asset(service,ref['id'])
            if (a['sha256'],a['bytes'],w,h)!=(ref['sha256'],ref['bytes'],ref['width'],ref['height']):raise ValueError('La referencia cambió después de poner la edición en cola.')
            path=folder/(f'qwen-source-{index}'+source.suffix.lower())
            if not path.exists():
                tmp=path.with_suffix(path.suffix+'.part');shutil.copyfile(source,tmp)
                if digest(tmp)!=ref['sha256']:raise ValueError('No coincide la copia de la referencia.')
                tmp.replace(path)
            if path.stat().st_size!=ref['bytes'] or digest(path)!=ref['sha256']:raise ValueError('La copia de la referencia cambió.')
            save_json(meta,{'filename':path.name,'reference':ref})
        if not path.is_file() or path.stat().st_size!=ref['bytes'] or digest(path)!=ref['sha256']:raise ValueError('Cambió la copia de la referencia preparada.')
        result.append(path)
    return result


async def prepare(service,job):
    p=job['parameters'];ident=job['id'];folder=service.root/'jobs'/ident;folder.mkdir(parents=True,exist_ok=True)
    pinned=binding(p);current=service.store.job(ident)
    accepted=current.get('guided_phase')==PHASE and bool(current.get('prompt_id'))
    done=(folder/(PHASE+'-complete.json')).is_file()
    context_path=folder/'qwen-context.json';graph_path=folder/(PHASE+'-workflow.api.json')
    if (accepted or done) and (not context_path.is_file() or not graph_path.is_file()):raise ValueError('Falta el gráfico aceptado de Qwen; no se vuelve a enviar.')
    paths=await thread_checkpoint(service,ident,copies,service,job,folder,bool(accepted or done))
    if context_path.is_file():
        saved=json.loads(context_path.read_text(encoding='utf-8'))
        if saved.get('binding')!=pinned or not graph_path.is_file() or digest(graph_path)!=saved.get('graph_sha256'):raise ValueError('Cambió el gráfico guardado de Qwen.')
        return json.loads(graph_path.read_text(encoding='utf-8'))
    uploads=folder/'qwen-uploads.json'
    if uploads.exists():
        uploaded=json.loads(uploads.read_text(encoding='utf-8'))
        if uploaded['binding']!=pinned:raise ValueError('La carga corresponde a otra edición.')
        names=uploaded['names']
    else:
        names=[]
        for path in paths:
            checkpoint(service,ident)
            with path.open('rb') as stream:
                uploaded=await service.post(service.settings['comfy_url'],'/upload/image',files={'image':(path.name,stream)},data={'overwrite':'false','subfolder':f'gen_studio/{ident}/qwen_image21'})
            name='/'.join(v for v in (uploaded.get('subfolder',''),uploaded['name']) if v)
            parts=PurePosixPath(name.replace('\\','/'))
            if parts.is_absolute() or '..' in parts.parts or ':' in name:raise ValueError('El motor devolvió una ruta inválida.')
            names.append(name)
            checkpoint(service,ident)
        save_json(uploads,{'binding':pinned,'names':names})
    g=graph(p,ident,names)
    save_json(graph_path,g);save_json(context_path,{'binding':pinned,'graph_sha256':digest(graph_path)})
    checkpoint(service,ident)
    return g


def publish(service,job,rendered):
    p=job['parameters'];binding(p)
    folder=(service.root/'jobs'/job['id']).resolve();source=Path(rendered['path']).resolve()
    if not source.is_relative_to(folder) or digest(source)!=rendered['sha256']:raise ValueError('Cambió la salida de Qwen.')
    info=probe(source)
    if (info['width'],info['height'],info['codec'])!=(p['width'],p['height'],'png'):raise ValueError('Qwen no devolvió el tamaño PNG esperado.')
    copies(service,job,folder,recover=True)
    aid=str(uuid5(UUID(job['id']),p['image_version']));target=service.root/'inputs'/(aid+'.png');target.parent.mkdir(exist_ok=True)
    if target.exists():
        if digest(target)!=rendered['sha256']:raise ValueError('Ya existe una imagen diferente con este identificador.')
    else:
        tmp=target.with_suffix('.qwen.part');shutil.copyfile(source,tmp)
        if digest(tmp)!=rendered['sha256']:raise ValueError('La copia de la imagen no coincide.')
        tmp.replace(target)
    asset={'id':aid,'kind':'image','name':('Qwen Upscale 2.1' if p['mode']=='upscale' else 'Qwen Edit 2.1' if p['mode']=='edit' else 'Qwen Image 2.1')+' · '+job['id'][:8]+'.png',
        'filename':target.name,'bytes':target.stat().st_size,'sha256':rendered['sha256'],**info,
        'generated_image':{'job_id':job['id'],'model':'Qwen Image 2.1','version':p['image_version'],'mode':p['mode'],'references':p['qwen_image_references']}}
    old=service.store.asset(aid)
    if old and old!=asset:raise ValueError('El registro de esta imagen cambió.')
    if not old:service.store.add_asset(asset)
    return asset


async def process(service,job):
    if job['parameters'].get('mode')=='upscale':await thread_checkpoint(service,job['id'],upscale.verify_lora)
    g=await prepare(service,job)
    rendered=await phase(service,job,PHASE,g,'save','.png')
    if rendered is None:return
    asset=await thread_checkpoint(service,job['id'],publish,service,job,rendered)
    service.store.update(job['id'],status='completed',stage='Imagen lista · Qwen Image 2.1',result_available=True,progress=None,image_asset_id=asset['id'],image_result_sha256=asset['sha256'])


async def preflight(service,mode='generate'):
    p={'mode':mode,'prompt':'A ceramic teapot on a wooden table.','negative_prompt':'','quality':'standard','steps':20,'cfg':3.,'seed':42,'width':1024,'height':1024}
    if mode=='upscale':
        try:await asyncio.to_thread(upscale.verify_lora)
        except ValueError as error:return {'ok':False,'errors':[str(error)]}
        p.update(steps=25,cfg=1.,upscale_strength=1.,upscale_denoise=.8)
    value=await check_graph(service.client,service.settings['comfy_url'],graph(p,'preflight'))
    if mode=='upscale':return {**value,'model':'Qwen Image 2.1 · Detail Enhancer','version':upscale.VERSION,'mode':mode,'default_steps':25,'default_cfg':1,'max_references':1,'megapixels':[2,4],'lora_url':upscale.MODEL_URL,'license_url':LICENSE_URL,'license_scope':'research-evaluation-noncommercial'}
    return {**value,'model':'Qwen Image 2.1','version':VERSION,'mode':mode,'default_steps':20,'default_cfg':3,'sizes':SIZES,'max_references':4,
        'preserve_aspect':mode=='edit','license_url':LICENSE_URL,'license_scope':'research-evaluation-noncommercial'}


def register(app,settings):
    @app.get('/api/images/qwen21/preflight')
    async def check(mode:Literal['generate','edit','upscale']='generate'):
        try:return await preflight(app.state.jobs,mode)
        except Exception as error:raise HTTPException(503,'No se pudo comprobar Qwen Image 2.1 en el motor local.') from error

    @app.post('/api/images/qwen21/generate',status_code=202)
    async def generate(body:Request):
        if not settings['generation_enabled']:raise HTTPException(503,'Generación desactivada.')
        service=app.state.jobs;requested=body.model_dump(mode='json');key=requested.pop('request_key')
        if requested['mode']!='upscale':
            requested.pop('upscale_strength');requested.pop('upscale_denoise')
        async with service.lock:
            with service.store.connect() as db:row=db.execute('SELECT state FROM jobs WHERE request_key=?',(key,)).fetchone()
            if row:
                old=json.loads(row[0]);p=old['parameters']
                if p.get('engine')!='qwen_image21' or p.get('qwen_image_request')!=requested:raise HTTPException(409,'Esta clave ya se usó con otros parámetros.')
                return service.store.job(old['id'])
            try:p=await asyncio.to_thread(bind,service,requested)
            except (ValueError,OSError,KeyError) as error:raise HTTPException(422,str(error)) from error
            try:
                if requested['mode']=='upscale':await asyncio.to_thread(upscale.verify_lora)
                checked=await check_graph(service.client,service.settings['comfy_url'],graph(p,'preflight'))
            except Exception as error:raise HTTPException(503,'No se pudo comprobar Qwen Image 2.1.') from error
            if not checked['ok']:raise HTTPException(503,' · '.join(checked['errors']))
            p['qwen_image_request']=requested
            job=service.store.create_job(str(uuid4()),key,p)
        service.wake.set();return job
