"""No model load or process launch during availability checks."""
import json
from pathlib import Path
ENGINE='viggle_remake'
MAX_FRAMES=2880
VERSION='viggle-remake-comfy-1'
CONTINUITY='viggle-remake-hop17-1'
REVISION='21665fa7a1e038e0a5312fb15f37b18d0f06f117'
BASE='http://127.0.0.1:8198'
LENGTHS=(90,107,124)
SIZES={480:{'landscape':(832,480),'portrait':(480,832)},736:{'landscape':(1280,736),'portrait':(736,1280)}}
MODELS={mode:f'viggle_remake_{mode}_pruned_int8_convrot.safetensors' for mode in ('swap','edit')}
SIGMAS={'swap':'1.0, 0.990476, 0.965243, 0.923077, 0.8, 0.631579, 0.0','edit':'1.0, 0.983607, 0.96, 0.923077, 0.857143, 0.705882, 0.0'}

def root(settings):
    value=settings.get('viggle_remake_root')
    if not value or not Path(value).is_absolute():raise ValueError('Configura la ruta absoluta de la instalación aislada de Viggle.')
    return Path(value).resolve()

def inventory(settings,mode='swap'):
    if mode not in MODELS:raise ValueError('Modo nativo Viggle desconocido.')
    errors=[]
    if not settings.get('viggle_remake_enabled',False):errors.append('Viggle Remake está desactivado.')
    try:
        base=root(settings)
        manifest=json.loads((base/'config/gen-studio-inventory-v1.json').read_text(encoding='utf-8'))
        if manifest['revision']!=REVISION:raise ValueError('La revisión no coincide.')
        for key in ('python','main','nodes','hop','clip','video_vae','audio_vae',mode):
            item=manifest.get('files',{}).get(key)
            if not item:errors.append('Falta verificar el componente: '+key);continue
            path=Path(item['path']);stat=path.stat()
            if stat.st_size!=item['bytes'] or stat.st_mtime_ns!=item['mtime_ns'] or len(item.get('sha256',''))!=64:
                errors.append('Cambió un componente verificado: '+path.name)
    except (OSError,ValueError,KeyError,TypeError) as error:errors.append('Inventario Viggle no disponible: '+str(error))
    return {'ok':not errors,'errors':errors,'native_mode':mode,'version':VERSION,
            'verification':'SHA256 inicial + guardas de tamaño y fecha; sin cargar modelos'}

def capabilities(settings):
    from .painted import VERSION as PAINTED_VERSION
    from .detail_director import VERSION as DETAIL_VERSION
    swap=inventory(settings,'swap')
    errors=list(swap['errors'])
    if not settings.get('qwen_director_enabled') or not settings.get('video_sequences_enabled'):
        errors.append('Viggle requiere Qwen y la generación por ventanas.')
    formats=[{'id':name,'width':SIZES[736][name][0],'height':SIZES[736][name][1]} for name in settings.get('viggle_remake_formats',['landscape']) if name in SIZES[736]]
    return {'ok':not errors,'errors':errors,'formats':formats,'max_frames':MAX_FRAMES,
'engine':'viggle_remake','version':VERSION,'experimental':True,
      'multi_character':{'enabled':bool(settings.get('viggle_multi_enabled',False)),'max_assignments':4,'reference_mode':'appearance','gpu_tested_assignments':2,'gpu_test_frames':90,'gpu_test_resolution':[1280,736]},
      'painted_keyframes':{'enabled':bool(settings.get('viggle_keyframes_enabled',False)),'version':PAINTED_VERSION,'editor':'Qwen Image 2.1','max_paint_attempts':2,'full_frame':True,'review_each_character':True,'video_review':'sampled, blocks continuation on failure or uncertainty'},
      'shot_review':{'enabled':bool(settings.get('viggle_shot_review_enabled',False)),'version':'viggle-shot-anchor-1','coarse_before_details':True,'experimental':True},
      'anchor_selection':{'enabled':bool(settings.get('viggle_shot_review_enabled',False) and settings.get('viggle_anchor_selection_enabled',False)),'version':'viggle-anchor-selection-7','scope':'first native window of each shot','max_candidates':7,'continuation':'native hop17 unchanged'},
      'take_repair':{'enabled':False,'stage':'isolated preparation; no automatic rerender'},
      'detail_corrections':{'enabled':bool(settings.get('viggle_detail_corrections_enabled',False)),'version':DETAIL_VERSION,'max_attempts_per_detail':2,'experimental':True,'scope':'painted anchors only'},
      'enabled':bool(settings.get('viggle_remake_enabled',False)),
      'modes':{m:inventory(settings,m) for m in MODELS},'fps':24,'native_window_frames':list(LENGTHS),
      'continuity_frames':17,'continuity_version':CONTINUITY,'steps':6,'default_short_edge':736,
      'tested':{'mode':'swap','canvas':[1280,736],'frames':226,'references_per_window':1},
      'negative_prompt':False,'automatic_visual_regeneration':False,
      'stage':'backend_experimental','qwen_chat_integrated':True,
      'selectable_modes':['swap','motion'],'native_mode':'swap','free_video_edit':False,
      'validated_formats':settings.get('viggle_remake_formats',['landscape'])}
