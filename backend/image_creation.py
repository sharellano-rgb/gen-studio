"""Pinned text-to-image MOSS BASE recipe, with durable accepted-graph recovery."""
import json
from . import image_profiles as profiles, image_realism as realism
from .head import digest
from .job_cancel import checkpoint

VERSION='moss-image-base8-native-v1'
PHASE='MOSS-Image-BASE'
FIELDS=('edit_profile','mystic_strength','distill_strength','edit_sampler','edit_scheduler',
    'generation_profile_version','base_profile_weights','mystic_trigger','image_version',
    'prompt','negative_prompt','steps','cfg','seed','quality','format','resolution')

def bind(p):
    profiles.verify_weights()
    return realism.bind({**p,'image_version':VERSION,'model':'MOSS-Image-BASE',
        'generation_profile_version':VERSION,'base_profile_weights':profiles.pins(),
        'mystic_trigger':profiles.MYSTIC_TRIGGER})

def verify(p,verify_files=True):
    if (p.get('image_version')!=VERSION or p.get('generation_profile_version')!=VERSION or
        p.get('base_profile_weights')!=profiles.pins() or p.get('mystic_trigger')!=profiles.MYSTIC_TRIGGER):
        raise ValueError('La receta MOSS BASE no coincide con los modelos del trabajo guardado.')
    if verify_files:profiles.verify_weights()
    realism.verify_binding(p,verify_files)

def binding(p):return {**{key:p[key] for key in FIELDS},**realism.binding(p)}

def graph(p,ident):
    from .image_generation import CLIP,VAE,SIZES
    def n(kind,**inputs):return {'class_type':kind,'inputs':inputs}
    w,h=SIZES[p['quality']][p['format']]
    text=(profiles.MYSTIC_TRIGGER+' ' if p['mystic_strength']>0 else '')+p['prompt']
    g={
        'model':n('UNETLoader',unet_name=profiles.BASE,weight_dtype='default'),
        'clip':n('CLIPLoader',clip_name=CLIP,type='moss_image',device='default'),
        'vae':n('VAELoader',vae_name=VAE),
        'distill':n('LoraLoaderModelOnly',model=['model',0],lora_name=profiles.DISTILL,strength_model=p['distill_strength']),
        'mystic':n('LoraLoaderModelOnly',model=['distill',0],lora_name=profiles.MYSTIC,strength_model=p['mystic_strength']),
        'positive':n('CLIPTextEncode',clip=['clip',0],text=text),
        'negative':n('CLIPTextEncode',clip=['clip',0],text=p['negative_prompt']),
        'latent':n('EmptyFlux2LatentImage',width=w,height=h,batch_size=1),
        'sample':n('KSampler',model=['mystic',0],positive=['positive',0],negative=['negative',0],latent_image=['latent',0],
            seed=p['seed'],steps=p['steps'],cfg=p['cfg'],sampler_name=p['edit_sampler'],scheduler=p['edit_scheduler'],denoise=1.0),
        'decode':n('VAEDecode',samples=['sample',0],vae=['vae',0]),
        'save':n('SaveImage',images=['decode',0],filename_prefix=f'gen_studio/{ident}/moss/base'),
    }
    realism.apply(g,p,target='sample')
    return g

def prepare(service,job):
    p=job['parameters'];folder=service.root/'jobs'/job['id']
    current=service.store.job(job['id'])
    accepted=current.get('guided_phase')==PHASE and bool(current.get('prompt_id'))
    done=(folder/(PHASE+'-complete.json')).is_file()
    context=folder/'base-image-context.json';workflow=folder/(PHASE+'-workflow.api.json')
    if (accepted or done) and (not context.is_file() or not workflow.is_file()):
        raise ValueError('Falta el registro de la generación BASE aceptada; no se enviará de nuevo.')
    checkpoint(service,job['id']);verify(p,not (accepted or done))
    if context.is_file():
        saved=json.loads(context.read_text(encoding='utf-8'))
        if saved['binding']!=binding(p) or not workflow.is_file() or saved['graph_sha256']!=digest(workflow):
            raise ValueError('Cambió la receta o el gráfico MOSS BASE guardado.')
        return json.loads(workflow.read_text(encoding='utf-8'))
    from .image_edit import save_json
    result=graph(p,job['id']);save_json(workflow,result)
    save_json(context,{'binding':binding(p),'graph_sha256':digest(workflow)})
    return result
