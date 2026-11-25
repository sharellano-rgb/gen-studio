"""Local Qwen Image 2.1 Edit 4MP stage, private and resumable within Alisson."""
import asyncio
import json
import shutil
from pathlib import Path
from uuid import UUID, uuid5

from .head import digest
from .media import probe
from .workflows import ROOT, check_graph
from .motion_qwen import MODELS
from .guided_head import phase
from . import prompt_language

VERSION = 'alisson-qwen-sheet-4mp-1'
PRESET = 'Qwen Image 2.1 Edit 4MP'
PHASE = 'character-sheet-4mp'
WIDTH = HEIGHT = 2048
VENDOR = ROOT/'workflows/vendor/qwen-image-21-4mp/image_qwen_image_21_edit_4mp.template.json'
VENDOR_SHA = '1853aba2261c85b8c29e2d393f1a1413a8b268571dc1160e9a5d060d9970731e'
PROMPT = (
    'Create a clean photographic character reference sheet of the ONE character in <image1>. '
    'Use a square white canvas. The LEFT forty percent contains ONE large full-body front view, '
    'with the complete head, hair, hands and shoes visible and comfortable white margins. '
    'The RIGHT sixty percent contains exactly THREE large head-and-shoulders close-ups in a '
    'vertical column: front view at the top, true left profile in the middle, true right profile '
    'at the bottom. Keep the entire nose, ears, chin and hairstyle inside each close-up. '
    'All four views depict the exact SAME character, age, facial structure, nose, eyes, jaw, '
    'skin tone, hairstyle, facial hair, physique and proportions as the reference photograph. '
    'Preserve the exact original outfit, garment colors, fit, footwear and accessories from '
    '<image1> in every view. Keep a relaxed neutral expression, soft even studio illumination, '
    'realistic skin texture and the visual style of the original character. Do not beautify, '
    'change age, change ethnicity, redesign clothes or add accessories. No text, labels, '
    'watermarks, decorative borders, props or background scenery. Separate the views with '
    'white space; never overlap bodies or faces. These are views of one identity, not a group.'
)
FACE_PROMPT = (' <image2> is an additional facial reference of the SAME character. It may '
    'contain several views of that one face. Use its facial details and hairstyle in all '
    'four views; take body proportions and the complete outfit only from <image1>.')


def graph(seed, reference, ident, face_reference=None, catalog=None):
    if digest(VENDOR) != VENDOR_SHA:
        raise ValueError('Cambió la copia del preset Qwen Image 2.1 Edit 4MP.')
    values = {**MODELS, 'width':WIDTH, 'height':HEIGHT, 'steps':25, 'cfg':1,
              'seed':seed, 'prompt':PROMPT+(FACE_PROMPT if face_reference else ''),
              'negative_prompt':'', **{f'ref_image_{i}':reference for i in range(1,5)}}
    values['ref_image_2'] = face_reference or reference
    source = VENDOR.read_text(encoding='utf-8')
    for key,value in values.items():
        source = source.replace('"{{'+key+'}}"',json.dumps(value)).replace('{{'+key+'}}',json.dumps(value))
    if '{{' in source:raise ValueError('Faltan valores del preset Qwen 4MP.')
    g = json.loads(source)
    # Remove the empty user-LoRA slot and unused image slots, preserving preset sampling.
    del g['simpligen_lora_1']
    g['sg_encode']['inputs']['clip'] = ['sg_clip',0]
    g['sg_cache']['inputs']['model'] = ['sg_unet',0]
    for i in range(3 if face_reference else 2,5):
        del g[f'sgopt_ref_image_{i}__load']
        del g['sg_encode']['inputs'][f'images.image_{i}']
    g['sg_save']['inputs']['filename_prefix'] = f'gen_studio/{ident}/{PHASE}'
    if catalog:
        from .alisson_swap import model_name
        for node,field,key in [('sg_unet','unet_name','unet'),('sg_clip','clip_name','clip'),('sg_vae','vae_name','vae')]:
            g[node]['inputs'][field] = model_name(catalog,g[node]['class_type'],field,MODELS[key])
    return g


async def availability(service):
    errors=[]
    try:
        catalog=await service.get(service.settings['comfy_url'],'/object_info')
        g=graph(42,'preflight.png','preflight',catalog=catalog)
        checked=await check_graph(service.client,service.settings['comfy_url'],g)
        errors.extend(checked['errors'])
    except Exception as error:errors.append(str(error))
    return dict(available=not errors,errors=errors,width=WIDTH,height=HEIGHT,preset=PRESET,version=VERSION)


