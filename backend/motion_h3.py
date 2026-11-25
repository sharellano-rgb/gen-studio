"""Optional short-take H3 motion transfer using individually reviewed scene views."""
import asyncio
import json
import shutil
import hashlib
from pathlib import Path
from uuid import UUID, uuid4
from typing import Literal

import httpx
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field,StrictInt

from .direct_character import graph as native_graph, VENDOR, VENDOR_SHA
from .guided_head import phase
from .head import digest
from .workflows import check_graph
from .store import now

MAX_FRAMES = 107
SEGMENTED_MAX_FRAMES = 720
BINDINGS = ('video_id', 'source_sha256', 'frames',
            'start_seconds', 'resolution', 'scene_mode', 'anchor_editor', 'qwen_recipe')


class SceneView(BaseModel):
    job_id: UUID
    sha256: str = Field(pattern='^[a-f0-9]{64}$')


class AnimateH3(BaseModel):
    request_key: UUID
    approved_anchor_sha256: str = Field(pattern='^[a-f0-9]{64}$')
    additional_views: list[SceneView] = Field(default_factory=list, max_length=2)
    timing_offset_frames: int = Field(default=0, ge=0, le=18, strict=True)
    lora_strength: float = Field(default=1, ge=0, le=1.5, allow_inf_nan=False)
    motion_timing_notes: str = Field(default='', max_length=1500)
    segmented: bool = Field(default=False, strict=True)
    vocal_performance: bool = Field(default=False, strict=True)
    preset: Literal['base', 'turbo8'] = 'base'
    appearance_recipe: Literal['legacy', 'style_v2'] = 'style_v2'
    reuse_first_take: SceneView | None = None
    cut_frames: list[StrictInt] = Field(default_factory=list,max_length=11)


class Retime(BaseModel):
    request_key: UUID
    timing_offset_frames: int = Field(ge=0, le=18, strict=True)


class TakeReview(BaseModel):
    expected_sha256: str = Field(pattern='^[a-f0-9]{64}$')
    approved: bool
    feedback: str = Field(min_length=1,max_length=1000)


