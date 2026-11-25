"""Optional, publisher-pinned photographic LoRAs for the MOSS BASE edit recipe."""
from .head import digest
from . import image_profiles

VERSION = 'moss-base-photographic-loras-v1'
CHOICES = {
    'lenovo': {'name':'lenovo_moss.safetensors','bytes':64527960,
        'sha256':'f28ab12e33e60146c8740a86730385cf5ba488e9f6eda50b14383d6ddbf300fb','trigger':'l3n0v0'},
    'oldnokia': {'name':'nokia_moss.safetensors','bytes':64527960,
        'sha256':'4980ed9b597fcf8f502072694ecb9819f28cdc2cba5dc56f6328573fbf718086','trigger':'n0k1a'},
    'smartphone': {'name':'MOSS_BASE_Smartphone_Snapshot_v14.safetensors','bytes':39755064,
        'sha256':'e7442190461c6b96b5e0f45ae41c432adc57ac1a6c84590338afc310935dad9c','trigger':'photo'},
}
FIELDS = ('realism_lora','realism_strength')
BOUND = (*FIELDS,'realism_version','realism_weights')

def selected(p):return p.get('realism_lora','none')!='none'

def validate(mode,profile,choice):
    if choice!='none' and profile not in ('standard',image_profiles.PROFILE):
        raise ValueError('Elige un perfil MOSS válido para el acabado fotográfico.')

def weights(p):return dict(CHOICES[p['realism_lora']])

def verify(p):
    value=weights(p);path=image_profiles.MODELS_ROOT/'loras'/value['name']
    try:
        if not path.is_file() or path.stat().st_size!=value['bytes'] or digest(path)!=value['sha256']:
            raise ValueError('weight mismatch')
    except (OSError,ValueError) as error:
        raise ValueError('No se pudo verificar el SHA-256 del LoRA de acabado fotográfico seleccionado.') from error

def bind(p):
    if not selected(p):return p
    verify(p)
    return {**p,'realism_version':VERSION,'realism_weights':weights(p)}

def binding(p):return {key:p[key] for key in BOUND} if selected(p) else {}

def verify_binding(p,verify_files=True):
    if not selected(p):return
    if p.get('realism_version')!=VERSION or p.get('realism_weights')!=weights(p):
        raise ValueError('Cambió el acabado fotográfico del trabajo guardado.')
    if verify_files:verify(p)

def apply(graph,p,target='edit',source=None):
    if not selected(p):return
    value=weights(p)
    graph['realism']={'class_type':'LoraLoaderModelOnly','inputs':{
        'model':source if source is not None else ['mystic',0],'lora_name':value['name'],'strength_model':p['realism_strength']}}
    graph[target]['inputs']['model']=['realism',0]
    if p['realism_strength']>0:
        graph['positive']['inputs']['text']=value['trigger']+' '+graph['positive']['inputs']['text']
