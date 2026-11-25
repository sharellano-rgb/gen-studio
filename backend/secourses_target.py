"""Versioned target selection and empty-mask guard for new Qwen SECourses takes."""
import re
from copy import deepcopy

POLICY = 'whole-video-1'
GUARD_NODE = 'GenStudioSECoursesMaskCheck'
_INVALID_WORDS = frozenset(('replace replacement change remove generate create make keep preserve use please ignore '
    'reemplaza reemplazar cambia cambiar elimina generar crea haz conserva usa por favor '
    'perro perros persona personas hombre mujer marron cafe negro negra imagen video reference picture').split())


def validate_target_sam(value):
    """Reject obvious instructions; semantic/source validation belongs to scoped Qwen evidence."""
    if not isinstance(value,str):
        raise ValueError('Falta la selección visual del sujeto para SAM; vuelve a analizar la toma.')
    if any(c in value for c in '\r\n\t'):
        raise ValueError('SAM requiere una sola descripción breve, sin instrucciones adicionales.')
    value=' '.join(value.strip().lower().split())
    words=value.split()
    if not 1<=len(words)<=12 or not 1<=len(value)<=120 or not re.fullmatch(r'[a-z][a-z0-9 -]*',value):
        raise ValueError('SAM requiere una descripción breve del sujeto en inglés, sin instrucciones.')
    if any(w in _INVALID_WORDS for w in words):
        raise ValueError('La selección de SAM contiene instrucciones o no describe al sujeto fuente en inglés.')
    return value


def active(job):
    p=job['parameters']
    return job.get('qwen_global_policy')==POLICY and p.get('engine')=='h3_secourses' and bool(p.get('auto_describe'))


def parameters(job,analysis,render_parameters,*,scoped_context=None):
    """Return an ephemeral graph plan; never mutate the accepted request or history."""
    if not active(job):
        return render_parameters
    context=analysis.get('global_context') if isinstance(analysis,dict) else None
    binding=analysis.get('binding') if isinstance(analysis,dict) else None
    sha=context.get('sha256') if isinstance(context,dict) else None
    if (not isinstance(sha,str) or not re.fullmatch(r'[0-9a-f]{64}',sha)
            or not isinstance(binding,dict) or binding.get('global_context_sha256')!=sha
            or not isinstance(scoped_context,dict) or scoped_context!=context):
        raise ValueError('La selección SAM no está vinculada al análisis completo de esta toma.')
    target=validate_target_sam(context.get('target_sam'))
    return {**render_parameters,'target':target,'secourses_target_policy':POLICY,
            'secourses_target_context_sha256':sha}


def adapt_graph(graph,p,ident):
    if p.get('secourses_target_policy')!=POLICY:return graph
    target=validate_target_sam(p.get('target'))
    if graph.get('guide',{}).get('class_type')!='SECoursesH3GenjutsuMotionGuide':
        raise ValueError('La selección SAM requiere la guía nativa SECourses.')
    if 'target-mask-check' in graph:raise ValueError('La máscara ya tiene una validación asignada.')
    if graph['guide']['inputs'].get('target')!=target:raise ValueError('Cambió el sujeto de SAM después del análisis.')
    g=deepcopy(graph)
    g['target-mask-check']={'class_type':GUARD_NODE,'inputs':{'images':['guide',0],
        'masks':['guide',1],'expected':['guide',2],'target':target,'run_id':ident,
        'context_sha256':p['secourses_target_context_sha256']}}
    if p.get('visual_method')=='faithful':
        if g.get('fiel-mask',{}).get('inputs',{}).get('masks')!=['guide',1]:
            raise ValueError('La guía Fiel no ofrece la máscara fuente esperada.')
        g['fiel-mask']['inputs']['masks']=['target-mask-check',1]
    else:
        inputs=g['condition']['inputs']
        if inputs.get('ref_videos.ref_video_0')!=['guide',0]:
            raise ValueError('La guía SECourses no ocupa el papel de vídeo esperado.')
        inputs['ref_videos.ref_video_0']=['target-mask-check',0]
    return g
