import json
from pathlib import Path

ENGINE='pika_h3_flex'
VERSION='pika-i2va-1'
PROFILE='pika-i2va-480p-5s-v1'
BASE='http://127.0.0.1:8191'
SIZES={'landscape':(832,480),'portrait':(480,832)}
PROFILE_720='pika-i2va-720p-5s-v1'
DIRECT_ENGINE='h3_i2va'
DIRECT_PROFILE='h3-i2va-480p-5s-v1'
DIRECT_PROFILE_720='h3-i2va-720p-5s-v1'
HIGH_SIZES={'landscape':(1344,768),'portrait':(768,1344)}
OUTPUT_720={'landscape':(1280,720),'portrait':(720,1280)}
PROFILES={
    PROFILE:dict(engine=ENGINE,label='480p',sizes=SIZES,output=SIZES,steps=6),
    PROFILE_720:dict(engine=ENGINE,label='720p',sizes=HIGH_SIZES,output=OUTPUT_720,steps=6),
    DIRECT_PROFILE:dict(engine=DIRECT_ENGINE,label='480p',sizes=SIZES,output=SIZES,steps=8),
    DIRECT_PROFILE_720:dict(engine=DIRECT_ENGINE,label='720p',sizes=HIGH_SIZES,output=OUTPUT_720,steps=8),
}
def dimensions(p,output=False):
    entry=PROFILES[p.get('profile',PROFILE)]
    if p.get('engine',entry['engine'])!=entry['engine']:raise ValueError('El perfil no corresponde al motor.')
    return entry['output' if output else 'sizes'][p['format']]

def engine_name(p):return 'H3 directo' if p.get('engine')==DIRECT_ENGINE else 'Pika H3 Flex'

SIGMAS='1.0, 0.972973, 0.923077, 0.8, 0.571429, 0.333333, 0.0'

def root(settings):
    return Path(settings.get('pika_h3_flex_root','D:/AI/experiments/pika-h3-flex/pilot-01')).resolve()

def inventory(settings):
    """Metadata-only: never load a model or start a process during UI polling."""
    base=root(settings);errors=[]
    if not settings.get('pika_h3_flex_enabled',False):errors.append('Pika H3 Flex está desactivado en esta instalación.')
    for name in ['python/python.exe','ComfyUI/main.py','config/launch.json','evidence/models-verified.json']:
        if not (base/name).is_file():errors.append('Falta un componente de la instalación aislada: '+name)
    if not errors:
        try:
            verified=json.loads((base/'evidence/models-verified.json').read_text(encoding='utf-8-sig'))
            for item in verified['models']:
                p=Path(item['path']);s=p.stat()
                if not item['hash_matches'] or s.st_size!=item['bytes'] or s.st_mtime_ns!=item['mtime_ns']:
                    errors.append('El archivo cambió desde la verificación SHA-256: '+p.name)
        except (OSError,ValueError,KeyError):errors.append('No se pudo comprobar el inventario de modelos.')
    return {'ok':not errors,'errors':errors,'model_hash_verification':'verified_snapshot_with_size_and_mtime_guard','engine':ENGINE,'version':VERSION}

def capabilities(settings,engine=ENGINE):
    check=inventory(settings)
    formats=[f for f in settings.get('pika_h3_flex_formats',['landscape']) if f in SIZES]
    return {**check,'name':engine_name({'engine':engine}),'engine':engine,'experimental':True,'modalities':['image_to_video'],
        'profile':DIRECT_PROFILE if engine==DIRECT_ENGINE else PROFILE,'profiles':[{'id':key,'label':v['label'],'steps':v['steps'],'native_note':'Generación nativa a 768p; salida ajustada a 720p.' if v['output']!=v['sizes'] else 'Resolución nativa.', 'formats':[{'id':f,'width':v['output'][f][0],'height':v['output'][f][1]} for f in formats]} for key,v in PROFILES.items() if v['engine']==engine],'formats':[{'id':f,'width':SIZES[f][0],'height':SIZES[f][1]} for f in formats],
        'frames':124,'fps':24,'duration_seconds':124/24,'steps':8 if engine==DIRECT_ENGINE else 6,'qwen':True,'chat':True,
        'negative_prompt':False,'multi_reference':False,'video_sequence':False,
        'not_validated':['text_to_video','character_replacement','motion_transfer','multi_reference','long_video'],
        'limits':'Una imagen inicial y una toma de 5.17 s. La calidad y la fidelidad del movimiento requieren revisión.'}
