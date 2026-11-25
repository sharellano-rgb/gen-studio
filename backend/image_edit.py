"""Single-reference MOSS edits, with pinned inputs and accepted-graph recovery."""
import asyncio
import json
import math
import os
import shutil
from pathlib import Path, PurePosixPath

from .head import digest
from .image_profiles import MODELS_ROOT
from .job_cancel import checkpoint

VERSION = 'moss-image-edit-v1'
PHASE = 'MOSS-Image-Edit'
PATCH = 'moss_image_edit.safetensors'
PATCH_SHA256 = '17a48b8c605dcffdedb92a2b887d18cf316505be90802717669fb6556098dcdf'
PATCH_BYTES = 297382216
PLUGIN_REVISION = 'b3377be990c0fa296459a20a4503dda40c045b4b'
PATCH_PATH = MODELS_ROOT/'model_patches'/PATCH


def dimensions(width, height, quality):
    if quality not in {'standard', 'high'} or type(width) is not int or type(height) is not int or min(width,height)<=0:
        raise ValueError('No se pudo determinar el tamaño de la imagen original.')
    scale=math.sqrt((2 if quality=='high' else 1)*1024**2/(width*height))
    result=tuple(int(math.floor(v*scale/16+.5))*16 for v in (width,height))
    if any(v<64 or v>4096 for v in result):
        raise ValueError('La proporción de esta imagen es demasiado extrema. Usa una imagen que permita entre 64 y 4096 píxeles por lado.')
    return result


def checked_asset(service, ident):
    asset=service.store.asset(str(ident))
    if not asset or asset.get('kind')!='image':
        raise ValueError('Selecciona una imagen de este espacio para editar.')
    root=(service.root/'inputs').resolve();path=(root/asset['filename']).resolve()
    if not path.is_relative_to(root) or not path.is_file():raise ValueError('No se encontró la imagen original en este espacio.')
    if path.stat().st_size!=asset.get('bytes') or digest(path)!=asset.get('sha256'):
        raise ValueError('La imagen original cambió. Vuelve a cargarla antes de editar.')
    width=asset.get('display_width',asset.get('width'));height=asset.get('display_height',asset.get('height'))
    if type(width) is not int or type(height) is not int or not 0<width<=8192 or not 0<height<=8192:
        raise ValueError('Las dimensiones de la imagen original no son válidas.')
    return asset,path,width,height


def verify_patch():
    if not PATCH_PATH.is_file() or PATCH_PATH.stat().st_size!=PATCH_BYTES or digest(PATCH_PATH)!=PATCH_SHA256:
        raise ValueError('El adaptador MOSS Image Edit no está instalado o no coincide con el SHA-256 verificado.')


def bind_source(service, parameters):
    from .image_profiles import bind
    asset,path,width,height=checked_asset(service,parameters['edit_reference_id'])
    output_width,output_height=dimensions(width,height,parameters['quality'])
    verify_patch()
    return bind({**parameters,'image_version':VERSION,'model':'MOSS-Image-Edit',
        'edit_reference_sha256':asset['sha256'],'edit_reference_bytes':asset['bytes'],
        'edit_reference_width':width,'edit_reference_height':height,
        'edit_patch_sha256':PATCH_SHA256,'edit_patch_bytes':PATCH_BYTES,
        'edit_plugin_revision':PLUGIN_REVISION,'width':output_width,'height':output_height,
        'resolution':f'{output_width}x{output_height}'})


