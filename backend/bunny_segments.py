"""Sequential Bunny windows with a native guide at both sampling resolutions."""
import asyncio
import json
from pathlib import Path
from uuid import uuid4
from .head import digest
from .guided_composition import local_path
from .job_cancel import checkpoint, thread_checkpoint

VERSION = 'bunny-segments-v4'
TAIL_CONTEXT_VERSION = 'bunny-segments-v3'
LEGACY_VERSION = 'bunny-segments-v2'
CONTINUITY = 'bunny-guide-full-tail-v3'
TAIL_CONTEXT_CONTINUITY = 'bunny-guide-both-passes-tail-v2'
LEGACY_CONTINUITY = 'bunny-guide-both-passes-v1'
MAX_FRAMES = 1440
CHUNK_FRAMES = 120


def plan(frames, version=VERSION):
    if version not in (VERSION, TAIL_CONTEXT_VERSION, LEGACY_VERSION):
        raise ValueError('La versión del plan Bunny no es compatible.')
    if type(frames) is not int or not 5 <= frames <= MAX_FRAMES:
        raise ValueError('Bunny admite entre 5 fotogramas y 60 segundos.')
    if version == VERSION and frames > CHUNK_FRAMES:
        # Keep the residual at the beginning. Every later output is a full 5 s
        # window with its continuity image at frame zero, including the ending.
        first = frames % CHUNK_FRAMES or CHUNK_FRAMES
        keeps = [first] + [CHUNK_FRAMES] * ((frames-first)//CHUNK_FRAMES)
    else:
        keeps = [min(CHUNK_FRAMES, frames-start) for start in range(0, frames, CHUNK_FRAMES)]
    result = []
    start = 0
    for keep in keeps:
        context = (CHUNK_FRAMES+1-keep if version == TAIL_CONTEXT_VERSION else 1) if start else 0
        # A short first delivery still sees 5 s of real source motion; the
        # generated look-ahead is discarded and never extends the output.
        input_frames = CHUNK_FRAMES if version == VERSION and frames > CHUNK_FRAMES and not start else keep+context
        result.append(dict(index=len(result), start=start, keep=keep, context_frames=context,
                           input_start=start-context, input_frames=input_frames))
        start += keep
    return result


def add_guides(g, image, frame_idx=0):
    if type(frame_idx) is not int or not 0 <= frame_idx < g['condition']['inputs']['length']:
        raise ValueError('La posición de la guía de continuidad no es válida.')
    # Each guide must use that pass's actual AV latent dimensions. Reusing the
    # low-resolution keyframe in the refinement pass gives mismatched geometry.
    g['continuity-image'] = {'class_type':'LoadImage','inputs':{'image':image}}
    for name, latent in [('first',['condition',1]), ('refine',['concat_av',0])]:
        g['continuity-'+name] = {'class_type':'MiniMaxH3AddGuide','inputs':{
            'positive':['condition',0], 'latent':latent, 'vae':['vae',0],
            'image':['continuity-image',0], 'frame_idx':frame_idx}}
    g['guider']['inputs']['conditioning'] = ['continuity-first',0]
    g['continuity-guider'] = {'class_type':'BasicGuider','inputs':{
        'model':g['guider']['inputs']['model'], 'conditioning':['continuity-refine',0]}}
    g['pass2']['inputs']['guider'] = ['continuity-guider',0]
    if frame_idx == 0:
        # Preserve the exact graph/prompt of saved v2 and full-length windows.
        g['condition']['inputs']['prompt'] += ('\nTemporal continuity: the first source frame is the previous segment endpoint, supplied as a native image guide. Continue directly from its appearance, facial expression, mouth position, skin color, lighting and environment. Preserve the original character reference as the identity authority. Follow the NEW source movement immediately after that first frame; do not repeat the preceding action or freeze the face.')
    else:
        g['condition']['inputs']['prompt'] += (
            f'\nTemporal continuity: source frame {frame_idx} (zero-based, {frame_idx/24:.6f} seconds into <Video 1>) is the previous segment endpoint, supplied as a native image guide at that exact time. '
            'Earlier source frames provide motion context and their generated output will be discarded. '
            'Match the guide appearance, facial expression, mouth position, skin color, lighting and environment at the anchor. '
            'Preserve the original character reference as the identity authority. '
            'Immediately after the anchor, continue the subsequent source movement frame by frame with its direction, speed, scale and head orientation; do not restart the action, freeze at the anchor or move the anchor to the beginning of the clip.')
    return g


def save_once(path, value):
    if path.exists():
        if json.loads(path.read_text(encoding='utf-8')) != value:
            raise ValueError('Cambió el plan de tramos guardado; no se regenerará silenciosamente.')
        return
    temporary = path.with_suffix('.'+str(uuid4())+'.tmp')
    temporary.write_text(json.dumps(value,indent=2),encoding='utf-8')
    temporary.replace(path)


async def process(service, job):
    from .motion_bunny import checked_assets, graph
    from .motion_transfer import cpu, upload
    from .guided_head import phase
    from .motion_hybrid import verified_copy
    p = job['parameters']
    version = p.get('bunny_version')
    expected_continuity = {VERSION: CONTINUITY, TAIL_CONTEXT_VERSION: TAIL_CONTEXT_CONTINUITY,
        LEGACY_VERSION: LEGACY_CONTINUITY}.get(version)
    if expected_continuity is None or p.get('bunny_chunk_frames') != CHUNK_FRAMES or p.get('bunny_continuity_version') != expected_continuity:
        raise ValueError('La política de tramos Bunny no es compatible.')
    segments = plan(p['frames'], version)
    # Old CPU checkpoints/manifests remain unchanged when resuming v2 jobs.
    media_version = {'bunny_version': version} if version != LEGACY_VERSION else {}
    folder = service.root/'jobs'/job['id']; folder.mkdir(parents=True,exist_ok=True)
    checkpoint(service,job['id'])
    paths = await asyncio.to_thread(checked_assets,service,p)
    save_once(folder/'bunny-plan.json',{'version':version,'parameters':p,'segments':segments})
    direction = p['direction'].strip()
    if direction:
        from .prompt_language import normalize
        record = await normalize(service,job,direction,folder/'bunny-direction.json')
        direction = record['prompt']
    finished = []
    source_start = round(p['start_seconds']*24)
    service.store.update(job['id'],bunny_segment_total=len(segments))
    for segment in segments:
        checkpoint(service,job['id'])
        i = segment['index']; tag = f'bunny-{i:03}'
        service.store.update(job['id'],bunny_segment_index=i,bunny_segments_done=i,
            stage=f'Preparando tramo {i+1} de {len(segments)} · Bunny')
        ready = await cpu(service,job,'bunny_prepare',{
            'source':str(paths['video']),'source_sha256':p['video_sha256'],
            'frames':segment['input_frames'],'start_frame':source_start+segment['input_start'],
            'resolution':p['resolution']},checkpoint_name=tag+'-prepare')
        guide = None
        if i:
            previous = finished[-1]
            if digest(local_path(folder,previous['video'])) != previous['sha256']:
                raise ValueError('Cambió el tramo anterior usado para la continuidad.')
            guide = await cpu(service,job,'bunny_guide',{
                'source':previous['video'],'source_sha256':previous['sha256'],
                'frame_index':previous['keep']-1,'resolution':p['resolution']},checkpoint_name=tag+'-guide')
            if (guide['source_sha256'] != previous['sha256'] or guide['frame_index'] != previous['keep']-1):
                raise ValueError('La guía no corresponde al último fotograma visible anterior.')
        checkpoint(service,job['id'])
        uploads = folder/(tag+'-uploads.json')
        bindings = {'video':ready['files'][ready['video']], 'reference':p['reference_sha256'],
            'environment_reference':p.get('environment_reference_sha256'),
            'continuity':guide['sha256'] if guide else None}
        if uploads.exists():
            saved = json.loads(uploads.read_text(encoding='utf-8'))
            if saved['bindings'] != bindings:raise ValueError('Cambiaron las entradas de un tramo ya preparado.')
            names = saved['names']
        else:
            names = {'video':await upload(service,ready['video'],job['id']),
                'reference':await upload(service,paths['reference'],job['id'])}
            if paths.get('environment_reference'):
                names['environment_reference'] = await upload(service,paths['environment_reference'],job['id'])
            if guide:names['continuity'] = await upload(service,guide['path'],job['id'])
            save_once(uploads,{'names':names,'bindings':bindings})
        params = {**p,'frames':segment['input_frames']}
        if guide and version == TAIL_CONTEXT_VERSION:
            params['bunny_guide_frame_index'] = segment['context_frames']-1
        result = await phase(service,job,f'Bunny tramo {i+1:02} de {len(segments):02}',
            graph(params,names,job['id']+'/'+tag,direction),'save_final','.mp4')
        if result is None:return
        checkpoint(service,job['id'])
        service.store.update(job['id'],stage=f'Guardando tramo {i+1} de {len(segments)} · Bunny')
        finalize_policy = {**media_version, 'input_frames':segment['input_frames']} if version == VERSION else media_version
        final = await cpu(service,job,'bunny_finalize',{
            'video':result['path'],'video_sha256':result['sha256'],
            'source':str(paths['video']),'source_sha256':p['video_sha256'],
            'frames':segment['keep'],'context_frames':segment['context_frames'],
            'start_frame':source_start+segment['start'],'resolution':p['resolution'],
            'with_audio':False,**finalize_policy},checkpoint_name=tag+'-finalize')
        finished.append({**segment,'video':final['video'],'sha256':final['files'][final['video']]})
        service.store.update(job['id'],bunny_segments_done=i+1,
            bunny_segments=[{'index':s['index'],'frames':s['keep'],'sha256':s['sha256']} for s in finished])
    checkpoint(service,job['id'])
    service.store.update(job['id'],stage='Uniendo tramos Bunny y conservando el audio original')
    final = await cpu(service,job,'bunny_join',{'source':str(paths['video']),
        'source_sha256':p['video_sha256'],'frames':p['frames'],'start_frame':source_start,
        'resolution':p['resolution'],'segments':finished,**media_version},checkpoint_name='bunny-assembly')
    await asyncio.to_thread(checked_assets,service,p)
    target = service.root/'results'/(job['id']+'.mp4'); target.parent.mkdir(exist_ok=True)
    await thread_checkpoint(service,job['id'],verified_copy,final['video'],target,final['files'][final['video']])
    service.store.update(job['id'],status='completed',stage='Bunny · Vídeo completo listo',
        result_available=True,progress=None,scene_resolution=p['resolution'],
        motion_result_sha256=final['files'][final['video']],motion_audio=final['audio'])
