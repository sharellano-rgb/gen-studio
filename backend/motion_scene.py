"""Short experimental H3 whole-scene generation, without a Viggle video pass."""
from uuid import uuid5,NAMESPACE_URL
VERSION='h3-scene-direct-v3'
VERSIONS=('h3-scene-direct-v2',VERSION)
METHOD='h3_scene_direct'
RECIPE='whole-scene-guide-v1'
MAX_FRAMES=107

def prompt(direction,head_motion=False):
    text=(
        'Reconstruct the COMPLETE destination scene shown in <Picture 2> throughout <Video 1>. '
        '<Video 1> supplies ONLY motion, action timing, camera movement and spatial interactions. '
        '<Picture 1> supplies the exact main character identity, face, hair, anatomy, clothing and its colors. '
        '<Picture 2> supplies the destination environment, scene layout, replacement props and visual rendering style. '
        'Maintain those separate reference roles from the FIRST to the LAST frame. '
        'Rebuild the environment in the destination style in every frame, including areas revealed by camera motion. '
        'Replace source scenery and props wherever the destination scene calls for different scenery and props. '
        'Do not restore the source location, source performer, original outfit or photographic source appearance. '
        'Follow the exact source head and body rotations, gaze, balance, limb motion, contact points and action timing. '
        'Map source interactions onto the corresponding destination objects; keep feet supported during the action. '
        'The destination picture fixes appearance and the initial composition, not a frozen pose for the whole clip. '
        'Follow the original camera path and changes of framing; do not replace the source choreography with idle animation. '
        'Do not add a new action, pause, loop or camera cut. '
        'Requested destination scene: '+direction)
    if head_motion:
        text=text.replace('<Video 1> supplies ONLY motion, action timing, camera movement and spatial interactions.', '<Video 1> supplies the already reviewed whole-body poses, action timing, camera movement and spatial interactions.')
        text=text.replace('Follow the exact source head and body rotations, gaze, balance, limb motion, contact points and action timing.', 'Preserve the whole-body poses, balance, limb motion, contact points and action timing from <Video 1>. For HEAD AND NECK orientation only, follow <Video 2> instead.')
        text+=' <Video 2> is a synchronized grayscale head-motion study of the original performer. Use it ONLY for the head yaw, pitch, roll and direction of gaze, frame by frame. It takes priority over the head direction in <Video 1> and the static reference portraits. When it shows the back or side of the head, show the corresponding back or profile of the target head. Keep the chin lowered whenever the original head is lowered. Do not turn toward the camera to display the reference face. Do not copy the human identity, clothing, grayscale, neutral mask, background or crop layout of <Video 2>. Keep the target character identity from <Picture 1> and keep the already reviewed body motion of <Video 1>. Keep the destination appearance and scene content specified in the requested goal throughout, including all image borders. Do not introduce objects or scenery explicitly excluded by that goal.'
    return text

def register_scene(service,parent,path):
    from .motion_hybrid import verified_copy
    from .media import probe
    p=parent['parameters'];sha=p['anchor_sha256']
    if p.get('motion_scene_reference_id'):
        asset,reference=checked_asset(service,p['motion_scene_reference_id'],'image',p['motion_scene_reference_sha256'])
        return asset
    ident=str(uuid5(NAMESPACE_URL,'gen-studio:motion-scene-guide:'+parent['id']))
    target=service.root/'inputs'/(ident+'.png');verified_copy(path,target,sha)
    old=service.store.asset(ident)
    if old:
        if old.get('sha256')!=sha:raise ValueError('Cambió el registro de la escena destino.')
        return old
    return service.store.add_asset(dict(id=ident,kind='image',filename=target.name,name='Escena destino H3 · '+parent['id'][:8],sha256=sha,internal=True,bytes=target.stat().st_size,**probe(target)))

def parameters(parent,source,scene):
    from .motion_hybrid import identity_parameters,VERSION as hybrid_version
    p=parent['parameters']
    base=identity_parameters({**parent,'parameters':{**p,'hybrid_version':hybrid_version}},source,p['hybrid_identity_resolution'])
    base.pop('direct_edit_policy',None)
    base.update(direct_reference_ids=[p['hybrid_identity_reference_id'],scene['id']],
        direct_reference_hashes=[p['hybrid_identity_reference_sha256'],scene['sha256']],
        prompt=prompt(p['motion_scene_goal'],bool(p.get('motion_head_guide'))),motion_scene_recipe=RECIPE,motion_scene_anchor_index=0)
    if p.get('motion_head_guide'):base['motion_head_guide']=p['motion_head_guide'].copy()
    return base

