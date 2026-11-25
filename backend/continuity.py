"""Opt-in temporal guide for continuous Direct windows; no reference slots replaced."""
from .head import digest
from .long_plan import CHUNK_SIZES, DIRECT_7S_VERSION
from .guided_composition import local_path

VERSION='last-visible-guide-v1'
NODE='MiniMaxH3AddGuide'


def validate_catalog(catalog):
    node=catalog.get(NODE,{})
    fields=set(node.get('input',{}).get('required',{}))|set(node.get('input',{}).get('optional',{}))
    if not {'positive','latent','frame_idx','vae','image'}<=fields or node.get('output',[None])[0]!='CONDITIONING':
        raise ValueError('El motor no tiene una guía nativa de continuidad compatible. Comprueba el motor antes de generar.')


def enabled(p):
    if not p.get('direct_continuity'):
        if p.get('direct_continuity_version'):raise ValueError('La política de continuidad no es válida.')
        return False
    if (p.get('direct_continuity_version')!=VERSION or p.get('direct_chunk_frames') not in CHUNK_SIZES.values()
            or p.get('engine')!='h3_direct' or p.get('direct_preset')!='hands'
            or p.get('direct_character_version')!='v1.5' or p.get('direct_scene_cuts')
            or not 240<p.get('frames',0)<=(2880 if p.get('direct_chunk_version')==DIRECT_7S_VERSION else 1440)):
        raise ValueError('La continuidad requiere una toma continua de H3 directo v1.5 de más de 10 s.')
    return True


def contextual_segments(segments):
    result=[]
    for i,s in enumerate(segments):
        previous=segments[i-1] if i else None
        context=int(bool(previous and not previous.get('ends_at_cut') and not previous['passthrough'] and not s['passthrough']))
        count=s['keep']+context
        result.append({**s,'context_frames':context,'input_start':s['start']-context,'input_keep':count,
            'render_frames':max(5,count+(5-count)%17)})
    return result


def apply_guide(graph,image_name):
    encoders=[k for k,n in graph.items() if n['class_type']=='MiniMaxH3ReferenceToVideo']
    if len(encoders)!=1:raise ValueError('No se encontró un condicionamiento H3 único para la guía.')
    encoder=encoders[0]
    vae=graph[encoder]['inputs'].get('vae')
    if not vae:raise ValueError('La guía de continuidad requiere el VAE de vídeo.')
    if not image_name:raise ValueError('Falta el fotograma de continuidad.')
    graph['continuity-image']={'class_type':'LoadImage','inputs':{'image':image_name}}
    graph['continuity-guide']={'class_type':NODE,'inputs':{'positive':[encoder,0],'latent':[encoder,1],
        'vae':vae,'image':['continuity-image',0],'frame_idx':0}}
    changed=0
    for n in graph.values():
        if n['class_type']=='BasicGuider' and n['inputs'].get('conditioning')==[encoder,0]:
            n['inputs']['conditioning']=['continuity-guide',0];changed+=1
    if changed!=1:raise ValueError('No se pudo conectar la guía al muestreo de H3.')
    return graph


def validate_guide(service,job):
    p=job['parameters'];guide=p.get('direct_continuity_guide')
    if not guide:
        if p.get('long_context_frames',0):raise ValueError('Falta la guía del fotograma previo oculto.')
        return None
    if (guide.get('version')!=VERSION or p.get('long_context_frames')!=1
            or not p.get('long_parent_id') or type(p.get('long_segment_index')) is not int
            or p['long_segment_index']<1):
        raise ValueError('La guía no corresponde a un tramo continuo.')
    previous=service.store.job(guide.get('previous_job_id',''))
    pp=(previous or {}).get('parameters',{})
    from .long_video import lineage
    parent=service.store.job(p['long_parent_id'])
    if (not previous or previous['status']!='completed' or not previous.get('result_available')
            or not parent or pp.get('long_parent_id') not in lineage(service,parent)
            or pp.get('long_segment_index')!=p['long_segment_index']-1
            or guide.get('frame_index')!=pp.get('long_context_frames',0)+pp.get('long_keep_frames',0)-1):
        raise ValueError('El fotograma no procede del final visible del tramo anterior.')
    source=service.root/'results'/(previous['id']+'.mp4')
    if (guide.get('source_sha256')!=previous.get('direct_result_sha256')
            or not source.is_file() or digest(source)!=guide['source_sha256']):
        raise ValueError('Cambió el resultado usado para mantener la continuidad.')
    image=local_path(service.root,guide['path'])
    if not image.is_file() or digest(image)!=guide.get('sha256'):
        raise ValueError('Cambió el fotograma de continuidad; no se sustituye silenciosamente.')
    return image


def regeneration_indices(plan,index):
    indices=[index]
    if plan.get('continuity_version')!=VERSION:return indices
    for s in plan['segments'][index+1:]:
        if not s.get('context_frames'):break
        indices.append(s['index'])
    return indices
