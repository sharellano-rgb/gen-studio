"""Exact 24 fps windows bounded by the selected policy and hard cuts."""
from .long_plan import DIRECT_7S_VERSION, DIRECT_7S_FRAMES
VERSION='video-sequence-5s-v1'
FAITHFUL_VERSION='visual-faithful-sequence-5s-v1'
FAITHFUL_METHOD_VERSION='visual-faithful-1'
VIGGLE_VERSION='viggle-remake-hop17-1'


def version_for(parameters):
    if parameters.get('engine')=='h3_direct' and parameters.get('direct_chunk_version')==DIRECT_7S_VERSION:
        return DIRECT_7S_VERSION
    if parameters.get('engine')=='viggle_remake':
        from .viggle_remake.profiles import VERSION as native
        if parameters.get('viggle_version')!=native:raise ValueError('Versión Viggle no compatible.')
        return VIGGLE_VERSION
    if parameters.get('visual_method')=='faithful':
        if parameters.get('visual_faithful_version')!=FAITHFUL_METHOD_VERSION:
            raise ValueError('La versión del método fiel no es compatible.')
        return FAITHFUL_VERSION
    if parameters.get('visual_faithful_version'):
        raise ValueError('La política fiel requiere su propio método.')
    return VERSION


def context_for_version(version):
    if version==VIGGLE_VERSION:return 17
    if version in (VERSION,DIRECT_7S_VERSION):return 1
    if version==FAITHFUL_VERSION:return 5
    raise ValueError('La versión de continuidad no es compatible.')

MAX_FRAMES=2880
CHUNK=120

def window_size(version):
    return DIRECT_7S_FRAMES if version==DIRECT_7S_VERSION else CHUNK


def plan(frames,cuts=(),*,context_frames=1,chunk_frames=CHUNK):
    if chunk_frames not in (CHUNK,DIRECT_7S_FRAMES) or (chunk_frames!=CHUNK and context_frames!=1):
        raise ValueError('La duración del tramo no corresponde a la política de continuidad.')
    if type(context_frames) is int and context_frames==17:
        from .viggle_remake.planner import plan as native_plan
        result=[]
        for w in native_plan(frames,cuts)['windows']:
            result.append(dict(index=w['index'],shot=w['shot'],start=w['own'][0],keep=w['keep'],
                context_frames=w['trim_start'],input_start=w['source'][0],input_keep=w['source'][1]-w['source'][0],
                render_frames=w['render_frames'],starts_at_cut=w['starts_at_cut'],
                ends_at_cut=w['own'][1] in cuts))
        return result
    if type(context_frames) is not int or context_frames not in (1,5):raise ValueError('El contexto debe contener uno o cinco fotogramas reales.')
    if type(frames) is not int or not 5<=frames<=MAX_FRAMES:raise ValueError('Selecciona entre 5 fotogramas y 120 segundos.')
    if any(type(c) is not int or not 0<c<frames for c in cuts):raise ValueError('Límites de plano no válidos.')
    boundaries=[0,*sorted(set(cuts)),frames];result=[]
    for shot,(begin,end) in enumerate(zip(boundaries,boundaries[1:])):
        start=begin
        while start<end:
            keep=min(chunk_frames,end-start)
            if 0<end-start-keep<5:keep-=5-(end-start-keep)
            context=(context_frames if start-begin>=context_frames else 1) if start>begin else 0
            count=keep+context
            result.append(dict(index=len(result),shot=shot,start=start,keep=keep,context_frames=context,
                input_start=start-context,input_keep=count,render_frames=5+17*max(0,(max(48,count)-5+16)//17),
                starts_at_cut=start==begin and shot>0,ends_at_cut=start+keep==end and end<frames))
            start+=keep
    assert sum(s['keep'] for s in result)==frames
    return result

def supported(p):
    if any(p.get(k) for k in ('sequence_parent_id','long_parent_id','hybrid_parent_id','ninja_sequence_owner')):return False
    engine=p.get('engine');op=p.get('operation')
    return ((engine=='h3_direct' and op in (None,'long_character') and not p.get('long_variant_parent_id'))
        or (engine=='viggle_remake' and op in ('viggle_swap','viggle_motion'))
        or (engine=='pika_ref2va' and op in ('pika_swap','pika_motion'))
        or (engine=='h3_alisson' and op=='alisson_swap')
        or (engine=='h3_secourses' and op=='secourses_swap')
        or (engine=='viggle_motion' and op in ('motion_bunny','motion_layers')))
