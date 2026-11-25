import json
from pathlib import Path
from ..pika_h3_flex.profiles import BASE,SIGMAS,SIZES,root,inventory as common_inventory

from ..sequence_plan import MAX_FRAMES

ENGINE='pika_ref2va'
VERSION='pika-ref2va-1'
PROFILE='pika-ref2va-480p-6steps-v1'
PROFILE_768='pika-ref2va-768p-6steps-v1'
# Official Pika geometry: src/pika_h3/core/buckets.yaml, p480/p768.
# Keep the historical 480p profile and the independent I2VA SIZES untouched.
PROFILES={PROFILE:{'label':'480p · Estándar','tier':'480p','sizes':SIZES},
    PROFILE_768:{'label':'768p · Alta resolución','tier':'768p',
        'sizes':{'landscape':(1344,768),'portrait':(768,1344)}}}

def dimensions(p):
    profile=PROFILES.get(p.get('profile',PROFILE))
    if not profile or p.get('format') not in profile['sizes']:
        raise ValueError('Resolución o formato Pika no compatible.')
    w,h=profile['sizes'][p['format']]
    if p.get('resolution') and p['resolution']!=f'{w}x{h}':
        raise ValueError('La resolución no coincide con el perfil aceptado de Pika.')
    return w,h

def profile_options(formats):
    return [{'id':key,'label':value['label'],'tier':value['tier'],
        'formats':[{'id':f,'width':value['sizes'][f][0],'height':value['sizes'][f][1]} for f in formats]}
        for key,value in PROFILES.items()]


def inventory(settings):
    check=common_inventory(settings);errors=list(check['errors'])
    if not settings.get('pika_ref2va_enabled'):errors.append('Pika Ref2VA está desactivado.')
    try:
        verified=json.loads((root(settings)/'evidence/ref2va-models-verified.json').read_text(encoding='utf-8'))
        if verified['version']!=VERSION:raise ValueError('version')
        for item in verified['models']:
            path=Path(item['path']);stat=path.stat()
            if not item['hash_matches'] or stat.st_size!=item['bytes'] or stat.st_mtime_ns!=item['mtime_ns']:
                errors.append('Cambió el modelo Ref2VA verificado: '+path.name)
    except (OSError,ValueError,KeyError):errors.append('Falta verificar el inventario aislado de Ref2VA.')
    return {'ok':not errors,'errors':errors,'engine':ENGINE,'version':VERSION}

def capabilities(settings):
    formats=[f for f in settings.get('pika_ref2va_formats',['portrait']) if f in SIZES]
    return {**inventory(settings),'name':'Pika H3 Flex · Ref2VA','experimental':True,'profile':PROFILE,
        'profiles':profile_options(formats),'modalities':['object_swap','motion_transfer'],'formats':[{'id':f,'width':SIZES[f][0],'height':SIZES[f][1]} for f in formats],
        'max_frames':MAX_FRAMES,'fps':24,'steps':6,'visible_frames_per_segment':120,'qwen':True,'chat':True,
        'video_sequence':True,'continuity_guide_frames':1,'cut_reset':True,'references':{'images':1,'videos':1},
        'limits':'480p / 768p · Hasta 120 s en tramos de hasta 5 s. Revisa identidad, movimiento, fondo y uniones; el reemplazo es generativo.'}