def binding(p):
    return {'version':VERSION,'preset_sha256':VENDOR_SHA,'source_sha256':p['alisson_reference_sha256'],
            'face_sha256':p.get('alisson_face_reference_sha256'),'seed':p['seed'],
            'width':WIDTH,'height':HEIGHT}


def checked_sheet(path, expected_sha):
    path=Path(path)
    if digest(path)!=expected_sha:raise ValueError('Cambió el character sheet guardado; no se sustituye la referencia.')
    info=probe(path)
    if (info['width'],info['height'])!=(WIDTH,HEIGHT) or info['codec']!='png':
        raise ValueError('El character sheet no conserva los 2048 × 2048 píxeles esperados.')
    return path,info


def publish(service,job,rendered):
    """Deterministic ID + exact-byte recovery across copy/DB/state interruption."""
    path,info=checked_sheet(rendered['path'],rendered['sha256'])
    aid=str(uuid5(UUID(job['id']),VERSION));filename=aid+'.png'
    target=service.root/'inputs'/filename;target.parent.mkdir(exist_ok=True)
    if target.exists():
        if digest(target)!=rendered['sha256']:raise ValueError('Ya existe otra hoja con este identificador.')
    else:
        temporary=target.with_suffix('.part')
        shutil.copyfile(path,temporary)
        temporary.replace(target)
    provenance={**binding(job['parameters']),'job_id':job['id'],
                'source_reference_id':job['parameters']['reference_id']}
    old=service.store.asset(aid)
    if old:
        if old.get('sha256')!=rendered['sha256'] or old.get('character_sheet')!=provenance or old.get('filename')!=filename:
            raise ValueError('El registro del character sheet no coincide con su generación.')
    else:
        service.store.add_asset({'id':aid,'kind':'image','name':'Character sheet · Qwen 4MP · '+job['id'][:8]+'.png',
            'filename':filename,'bytes':target.stat().st_size,'sha256':rendered['sha256'],
            'character_sheet':provenance,**info})
    record={'asset_id':aid,'width':WIDTH,'height':HEIGHT,'sha256':rendered['sha256'],
            'preset':PRESET,'version':VERSION,'review_required':True}
    service.store.update(job['id'],alisson_character_sheet=record)
    return target,record


async def ensure(service,job,reference,face_reference=None):
    """Never rebuild/resubmit an accepted stage; later H3 resumes require its sheet."""
    p=job['parameters'];folder=service.root/'jobs'/job['id']
    if p.get('alisson_sheet_version')!=VERSION:raise ValueError('Versión del character sheet desconocida.')
    current=service.store.job(job['id'])
    graph_path=folder/(PHASE+'-workflow.api.json');context_path=folder/(PHASE+'-context.json')
    done_path=folder/(PHASE+'-complete.json')
    later=current.get('guided_phase')=='alisson' or (folder/'alisson-complete.json').exists() or (folder/'direct-prepare.json').exists()
    accepted=current.get('guided_phase')==PHASE and current.get('prompt_id')
    if (accepted or done_path.exists() or later) and (not graph_path.is_file() or not context_path.is_file()):
        raise ValueError('Falta el registro del character sheet aceptado; no se vuelve a generar.')
    if later and not done_path.is_file():raise ValueError('Falta la hoja usada por Alisson; no se sustituye por otra.')
    if context_path.is_file():
        saved=json.loads(context_path.read_text(encoding='utf-8'))
        if saved.get('binding')!=binding(p) or not graph_path.is_file() or digest(graph_path)!=saved.get('graph_sha256'):
            raise ValueError('Cambió la referencia o el preset del character sheet guardado.')
        g=json.loads(graph_path.read_text(encoding='utf-8'))
    else:
        names=[]
        for path in [reference]+([face_reference] if face_reference else []):
            with Path(path).open('rb') as stream:
                uploaded=await service.post(service.settings['comfy_url'],'/upload/image',
                    files={'image':(Path(path).name,stream)},data={'overwrite':'false','subfolder':f'gen_studio/{job["id"]}/sheet'})
            names.append('/'.join(v for v in (uploaded.get('subfolder',''),uploaded['name']) if v))
        catalog=await service.get(service.settings['comfy_url'],'/object_info')
        g=graph(p['seed'],names[0],job['id'],face_reference=names[1] if len(names)>1 else None,catalog=catalog)
        # phase() persists this same JSON before acceptance (including ASCII escapes).
        graph_path.write_text(json.dumps(g,indent=2),encoding='utf-8')
        prompt_language.save(context_path,{'binding':binding(p),'graph_sha256':digest(graph_path)})
    rendered=await phase(service,job,PHASE,g,'sg_save','.png')
    if rendered is None:return None
    from .job_cancel import thread_checkpoint
    return await thread_checkpoint(service,job['id'],publish,service,job,rendered)
