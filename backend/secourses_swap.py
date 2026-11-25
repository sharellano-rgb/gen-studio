"""Independent adapter for the pinned SECourses Genjutsu nodes."""
import asyncio
import json
import math
import shutil
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from .head import digest
from .media import probe
from .ninja_swap import asset_path, upload_inputs
from .direct_character import cpu
from .guided_head import phase
from .job_cancel import checkpoint, thread_checkpoint
from . import visual_methods
from . import secourses_target
from .qwen_models import default_model_id

VERSION = 'secourses-genjutsu-3'
REVISION = '36633e1cc91679095dd265534f859e2c963746cf'
MODELS = {'fast': 'fasth3_omniref_8step_pruned_int8_convrot.safetensors',
          'standard': 'minimax_h3_ref2va_pruned_int8_convrot.safetensors'}
ENCODER = 'qwen3vl_32b_minimax_h3_int8_convrot.safetensors'
DESCRIBER = 'qwen3vl_8b_int8_convrot.safetensors'
SAM = 'sam3.1_multiplex_fp16.safetensors'
BACKGROUNDS = {'video': 'video (character replacement)', 'image': 'image (motion transfer)'}


class Request(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_key: UUID
    video_id: UUID
    reference_id: UUID
    variant: Literal['fast', 'standard'] = 'fast'
    visual_method: Literal['experimental','faithful'] | None = None
    background: Literal['video', 'image'] = 'video'
    start_seconds: float = Field(default=0, ge=0, le=3600, allow_inf_nan=False)
    frames: int = Field(default=120, ge=5, le=1440, strict=True)
    megapixels: Literal[0.3, 0.5, 1.0] = 1.0
    guide_megapixels: float = Field(default=1.0, ge=0.05, le=1, allow_inf_nan=False)
    threshold: float = Field(default=0.5, ge=0.05, le=0.95, allow_inf_nan=False)
    mask_grow: int = Field(default=0, ge=0, le=64, strict=True)
    target: str = Field(default='person', min_length=1, max_length=200)
    prompt: str = Field(default='', max_length=12000)
    speech: str = Field(default='', max_length=2000)
    speech_language: str = Field(default='Spanish', min_length=1, max_length=80)
    auto_describe: bool = True
    qwen_adaptive_zoom: bool = Field(default=False, strict=True)
    qwen_motion_grounding: bool = Field(default=False, strict=True)
    lock_audio: bool = True
    seed: int = Field(default=42, ge=0, le=9007199254740991, strict=True)


def geometry(info, megapixels):
    w, h = info.get('display_width') or info['width'], info.get('display_height') or info['height']
    area = 1344 * 768 if megapixels == 1.0 else megapixels * 1_000_000
    scale = math.sqrt(area / (w * h))
    return max(32, round(w * scale / 32) * 32), max(32, round(h * scale / 32) * 32)


def graph(p, video, reference, ident, audio=False):
    def n(kind, **inputs): return {'class_type': kind, 'inputs': inputs}
    g = {
        'video': n('LoadVideo', file=video),
        'components': n('GetVideoComponents', video=['video', 0]),
        'reference': n('LoadImage', image=reference),
        'sam': n('CheckpointLoaderSimple', ckpt_name=SAM),
        'guide': n('SECoursesH3GenjutsuMotionGuide', images=['components', 0], sam3_model=['sam', 0], sam3_clip=['sam', 1],
            target=p['target'], background=BACKGROUNDS[p['background']], guide_megapixels=p['guide_megapixels'],
            threshold=p['threshold'], mask_grow=p['mask_grow'], refine_iterations=2,
            width=p['width'], height=p['height'], match_source_aspect=False),
        'prompt': n('SECoursesH3GenjutsuPrompt' if p.get('secourses_version') in ('secourses-genjutsu-1', VERSION) else 'GenStudioSECoursesPosePrompt', prompt=p['prompt'], background=BACKGROUNDS[p['background']],
            images=['components', 0], character=['reference', 0], speech=p['speech'], speech_language=p['speech_language']),
        'model': n('UNETLoader', unet_name=MODELS[p['variant']], weight_dtype='default'),
        'chunk': n('MiniMaxChunkFeedForward', model=['model', 0], chunks=2, seq_threshold=4096),
        'shift': n('MiniMaxH3SigmaShift', model=['chunk', 0], shift_video=12., shift_audio=3.),
        'clip': n('CLIPLoader', clip_name=ENCODER, type='minimax', device='default'),
        'vae': n('VAELoader', vae_name='minimax_h3_video_vae_fp16.safetensors'),
        'audio-vae': n('VAELoader', vae_name='minimax_h3_audio_vae_fp32.safetensors'),
        'condition': n('MiniMaxH3ReferenceToVideo', clip=['clip', 0], vae=['vae', 0], audio_vae=['audio-vae', 0],
            prompt=['prompt', 0], width=['guide', 3], height=['guide', 4], length=['guide', 2], ref_image_size='match',
            **{'ref_videos.ref_video_0': ['guide', 0], 'ref_images.ref_image_0': ['reference', 0]}),
        'guider': n('BasicGuider', model=['shift', 0], conditioning=['condition', 0]),
        # Sampler/schedule/shifts verified against the author's V84 presets.
        'schedule': n('BasicScheduler', model=['shift', 0], scheduler='simple', steps=8 if p['variant']=='fast' else 20, denoise=1.),
        'sampler': n('KSamplerSelect', sampler_name='euler' if p['variant']=='fast' else 'res_multistep'),
        'noise': n('RandomNoise', noise_seed=p['seed']),
        'sample': n('SamplerCustomAdvanced', noise=['noise', 0], guider=['guider', 0], sampler=['sampler', 0],
            sigmas=['schedule', 0], latent_image=['condition', 1]),
        'decode': n('VAEDecode', samples=['sample', 1], vae=['vae', 0]),
        'output': n('CreateVideo', images=['decode', 0], fps=24),
        'save': n('SaveVideo', video=['output', 0], filename_prefix=f'gen_studio/{ident}/secourses', format='mp4', codec='h264'),
    }
    if g['prompt']['class_type']=='GenStudioSECoursesPosePrompt':
        g['prompt']['inputs']['target']=p['target']
        g['prompt']['inputs']['pose_mask']=['guide',1]
    if p['variant']=='fast':
        g['speed'] = n('MiniMaxH3SpeedOptimizer', model=['shift', 0], first_block_cache=False,
            fbc_threshold=0.08, fbc_start_percent=0.15, fbc_end_percent=0.95, fbc_max_consecutive=3,
            sparse_attention='vsa', sparse_backend='native', sparse_extra_tokens=256,
            sparse_dense_last_steps=1, sparse_dense_steps_pct=0.20, sparse_dense_layers=2,
            sparse_tau=1., sparse_min_video_rows=4096, fbc_cache_device='gpu', verbose=True)
        g['guider']['inputs']['model'] = ['speed', 0]
        g['schedule']['inputs']['model'] = ['speed', 0]
        g['vae-speed'] = n('MiniMaxH3VAESpeedup', vae=['vae', 0], tile_batch_size=0)
        g['decode']['inputs']['vae'] = ['vae-speed', 0]
    if p.get('secourses_version')!=VERSION and p['auto_describe'] and 'subject_definitions:' not in p['prompt'].lower():
        g['describe'] = n('CLIPLoader', clip_name=DESCRIBER, type='stable_diffusion', device='default')
        g['prompt']['inputs']['describe_clip'] = ['describe', 0]
    if audio:
        g['guide']['inputs']['audio'] = ['components', 1]
        g['prompt']['inputs']['audio'] = ['guide', 5]
        g['condition']['inputs']['ref_video_audios.ref_video_audio_0'] = ['guide', 5]
        g['output']['inputs']['audio'] = ['guide', 5]
        if p['lock_audio']:
            g['audio-lock'] = n('SECoursesMiniMaxH3InitAudio', positive=['condition', 0], latent=['condition', 1],
                audio_vae=['audio-vae', 0], init_audio=['guide', 5], audio_conditioning='lock soundtrack + guide')
            g['guider']['inputs']['conditioning'] = ['audio-lock', 0]
            g['sample']['inputs']['latent_image'] = ['audio-lock', 1]
    if p.get('visual_method')=='faithful':
        from .visual_faithful import adapt_graph
        g=adapt_graph(g,p,ident)
    g=secourses_target.adapt_graph(g,p,ident)
    if p.get('flowhmr_control_version'):
        from .flowhmr_control import adapt_graph as apply_body_control
        g=apply_body_control(g,p,ident)
    return g


async def options(service,visual_method=None):
    info = await service.get(service.settings['comfy_url'], '/object_info')
    required = ('SECoursesH3GenjutsuMotionGuide','SECoursesH3GenjutsuPrompt','SECoursesMiniMaxH3InitAudio',
                'MiniMaxH3SpeedOptimizer','MiniMaxH3VAESpeedup','GenStudioSECoursesPosePrompt',secourses_target.GUARD_NODE)
    if visual_method=='faithful':
        from .visual_faithful import REQUIRED_NODES
        required=(*required,*REQUIRED_NODES)
    missing = [name for name in required if name not in info]
    for node, field, names in [('UNETLoader','unet_name',list(MODELS.values())),
                              ('CLIPLoader','clip_name',[ENCODER]),
                              ('CheckpointLoaderSimple','ckpt_name',[SAM]),
                              ('VAELoader','vae_name',['minimax_h3_video_vae_fp16.safetensors','minimax_h3_audio_vae_fp32.safetensors'])]:
        values = info.get(node, {}).get('input', {}).get('required', {}).get(field, [[]])[0]
        missing.extend(name for name in names if name not in values)
    return {'available': not missing, 'missing': missing, 'version': VERSION, 'revision': REVISION,
            'enabled': bool(service.settings.get('generation_enabled')), 'max_frames': 1440 if service.settings.get('video_sequences_enabled') else 360, 'analysis_model': service.store.qwen_preference()['model_id']}


def register(app, settings):
    @app.get('/api/secourses/options')
    async def get_options(visual_method: Literal['experimental','faithful'] | None = None):
        try: return await options(app.state.jobs,visual_method) if visual_method else await options(app.state.jobs)
        except httpx.HTTPError as error: raise HTTPException(503, 'No se pudo comprobar ComfyUI.') from error

    @app.post('/api/secourses/generate', status_code=202)
    async def submit(body: Request):
        service = app.state.jobs
        if not settings.get('generation_enabled'): raise HTTPException(503, 'Generación desactivada.')
        request = visual_methods.request_payload(body)
        async with service.lock:
            with service.store.connect() as db:
                row = db.execute('SELECT state FROM jobs WHERE request_key=?', (str(body.request_key),)).fetchone()
            if row:
                job = json.loads(row[0])
                if job['parameters'].get('secourses_request') != request: raise HTTPException(409, 'Esta clave corresponde a otra solicitud.')
                return job
            if service.enhancer_active: raise HTTPException(409, 'Espera a que termine el análisis actual.')
            try:
                visual_methods.validate(request,settings)
                if body.frames>360 and not settings.get('video_sequences_enabled'):raise ValueError('Activa los tramos para vídeos de más de 15 segundos.')
                if request.get('qwen_motion_grounding') and not request.get('qwen_adaptive_zoom'):
                    raise ValueError('Las indicaciones temporales requieren zoom y descripción automática de Qwen.')
                if request.get('qwen_adaptive_zoom'):
                    from .secourses_qwen import automatic
                    if not automatic(request):raise ValueError('El zoom adaptativo requiere la descripción automática de Qwen.')
                ready = await options(service,body.visual_method) if body.visual_method else await options(service)
                if not ready['available']: raise ValueError('Faltan componentes: ' + ', '.join(ready['missing']))
                source = asset_path(service, body.video_id, 'video')
                reference = asset_path(service, body.reference_id, 'image')
                info = await asyncio.to_thread(probe, source)
                start = round(body.start_seconds * 24)
                if start + body.frames > round(info['duration'] * 24) + 1: raise ValueError('El tramo supera la duración del vídeo.')
                w, h = geometry(info, body.megapixels)
                p = {**request, **visual_methods.versions(request), 'engine': 'h3_secourses', 'operation': 'secourses_swap', 'secourses_version': VERSION,
                    'secourses_request': request, 'upstream_revision': REVISION, 'width': w, 'height': h,
                    'resolution': f'{w}x{h}', 'steps': 8 if body.variant=='fast' else 20, 'start_frame': start,
                    'render_frames': body.frames+(5-body.frames)%17, 'preserve_face': False,
                    'source_sha256': await asyncio.to_thread(digest, source), 'reference_sha256': await asyncio.to_thread(digest, reference)}
                job = service.store.create_job(str(uuid4()), str(body.request_key), p)
                service.wake.set()
                return job
            except (OSError, ValueError, KeyError) as error: raise HTTPException(409, str(error)) from error


async def process(service, job):
    p = job['parameters']; ident = job['id']
    current = service.store.job(ident)
    if current.get('status')=='submission_unknown': return
    if p.get('secourses_version') not in ('secourses-genjutsu-1', 'secourses-genjutsu-2', VERSION): raise ValueError('Versión SECourses desconocida.')
    folder = service.root/'jobs'/ident; folder.mkdir(parents=True, exist_ok=True)
    source = asset_path(service, p['video_id'], 'video'); reference = asset_path(service, p['reference_id'], 'image')
    protected = {str(source): p['source_sha256'], str(reference): p['reference_sha256']}
    for path, sha in protected.items():
        if await asyncio.to_thread(digest, Path(path)) != sha: raise ValueError('Cambió una entrada; crea otra toma.')
    info = await asyncio.to_thread(probe, source)
    # Preserve native mask detail. Pad only to the model grid; trim the final output back to the requested duration.
    prepared = await cpu(service, job, 'prepare', {'source': str(source), 'references': [str(reference)], 'protected': protected,
        'width': max(2,int(info.get('display_width') or info['width'])//2*2),
        'height': max(2,int(info.get('display_height') or info['height'])//2*2),
        'frames': p['frames'], 'render_frames': p['render_frames'], 'start_frame': p['start_frame'],
        'crop_face': False, 'reference_type': 'auto', 'scene_cut_policy': 'warn'})
    checkpoint(service, ident)
    faithful=p.get('visual_method')=='faithful'
    names = None if faithful else await upload_inputs(service, folder, job, prepared)
    current = service.store.job(ident)
    saved = folder/'secourses-workflow.api.json'
    accepted = current.get('guided_phase')=='secourses' and current.get('prompt_id')
    if accepted and not saved.is_file(): raise ValueError('Falta el workflow aceptado; no se reenvía.')
    render_parameters = p
    if not accepted and p.get('secourses_version')==VERSION:
        from .secourses_qwen import automatic, analyze
        if automatic(p):
            analysis = await analyze(service, job, prepared)
            checkpoint(service, ident)
            render_parameters = {**p, 'prompt': analysis['prompt'], 'auto_describe': False}
            target_job=service.store.job(ident)
            if secourses_target.active(target_job):
                from .qwen_global import scoped
                target_context=await thread_checkpoint(service,ident,scoped,service,target_job)
                render_parameters=secourses_target.parameters(target_job,analysis,render_parameters,scoped_context=target_context)
    if faithful:
        from .visual_render_source import prepare as render_source
        render_prepared,render_sha=await render_source(service,job,prepared,accepted=bool(accepted))
        names=await upload_inputs(service,folder,job,render_prepared)
        render_parameters={**render_parameters,'visual_faithful_render_sha256':render_sha}
    if p.get('flowhmr_control_version') and not accepted:
        from .flowhmr_control import prepare as prepare_body_control
        render_parameters={**render_parameters,**(await prepare_body_control(service,job,prepared))}
    g = json.loads(saved.read_text(encoding='utf-8')) if accepted else graph(render_parameters, names['video'], names['reference'], ident, prepared['audio'])
    rendered = await phase(service, job, 'secourses', g, 'save', '.mp4')
    if rendered is None: return
    final = await cpu(service, job, 'finalize', {'video': rendered['path'], 'video_sha256': rendered['sha256'],
        'source': prepared['video'], 'source_sha256': prepared['files'][prepared['video']],
        'frames': p['frames'], 'render_frames': p['render_frames'], 'width': p['width'], 'height': p['height']})
    checkpoint(service, ident)
    for path, sha in protected.items():
        if await asyncio.to_thread(digest, Path(path)) != sha: raise ValueError('Cambió una entrada durante la generación.')
    target = service.root/'results'/(ident+'.mp4'); target.parent.mkdir(exist_ok=True)
    sha = final['files'][final['video']]
    if target.exists() and digest(target)!=sha: raise ValueError('Ya existe una salida distinta; no se sobrescribe.')
    if not target.exists(): await asyncio.to_thread(shutil.copy2, final['video'], target)
    service.store.update(ident, status='completed', result_available=True, progress=None, secourses_result_sha256=sha,
        scene_resolution=p['resolution'], stage=visual_methods.name(p)+' listo · Revisa identidad, movimiento y fondo')
