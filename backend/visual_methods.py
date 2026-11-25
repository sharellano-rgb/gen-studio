"""Explicit, versioned methods derived from the native SECourses adapter."""
NAMES={'experimental':'Análisis visual detallado Experimental',
       'faithful':'Análisis visual detallado fiel'}
EXPERIMENTAL_VERSION='visual-experimental-1'
FAITHFUL_VERSION='visual-faithful-1'

FAITHFUL_SCOPE='''WORKING-CROP SCOPE: <Video 1> is a tracked crop of the selected source subject, not the full scene. The external tracked crop and recomposition restore the original full-frame position, apparent size and camera framing. Do not perform that full-frame translation or scale change a second time inside the working crop. Match the visible subject placement, body proportions, orientation, limb articulation and floor/object contacts in each working-guide frame. Follow its actual timed changes, including the overlapping source frames; never restart the action or freeze an opening pose. Keep the assigned reference appearance stable. The original scene outside the replacement mask is restored separately; do not invent a new background or camera move.'''

FAITHFUL_DIRECTOR_RULES='''This method generates a tracked working crop and then maps it back into the original scene using source tracking. Full-frame box coordinates and scale changes in the evidence describe the FINAL composition, which is handled by that mapping, not by an additional translation or zoom inside the generated crop. In the generation instructions, focus on relative body geometry, orientation, articulation, changing contacts and occlusions at their observed times. Do not repeat absolute full-frame coordinates, numerical full-frame size targets, or instructions to move/scale the whole working crop. When describing camera/background, follow the actual working guide; external recomposition retains the original full-frame scene. Opening pose applies only to local time zero, including real overlap context. Explain the full-frame trajectory in the Spanish feedback if useful, but do not claim exact pose reconstruction or hard pixel continuity.'''

def name(p):
    return NAMES.get(p.get('visual_method'),'SECourses')

def request_payload(body):
    request=body.model_dump(mode='json',exclude={'request_key'})
    selected=body.visual_method
    if selected is None:request.pop('visual_method',None)
    for field in ('qwen_adaptive_zoom','qwen_motion_grounding'):
        if field not in body.model_fields_set:
            if selected:request[field]=True
            else:request.pop(field,None)
    return request

def validate(request,settings):
    selected=request.get('visual_method')
    if selected is None:return
    if selected not in NAMES:raise ValueError('Método visual desconocido.')
    from .secourses_qwen import automatic
    if not automatic(request) or not request.get('qwen_adaptive_zoom') or not request.get('qwen_motion_grounding'):
        raise ValueError('Este método utiliza Qwen automáticamente; escribe indicaciones adicionales, no un prompt H3 completo.')
    if not settings.get('qwen_director_enabled'):
        raise ValueError('Activa Qwen y su chat para utilizar este método.')
    if selected=='faithful':
        if request.get('background')!='video':raise ValueError('Análisis visual detallado fiel conserva el escenario original y se utiliza en Object Swap.')
        if not settings.get('video_sequences_enabled'):raise ValueError('Activa la generación por tramos para utilizar Análisis visual detallado fiel.')

def versions(request):
    selected=request.get('visual_method')
    if selected=='experimental':return {'visual_experimental_version':EXPERIMENTAL_VERSION}
    if selected=='faithful':return {'visual_faithful_version':FAITHFUL_VERSION}
    return {}