def apply_guide(graph,p,head_video=None):
    if not p.get('motion_scene_recipe'):return graph
    if (p['motion_scene_recipe']!=RECIPE or not p.get('hybrid_parent_id') or p.get('direct_preset')!='hands'
            or p.get('motion_scene_anchor_index')!=0 or len(p.get('direct_reference_ids',[]))!=2
            or 'reference_resize_1' not in graph or '19' not in graph or '20' not in graph):
        raise ValueError('La guía de escena completa no corresponde a esta prueba.')
    # The second reference is already padded/resized to the exact latent canvas.
    graph['scene-guide']={'class_type':'MiniMaxH3AddGuide','inputs':{
        'positive':['19',0],'latent':['19',1],'vae':graph['19']['inputs']['vae'],
        'image':['reference_resize_1',0],'frame_idx':0}}
    graph['20']['inputs']['conditioning']=['scene-guide',0]
    if p.get('motion_head_guide'):
        if not head_video:raise ValueError('Falta el vídeo de orientación de cabeza.')
        graph['scene-head-video']={'class_type':'LoadVideo','inputs':{'file':head_video}}
        graph['scene-head-components']={'class_type':'GetVideoComponents','inputs':{'video':['scene-head-video',0]}}
        graph['19']['inputs']['ref_videos.ref_video_1']=['scene-head-components',0]
    return graph


def checked_asset(service,ident,kind,expected=None):
    from .head import digest
    asset=service.store.asset(ident)
    if not asset or asset['kind']!=kind:raise ValueError('La referencia auxiliar no está disponible en este espacio.')
    path=service.root/'inputs'/asset['filename'];sha=digest(path)
    if sha!=asset['sha256'] or (expected and sha!=expected):raise ValueError('Cambió una referencia auxiliar de la toma.')
    return asset,path

def body_source(service,p):
    if not p.get('motion_body_source_job_id'):return None
    from .head import digest
    job=service.store.job(p['motion_body_source_job_id']);q=(job or {}).get('parameters',{})
    if (not job or job['status']!='completed' or not job.get('result_available') or q.get('transfer_method')!=METHOD
            or q.get('video_id')!=p['video_id'] or q.get('source_sha256')!=p['source_sha256']
            or q.get('start_seconds')!=p['start_seconds'] or q.get('frames',0)<p['frames']):
        raise ValueError('La toma de poses no corresponde a este vídeo, intervalo y espacio.')
    path=service.root/'results'/(job['id']+'.mp4')
    expected=p.get('motion_body_source_sha256') or job.get('motion_result_sha256')
    if not expected or expected!=job.get('motion_result_sha256') or digest(path)!=expected:raise ValueError('Cambió la toma de poses; no se usa otra copia.')
    return path,expected

def head_reference(service,p):
    info=p.get('motion_head_guide')
    if not info:return None
    if p.get('motion_scene_recipe')!=RECIPE or not p.get('hybrid_parent_id') or info.get('frames')!=p['frames']:
        raise ValueError('La guía de cabeza no corresponde al intervalo H3.')
    _,path=checked_asset(service,info['asset_id'],'video',info['sha256'])
    return path

def configure_refinements(service,p,body):
    from .head import digest
    if body.scene_reference_id:
        asset,_=checked_asset(service,str(body.scene_reference_id),'image')
        p.update(motion_scene_reference_id=asset['id'],motion_scene_reference_sha256=asset['sha256'])
    if body.body_source_job_id:
        p['motion_body_source_job_id']=str(body.body_source_job_id)
        _,sha=body_source(service,p);p['motion_body_source_sha256']=sha
    if body.head_reference_video_id:
        import json,subprocess
        if (p['frames']-5)%17:raise ValueError('La guía de cabeza requiere un intervalo H3 exacto de 17n + 5 fotogramas.')
        asset,path=checked_asset(service,str(body.head_reference_video_id),'video')
        result=subprocess.run(['ffprobe','-v','error','-count_frames','-select_streams','v:0','-show_entries','stream=width,height,avg_frame_rate,nb_read_frames','-of','json',str(path)],capture_output=True,text=True,timeout=30,check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        stream=json.loads(result.stdout)['streams'][0]
        if stream['avg_frame_rate']!='24/1' or int(stream['nb_read_frames'])!=p['frames'] or max(stream['width'],stream['height'])>512:
            raise ValueError('La guía de cabeza debe tener exactamente el mismo número de fotogramas, 24 fps y hasta 512 px.')
        p['motion_head_guide']={'asset_id':asset['id'],'sha256':asset['sha256'],'frames':p['frames']}