def graph(p, ident, reference_image='pending-upload.png'):
    from .image_generation import MODEL, CLIP, VAE, NSFW_LORA, NSFW_LORA_TRIGGER
    from .image_profiles import selected, BASE, DISTILL, MYSTIC, MYSTIC_TRIGGER, REFERENCE_MAX_EDGE
    def n(kind,**inputs):return {'class_type':kind,'inputs':inputs}
    g={
        'model':n('UNETLoader',unet_name=MODEL,weight_dtype='default'),
        'clip':n('CLIPLoader',clip_name=CLIP,type='moss_image',device='default'),
        'vae':n('VAELoader',vae_name=VAE),
        'edit_patch':n('ModelPatchLoader',name=PATCH),
        'edit_source':n('LoadImage',image=reference_image),
        'positive':n('CLIPTextEncode',clip=['clip',0],text=p['prompt']),
        'negative':n('CLIPTextEncode',clip=['clip',0],text=p['negative_prompt']),
        'edit':n('ApplyMossImageEdit',model=['model',0],model_patch=['edit_patch',0],vae=['vae',0],
            positive=['positive',0],negative=['negative',0],image=['edit_source',0],reference_max_edge=512,strength=p['edit_strength']),
        'slg':n('SkipLayerGuidanceDiTSimple',model=['edit',0],double_layers='',single_layers='22',start_percent=0.0,end_percent=1.0),
        'latent':n('EmptyFlux2LatentImage',width=p['width'],height=p['height'],batch_size=1),
        'sample':n('KSampler',model=['slg',0],positive=['edit',1],negative=['edit',2],latent_image=['latent',0],
            seed=p['seed'],steps=p['steps'],cfg=p['cfg'],sampler_name='euler',scheduler='normal',denoise=1.0),
        'decode':n('VAEDecode',samples=['sample',0],vae=['vae',0]),
        'save':n('SaveImage',images=['decode',0],filename_prefix=f'gen_studio/{ident}/moss/edit'),
    }
    if selected(p):
        g['model']['inputs']['unet_name']=BASE
        g['distill']=n('LoraLoaderModelOnly',model=['model',0],lora_name=DISTILL,strength_model=p['distill_strength'])
        g['mystic']=n('LoraLoaderModelOnly',model=['distill',0],lora_name=MYSTIC,strength_model=p['mystic_strength'])
        g['edit']['inputs'].update(model=['mystic',0],reference_max_edge=REFERENCE_MAX_EDGE)
        if MYSTIC_TRIGGER and float(p['mystic_strength'])>0:
            g['positive']['inputs']['text']=MYSTIC_TRIGGER+' '+p['prompt']
        g.pop('slg')
        g['sample']['inputs'].update(model=['edit',0],sampler_name=p['edit_sampler'],scheduler=p['edit_scheduler'])
    elif p.get('lora_enabled',False) and float(p.get('lora_strength',0))>0:
        strength=float(p['lora_strength'])
        g['lora']=n('LoraLoader',model=['model',0],clip=['clip',0],lora_name=NSFW_LORA,
            strength_model=strength,strength_clip=strength)
        # ApplyMossImageEdit clones its input patcher, retaining the style patches.
        g['edit']['inputs']['model']=['lora',0]
        g['positive']['inputs'].update(clip=['lora',1],text=NSFW_LORA_TRIGGER+' '+p['prompt'])
        g['negative']['inputs']['clip']=['lora',1]
    from .image_realism import apply
    apply(g,p,source=g['edit']['inputs']['model'])
    return g


def save_json(path,value):
    temporary=path.with_suffix(path.suffix+'.part')
    temporary.write_text(json.dumps(value,indent=2),encoding='utf-8');temporary.replace(path)


def binding(p):
    from .image_profiles import binding as profile_binding
    result={key:p[key] for key in ('image_version','edit_reference_id','edit_reference_sha256','edit_reference_bytes',
        'edit_reference_width','edit_reference_height','edit_patch_sha256','edit_patch_bytes','edit_plugin_revision',
        'width','height','quality','edit_strength','prompt','negative_prompt','steps','cfg','seed')}
    if p.get('lora_enabled',False) and float(p.get('lora_strength',0))>0:
        from .image_generation import NSFW_LORA, NSFW_LORA_TRIGGER
        result.update(lora_enabled=True,lora_strength=float(p['lora_strength']),
            style_lora=NSFW_LORA,style_lora_trigger=NSFW_LORA_TRIGGER)
    # Legacy accepted edits have no style fields; retain their exact binding.
    result.update(profile_binding(p))
    return result


