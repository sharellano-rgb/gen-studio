"""Versioned facial-only H3 base and strict LoRA shape preflight."""
import hashlib,json,struct
from pathlib import Path
from .workflows import ROOT
from .character_update import BASE

RECIPE='ref2va-curve-lms-v1'
CURVE_BASE='minimax_h3_ref2va_pruned_int8_convrot.safetensors'
CURVE_SHA256='9255f52b6677845ad238f20dfaafa94727053694127ab7f255c048f0f9365779'
ADAPTER_PAIRS={'minimax_h3_lms_v1.0_r64.safetensors':258,
 'minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors':208,
 'minimax-h3-facial-realism-closeup-cp2000.safetensors':100}


def base_model(p):
    recipe=p.get('h3_face_recipe')
    if recipe is None:
        if p.get('h3_face_version') not in (None,'h3-face-refine-v1','h3-face-refine-v2'):
            raise ValueError('Falta la receta verificada del refinamiento facial.')
        return BASE
    if p.get('h3_face_version')!='h3-face-refine-v3' or recipe!=RECIPE or p.get('h3_face_base_model')!=CURVE_BASE or p.get('h3_face_base_sha256')!=CURVE_SHA256:
        raise ValueError('La receta facial o su modelo base no coinciden.')
    return CURVE_BASE


def header(path):
    with Path(path).open('rb') as f:
        raw=f.read(8)
        if len(raw)!=8:raise ValueError('Cabecera de modelo incompleta.')
        size=struct.unpack('<Q',raw)[0]
        if not 2<=size<=16*1024*1024:raise ValueError('Cabecera de modelo inválida.')
        return json.loads(f.read(size))


def normalized(key):
    for prefix in ('model.diffusion_model.','diffusion_model.'):
        if key.startswith(prefix):return key[len(prefix):]
    return key


def compatible_pairs(base,lora):
    base={normalized(k):v for k,v in base.items() if k!='__metadata__'}
    pairs={}
    for key,value in lora.items():
        if key=='__metadata__':continue
        key=normalized(key)
        if key.endswith('.alpha'):continue
        matched=False
        for suffix,side in [('.lora_A.weight','a'),('.lora_A.default.weight','a'),('.lora_B.weight','b'),('.lora_B.default.weight','b')]:
            if key.endswith(suffix):
                target=key[:-len(suffix)]+'.weight';pair=pairs.setdefault(target,{})
                if side in pair:raise ValueError('Par LoRA duplicado: '+target)
                pair[side]=value['shape'];matched=True;break
        if not matched:raise ValueError('Tensor LoRA no reconocido: '+key)
    if not pairs:raise ValueError('El adaptador no contiene pares LoRA.')
    errors=[]
    for key,pair in pairs.items():
        a,b=pair.get('a'),pair.get('b');shape=base.get(key,{}).get('shape')
        if not a or not b or len(a)!=2 or len(b)!=2 or a[0]!=b[1] or shape!=[b[0],a[1]]:
            errors.append(key)
    if errors:raise ValueError(f'Adaptador incompatible con el modelo base ({len(errors)} capas): '+', '.join(errors[:3]))
    return len(pairs)


def verify(p):
    if base_model(p)==BASE:return None
    spec=json.loads((ROOT/'docs/h3-face-refine/curve-recipe.json').read_text(encoding='utf-8'))
    b=spec['base']
    adapters=spec['adapters']
    if (spec.get('recipe')!=RECIPE or len(adapters)!=len(ADAPTER_PAIRS)
        or {i['filename']:i['pairs'] for i in adapters}!=ADAPTER_PAIRS
        or any(Path(i['path']).name!=i['filename'] for i in adapters)):
        raise ValueError('El manifiesto facial no contiene todos los adaptadores esperados.')
    if b['filename']!=CURVE_BASE or b['sha256']!=CURVE_SHA256 or Path(b['path']).name!=CURVE_BASE:
        raise ValueError('El manifiesto de la base facial no coincide.')
    for item in [b,*spec['adapters']]:
        path=Path(item['path'])
        if not path.is_file() or path.stat().st_size!=item['bytes']:raise ValueError('Falta un modelo facial verificado: '+path.name)
        with path.open('rb') as f:actual=hashlib.file_digest(f,'sha256').hexdigest()
        if actual!=item['sha256']:raise ValueError('El SHA-256 de un modelo facial no coincide: '+path.name)
    h=header(b['path'])
    if h.get('adaln_t_table',{}).get('shape')!=[1025,8]:raise ValueError('La base facial no tiene la arquitectura curva esperada.')
    counts={}
    for item in spec['adapters']:
        count=compatible_pairs(h,header(item['path']))
        if count!=item['pairs']:raise ValueError('Cambió el número de capas del adaptador facial.')
        counts[item['filename']]=count
    return {'recipe':RECIPE,'base_model':CURVE_BASE,'base_sha256':CURVE_SHA256,'compatible_pairs':counts,'all_pairs_compatible':True}