def parameters_digest(job):
    return hashlib.sha256(json.dumps(job['parameters'],sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')).hexdigest()


def validate_reviewed_take(service,job):
    review=job.get('motion_h3_review') or {}
    if not review.get('approved'):return
    _,raw=raw_source(service,job)
    if review.get('video_sha256')!=job.get('motion_result_sha256') or review.get('raw_sha256')!=raw['sha256'] or review.get('parameters_sha256')!=parameters_digest(job):
        raise ValueError('La copia aprobada o sus parámetros cambiaron; requiere otra revisión.')
    from .motion_transfer import inputs
    inputs(service,job['parameters'])
    for view in job['parameters']['motion_h3_views']:
        other,_=reviewed_view(service,view['job_id'],view['sha256'])
        inputs(service,other['parameters'])


def raw_source(service, job):
    if not job or job['status']!='completed' or job['parameters'].get('transfer_method')!='h3_multiview' or not job.get('result_available'):
        raise ValueError('Selecciona una toma H3 terminada.')
    original=service.root/'results'/(job['id']+'.mp4')
    if not original.is_file() or digest(original)!=job.get('motion_result_sha256'):
        raise ValueError('El resultado de la toma cambió.')
    source_id=job['parameters'].get('motion_h3_source_job',job['id'])
    source=service.store.job(source_id)
    if not source or source['parameters'].get('transfer_method')!='h3_multiview' or source.get('motion_h3_raw_sha256')!=job.get('motion_h3_raw_sha256'):
        raise ValueError('No se encuentra la generación H3 original de esta toma.')
    if source['parameters'].get('motion_h3_segmented'):
        from .motion_h3_segments import raw_manifest
        return source_id, raw_manifest(service, source)
    path=service.root/'jobs'/source_id/'motion-h3.mp4'
    if not path.is_file() or digest(path)!=job.get('motion_h3_raw_sha256'):
        raise ValueError('Cambió la generación H3 original; se conserva el diagnóstico.')
    return source_id,{'path':str(path),'sha256':digest(path)}


def compatible(primary, other):
    if not primary or not other or other['parameters'].get('operation') != 'motion_anchor':
        return False
    a, b = primary['parameters'], other['parameters']
    if a.get('original_reference') or b.get('original_reference'):
        return bool(a.get('original_reference') and b.get('original_reference') and primary.get('id')==other.get('id'))
    shared = all(a.get(k, 'character' if k == 'scene_mode' else 'klein' if k == 'anchor_editor' else 'legacy' if k=='qwen_recipe' else None) == b.get(k, 'character' if k == 'scene_mode' else 'klein' if k == 'anchor_editor' else 'legacy' if k=='qwen_recipe' else None) for k in BINDINGS)
    same_reference = a.get('reference_id') == b.get('reference_id') and a.get('reference_sha256') == b.get('reference_sha256')
    inherited_style = a.get('scene_mode') == 'restyle' and bool(primary.get('motion_anchor_sha256')) and b.get('reference_sha256') == primary['motion_anchor_sha256']
    return shared and (same_reference or inherited_style)


def reviewed_view(service, job_id, sha):
    job = service.store.job(job_id)
    if not job or job['parameters'].get('operation') != 'motion_anchor' or job['status'] != 'completed' or not job.get('motion_anchor_available'):
        raise ValueError('Espera a que termine cada fotograma y revisa su apariencia.')
    path = Path(job['motion_anchor_path'])
    if job.get('motion_anchor_sha256') != sha or not path.is_file() or digest(path) != sha:
        raise ValueError('Un fotograma revisado cambió; vuelve a comprobarlo.')
    return job, path


def prompt(parameters, views):
    if parameters.get('original_reference'):
        return parameters['direction']+(' Additional timing directions: '+parameters['motion_timing_notes'] if parameters.get('motion_timing_notes') else '')
    if parameters.get('motion_appearance_recipe') == 'style_v2':
        return (
            'Animate the complete scene in <Picture 1>, using <Video 1> as the motion and camera guide. '
            'Every output frame must use the characters AND the visual rendering style of <Picture 1>. '
            'Recreate the whole environment, water, vegetation, architecture, objects and lighting in the same artistic medium, '
            'linework, shading, textures and palette as the prepared picture. '
            'The video supplies movement and spatial layout only; do not copy its original actors or its rendering style. '
            'Keep the reference character design, face, hair, costume and proportions throughout the complete clip, '
            'including turns, crouches, occlusions and the final frame. Never revert to the source performer. '
            'Follow the source choreography, limb articulation, contacts, camera movement and action timing. '
            'Use changing poses from the video, not a frozen reference pose. '
            + parameters['direction'] +
            (' Additional appearance views: ' + ' '.join(f'<Picture {i+2}> shows the same target appearance from another moment.' for i in range(len(views)-1)) if len(views)>1 else '') +
            (' Additional timing directions: '+parameters['motion_timing_notes'] if parameters.get('motion_timing_notes') else '')
        )
    descriptions = ' '.join(f'<Picture {i+1}> shows the desired appearance at source time {v["anchor_index"]/24:.3f} seconds.' for i, v in enumerate(views))
    scope = ('Reconstruct the entire scene, characters, objects, materials and environment in the requested style.'
             if parameters.get('scene_mode') == 'restyle' else 'Change only the requested character appearance; retain the source environment and lighting.')
    return ('Transfer the motion from <Video 1> with the following visual direction: ' + parameters['direction'] + ' ' + scope + ' ' + descriptions +
            ' The pictures are appearance references from different moments of the same take, not a sequence of replacement poses. '
            'The video determines exact choreography, head and body rotation, object contacts, relative sizes, camera path, framing, cuts and action timing. '
            'Keep the same number of characters and coherent anatomy through turns and occlusions. Do not delay the action, insert an opening freeze or invent extra motion. No soundtrack.' +
            (' Additional timing directions: ' + parameters['motion_timing_notes'] if parameters.get('motion_timing_notes') else ''))


def graph(parameters, video, names, ident, audio=None):
    # Uses the protected native H3 graph; no Viggle, SAM, face swap or cleanup.
    preset=parameters.get('motion_h3_preset','base')
    if preset not in ('base','turbo8'):raise ValueError('Selecciona H3 base o Turbo 8-step.')
    if preset=='turbo8' and parameters.get('motion_vocal_performance'):
        raise ValueError('La interpretación vocal requiere H3 base de 20 pasos.')
    p = {**parameters, 'frames': 107, 'steps': 8 if preset=='turbo8' else 20, 'direct_preset': preset, 'prompt': parameters['motion_h3_prompt']}
    result=native_graph(p, video, names, ident, audio=False)
    if parameters.get('motion_vocal_performance'):
        from .motion_vocal import fixed_audio_graph
        result=fixed_audio_graph(result,audio)
    return result


def reusable_first_take(service,parameters,job_id,expected_sha):
    first=service.store.job(job_id)
    if not first or first['status']!='completed' or not first.get('result_available'):
        raise ValueError('La primera toma reutilizada debe estar terminada.')
    a=first['parameters'];b=parameters
    if a.get('motion_h3_preset','base')!=b.get('motion_h3_preset','base'):
        raise ValueError('La primera toma utiliza otro modo H3: base o Turbo.')
    if (a.get('transfer_method')!='h3_multiview' or a.get('motion_h3_segmented') or a.get('frames')!=107
            or not a.get('original_reference') or not b.get('original_reference') or not b.get('motion_h3_segmented') or b.get('frames',0)<107
            or a.get('timing_offset_frames',0)!=0 or b.get('timing_offset_frames',0)!=0):
        raise ValueError('Sólo se reutiliza una toma H3 nativa de107frames con foto original y tiempo sin ajustar.')
    keys=('video_id','source_sha256','start_seconds','resolution','reference_id','reference_sha256','direction','seed',
          'lora_strength','motion_vocal_performance','motion_timing_notes')
    if any(a.get(k)!=b.get(k) for k in keys):raise ValueError('La primera toma tiene otra fuente, referencia o configuración.')
    from .motion_transfer import inputs
    inputs(service,a);raw_source(service,first)
    path=service.root/'results'/(first['id']+'.mp4')
    if first.get('motion_result_sha256')!=expected_sha or digest(path)!=expected_sha:raise ValueError('Cambió la primera toma reutilizada.')
    return first,path


async def process(service, job):
    from .motion_transfer import inputs, cpu, upload
    p = job['parameters']
    inputs(service, p)
    views = [reviewed_view(service, v['job_id'], v['sha256']) for v in p['motion_h3_views']]
    primary = views[0][0]
    if any(not compatible(primary, v[0]) for v in views):
        raise ValueError('Los ángulos no corresponden al mismo vídeo y tramo.')
    for view,_ in views:inputs(service,view['parameters'])
    ready = json.loads((service.root / 'jobs' / primary['id'] / 'motion-prepare.json').read_text())
    prepared = Path(ready['video'])
    if digest(prepared) != ready['files'][str(prepared)]:
        raise ValueError('Cambió el tramo de movimiento preparado.')
    if p.get('motion_h3_segmented'):
        from .motion_h3_segments import process as segmented_process
        return await segmented_process(service,job,prepared,views)
    if p.get('motion_h3_source_job'):
        source_job=service.store.job(p['motion_h3_source_job'])
        _,rendered=raw_source(service,source_job)
        if rendered['sha256']!=p['motion_h3_raw_sha256']:raise ValueError('Cambió la toma H3 que se iba a ajustar.')
        service.store.update(job['id'],stage='Ajustando el tiempo en una copia local · CPU')
    else:
        padded = await cpu(service, job, 'pad_h3', {'video': str(prepared), 'video_sha256': digest(prepared),
            'frames': p['frames'], 'resolution': p['resolution']})
        audio=None
        if p.get('motion_vocal_performance'):
            audio=await cpu(service,job,'h3_audio',{'video':str(prepared),'video_sha256':digest(prepared),
                'frames':p['frames'],'resolution':p['resolution'],
                'segment':{'input_start':0,'source_frames':p['frames']}},checkpoint_name='h3-audio')
        folder = service.root / 'jobs' / job['id']
        names_path = folder / 'motion-h3-uploads.json'
        if names_path.exists():
            names = json.loads(names_path.read_text())
        else:
            names = {'video': await upload(service, padded['video'], job['id']),
                     'views': [await upload(service, path, job['id']) for _, path in views]}
            if audio:names['audio']=await upload(service,audio['audio'],job['id'])
            names_path.write_text(json.dumps(names, indent=2), encoding='utf-8')
        rendered = await phase(service, job, 'motion-h3', graph(p, names['video'], names['views'], job['id'],names.get('audio')), '16', '.mp4')
    if rendered is None:
        return
    service.store.update(job['id'],motion_h3_raw_sha256=rendered['sha256'])
    final = await cpu(service, job, 'finalize', {'video': rendered['path'], 'video_sha256': rendered['sha256'],
        'audio_source': str(prepared), 'audio_source_sha256': digest(prepared), 'frames': p['frames'],
        'resolution': p['resolution'], 'timing_offset_frames': p['timing_offset_frames']})
    inputs(service, p)
    for v in p['motion_h3_views']:
        reviewed_view(service, v['job_id'], v['sha256'])
    target = service.root / 'results' / (job['id'] + '.mp4')
    target.parent.mkdir(exist_ok=True)
    if target.exists() and digest(target) != final['files'][final['video']]:
        raise ValueError('Existe otro resultado; se conserva.')
    if not target.exists():
        shutil.copy2(final['video'], target)
    service.store.update(job['id'], status='completed', progress=None, result_available=True,
        stage='Motion Transfer H3 listo · Revisa giro, identidad y tiempo', scene_resolution=p['resolution'],
        motion_result_sha256=digest(target), motion_audio=final['audio'], motion_h3_review_required=True,
        motion_timing={'offset_frames': p['timing_offset_frames'], 'automatic_alignment': False,
                       'tail_hold_frames': final.get('tail_hold_frames', 0), 'scope': 'manual whole-frame time offset; no spatial warp'})


def register(app, settings):
    @app.post('/api/jobs/{job_id}/stop-motion-h3-segments')
    async def stop_segments(job_id:UUID):
        service=app.state.jobs
        async with service.lock:
            job=service.store.job(str(job_id))
            if not job or not job['parameters'].get('motion_h3_segmented') or job['parameters'].get('motion_h3_source_job') or job['status'] not in ('queued','preparing','submitting','running','downloading'):
                raise HTTPException(409,'Selecciona una toma H3 por tramos en curso.')
            return service.store.update(str(job_id),motion_h3_stop_after_window=True)

    @app.post('/api/jobs/{job_id}/review-motion-h3')
    async def review_take(job_id:UUID,body:TakeReview):
        service=app.state.jobs
        async with service.lock:
            try:
                job=service.store.job(str(job_id));_,raw=raw_source(service,job)
                if job.get('motion_result_sha256')!=body.expected_sha256:raise ValueError('La copia revisada cambió.')
                from .motion_transfer import inputs
                inputs(service,job['parameters'])
                for view in job['parameters']['motion_h3_views']:
                    other,_=reviewed_view(service,view['job_id'],view['sha256'])
                    inputs(service,other['parameters'])
                review={'approved':body.approved,'turn_approved':body.approved,'timing_approved':body.approved,
                    'identity_approved':False,'accessory_approved':False,'human_feedback':body.feedback,
                    'video_sha256':body.expected_sha256,'raw_sha256':raw['sha256'],'parameters_sha256':parameters_digest(job),
                    'reviewed_at':now(),'scope':'turn_and_timing_of_this_take'}
                return service.store.update(str(job_id),motion_h3_review=review,motion_h3_review_required=not body.approved,
                    motion_h3_review_history=[*(job.get('motion_h3_review_history') or []),review],
                    stage='Motion Transfer H3 · Giro y sincronización aprobados' if body.approved else 'Motion Transfer H3 · Incidencia registrada')
            except (ValueError,OSError,KeyError) as error:raise HTTPException(409,str(error)) from error

    @app.post('/api/jobs/{job_id}/motion-h3-retime',status_code=202)
    async def retime(job_id:UUID,body:Retime):
        if not settings['generation_enabled']:raise HTTPException(503,'Generación desactivada.')
        service=app.state.jobs
        async with service.lock:
            try:
                parent=service.store.job(str(job_id));source_id,raw=raw_source(service,parent)
                p={**parent['parameters'],'motion_h3_source_job':source_id,'motion_h3_raw_sha256':raw['sha256'],
                   'timing_offset_frames':body.timing_offset_frames}
                result=service.store.create_job(str(uuid4()),str(body.request_key),p)
            except (ValueError,OSError,KeyError) as error:raise HTTPException(409,str(error)) from error
        service.wake.set();return result

    @app.get('/api/workflows/motion-transfer/h3/preflight')
    async def preflight(preset:Literal['base','turbo8']='base'):
        try:
            p = {'resolution': '864x480', 'seed': 42, 'lora_strength': 1, 'motion_h3_preset':preset, 'motion_h3_prompt': 'Transfer exact source motion from <Video 1>, appearance from <Picture 1>.'}
            g = graph(p, 'preflight.mp4', ['view1.png', 'view2.png'], 'preflight')
            check = await check_graph(app.state.client, settings['comfy_url'], g, digest(VENDOR) == VENDOR_SHA,
                                      skip_inputs={('7', 'file'), ('30', 'image'), ('31', 'image')})
            vocal={'ok':False,'errors':['La interpretación vocal requiere H3 base de 20 pasos.']}
            if preset=='base':
                vocal_graph=graph({**p,'motion_vocal_performance':True},'preflight.mp4',['view1.png'],'preflight-vocal','preflight.wav')
                vocal=await check_graph(app.state.client,settings['comfy_url'],vocal_graph,digest(VENDOR)==VENDOR_SHA,
                                         skip_inputs={('7','file'),('30','image'),('40','audio')})
            return {**check, 'maximum_frames': MAX_FRAMES, 'segmented_maximum_frames':SEGMENTED_MAX_FRAMES, 'segmented_experimental':True,
                    'preset':preset,'steps':8 if preset=='turbo8' else 20, 'maximum_views': 3, 'automatic_alignment': False,'vocal_performance':vocal}
        except (httpx.HTTPError, ValueError):
            return JSONResponse({'ok': False, 'detail': 'No se pudo comprobar H3. Revisa ComfyUI.'}, status_code=503)

    @app.post('/api/jobs/{job_id}/motion-transfer-h3', status_code=202)
    async def animate(job_id: UUID, body: AnimateH3):
        if not settings['generation_enabled']:
            raise HTTPException(503, 'Generación desactivada.')
        service = app.state.jobs
        async with service.lock:
            try:
                primary, _ = reviewed_view(service, str(job_id), body.approved_anchor_sha256)
                if body.preset=='turbo8' and body.vocal_performance:
                    raise ValueError('La interpretación vocal requiere H3 base de 20 pasos.')
                p = primary['parameters']
                if p['frames'] > (SEGMENTED_MAX_FRAMES if body.segmented else MAX_FRAMES):
                    raise ValueError('H3 con varios ángulos admite hasta 107 fotogramas (4,46 s); usa tramos unidos para hasta 30 s.')
                if body.cut_frames and not body.segmented:raise ValueError('Los cortes manuales requieren H3 con tramos unidos.')
                if body.segmented:
                    from .motion_h3_plan import plan_segments
                    plan_segments(p['frames'],body.cut_frames)
                from .motion_transfer import inputs
                inputs(service, p)
                views = [{'job_id': primary['id'], 'sha256': body.approved_anchor_sha256, 'anchor_index': p['anchor_index']}]
                for view in body.additional_views:
                    other, _ = reviewed_view(service, str(view.job_id), view.sha256)
                    inputs(service,other['parameters'])
                    if not compatible(primary, other) or other['parameters']['anchor_index'] in [v['anchor_index'] for v in views]:
                        raise ValueError('Selecciona ángulos distintos del mismo vídeo, tramo, referencias y resolución.')
                    views.append({'job_id': other['id'], 'sha256': view.sha256, 'anchor_index': other['parameters']['anchor_index']})
                if body.motion_timing_notes.strip():p={**p,'motion_timing_notes':body.motion_timing_notes}
                if body.appearance_recipe=='style_v2' and p.get('scene_mode')=='restyle' and not p.get('original_reference') and not body.vocal_performance:
                    p={**p,'motion_appearance_recipe':body.appearance_recipe}
                payload = {**p, 'operation': 'motion_transfer', 'steps': 8 if body.preset=='turbo8' else 20, 'motion_anchor_id': primary['id'],
                    'anchor_sha256': body.approved_anchor_sha256, 'transfer_method': 'h3_multiview', 'motion_h3_views': views,
                    'lora_strength': body.lora_strength, 'timing_offset_frames': body.timing_offset_frames,
                    'motion_h3_prompt': prompt(p, views)}
                if body.preset=='turbo8':payload['motion_h3_preset']='turbo8'
                if body.vocal_performance:
                    asset=service.store.asset(p['video_id'])
                    if not asset or not asset.get('audio'):raise ValueError('La interpretación vocal necesita un vídeo con audio.')
                    payload.update(motion_vocal_performance=True,lora_strength=0)
                if body.segmented:
                    payload['motion_h3_segmented']=True
                    payload['motion_h3_cut_frames']=sorted(body.cut_frames)
                if body.reuse_first_take:
                    if not body.segmented or (body.cut_frames and min(body.cut_frames)<107):raise ValueError('La reutilización requiere tramos unidos y un primer plano de al menos107frames.')
                    reusable_first_take(service,payload,str(body.reuse_first_take.job_id),body.reuse_first_take.sha256)
                    payload.update(motion_h3_reuse_first_job=str(body.reuse_first_take.job_id),motion_h3_reuse_first_sha256=body.reuse_first_take.sha256)
                result = service.store.create_job(str(uuid4()), str(body.request_key), payload)
            except (ValueError, OSError, KeyError) as error:
                raise HTTPException(409, str(error)) from error
        service.wake.set()
        return result