def prepared_source(service,job,folder,verify_model=True):
    from .image_profiles import verify_binding
    p=job['parameters']
    if (p.get('image_version')!=VERSION or p.get('edit_patch_sha256')!=PATCH_SHA256 or
            p.get('edit_patch_bytes')!=PATCH_BYTES or p.get('edit_plugin_revision')!=PLUGIN_REVISION):
        raise ValueError('La versión del adaptador de edición no coincide con este trabajo.')
    if verify_model:verify_patch()
    verify_binding(p,verify_files=verify_model)
    copy_meta=folder/'edit-source.json'
    if copy_meta.is_file():
        saved=json.loads(copy_meta.read_text(encoding='utf-8'));path=(folder/saved['filename']).resolve()
        if not path.is_relative_to(folder.resolve()) or saved.get('sha256')!=p['edit_reference_sha256']:
            raise ValueError('Cambió el registro de la imagen preparada para edición.')
        if not path.is_file() or path.stat().st_size!=p['edit_reference_bytes'] or digest(path)!=p['edit_reference_sha256']:
            raise ValueError('Cambió la copia de la imagen preparada para edición.')
        return path
    asset,source,w,h=checked_asset(service,p['edit_reference_id'])
    if (asset['sha256'],asset['bytes'],w,h)!=(p['edit_reference_sha256'],p['edit_reference_bytes'],p['edit_reference_width'],p['edit_reference_height']):
        raise ValueError('Cambió la imagen seleccionada después de poner la edición en cola.')
    path=folder/('edit-source'+source.suffix.lower())
    if path.exists():
        if path.stat().st_size!=p['edit_reference_bytes'] or digest(path)!=p['edit_reference_sha256']:
            raise ValueError('Existe una copia distinta de la referencia de edición.')
    else:
        temporary=path.with_suffix(path.suffix+'.part');shutil.copyfile(source,temporary)
        if temporary.stat().st_size!=p['edit_reference_bytes'] or digest(temporary)!=p['edit_reference_sha256']:
            raise ValueError('No se pudo verificar la copia de la imagen de edición.')
        temporary.replace(path)
    save_json(copy_meta,{'filename':path.name,'sha256':p['edit_reference_sha256']})
    return path


async def prepare(service,job):
    p=job['parameters'];folder=service.root/'jobs'/job['id'];folder.mkdir(parents=True,exist_ok=True)
    current=service.store.job(job['id']);accepted=current.get('guided_phase')==PHASE and bool(current.get('prompt_id'))
    done=(folder/(PHASE+'-complete.json')).is_file()
    context_path=folder/'edit-context.json';graph_path=folder/(PHASE+'-workflow.api.json')
    if (accepted or done) and (not context_path.is_file() or not graph_path.is_file() or not (folder/'edit-source.json').is_file()):
        raise ValueError('Falta el registro de la edición aceptada; no se vuelve a enviar ni a preparar.')
    checkpoint(service,job['id'])
    source=await asyncio.to_thread(prepared_source,service,job,folder,not (accepted or done))
    if context_path.is_file():
        saved=json.loads(context_path.read_text(encoding='utf-8'))
        if saved.get('binding')!=binding(p) or not graph_path.is_file() or digest(graph_path)!=saved.get('graph_sha256'):
            raise ValueError('Cambió el gráfico o la referencia de la edición guardada.')
        return json.loads(graph_path.read_text(encoding='utf-8'))
    uploads=folder/'edit-uploads.json'
    if uploads.is_file():
        upload=json.loads(uploads.read_text(encoding='utf-8'))
        if upload.get('binding')!=binding(p):raise ValueError('La referencia cargada corresponde a otra edición.')
        name=upload['name']
    else:
        checkpoint(service,job['id'])
        with source.open('rb') as stream:
            result=await service.post(service.settings['comfy_url'],'/upload/image',files={'image':(source.name,stream)},
                data={'overwrite':'false','subfolder':f'gen_studio/{job["id"]}/moss_edit'})
        name='/'.join(v for v in (result.get('subfolder',''),result['name']) if v)
        parts=PurePosixPath(name.replace('\\','/'))
        if parts.is_absolute() or '..' in parts.parts:raise ValueError('El motor devolvió una ruta de referencia inválida.')
        save_json(uploads,{'binding':binding(p),'name':name})
    g=graph(p,job['id'],name);save_json(graph_path,g)
    save_json(context_path,{'binding':binding(p),'graph_sha256':digest(graph_path)})
    checkpoint(service,job['id'])
    return g
