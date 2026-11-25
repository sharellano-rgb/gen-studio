"""Verified, cancellable 1080p copies; H3 results remain immutable."""
import asyncio
import json
import os
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path
from uuid import UUID, uuid4
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict

from .head import digest
from .job_cancel import checkpoint, thread_checkpoint
from .result_review import result_identity
from .workflows import ROOT

VERSION = 'realesrgan-x2plus-1080-v1'
MODEL = ROOT / 'data/models/upscale/RealESRGAN_x2plus.pth'
MODEL_SHA256 = '49fafd45f8fd7aa8d31ab2a22d14d91b536c34494a5cfe31eb5d89c2fa266abb'
MODEL_SIZE = 67061725
ALLOWED = {None, 'long_character', 'long_assembly', 'h3_face_refine', 'geometry_copy'}
METHOD_NAMES = {'natural': 'Natural · Lanczos'}
METHOD_VERSIONS = {'natural': 'natural-lanczos-1080-v1'}


class UpscaleRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_key: UUID
    method: Literal['natural'] = 'natural'


def enabled(settings):
    return bool(settings.get('generation_enabled') and Path(settings.get('face_python', '')).is_file()
                and MODEL.is_file() and MODEL.stat().st_size == MODEL_SIZE
                and shutil.which('ffmpeg') and shutil.which('ffprobe'))


def method_enabled(settings, method):
    return bool(method == 'natural' and settings.get('generation_enabled')
                and shutil.which('ffmpeg') and shutil.which('ffprobe'))


def method_options(settings):
    descriptions = {
        'natural': 'Conserva la apariencia original. Más rápido y suave, sin reconstruir texturas con IA.',
    }
    result = []
    for method, name in METHOD_NAMES.items():
        ready = method_enabled(settings, method)
        result.append(dict(id=method, name=name, enabled=ready,
            reason='' if ready else 'Este escalador local no está disponible.', description=descriptions[method]))
    return result


def method_for_job(parameters):
    method = parameters.get('upscale_method_id')
    if method is None and parameters.get('upscale_version') == VERSION:
        return None  # Preserve existing Real-ESRGAN queued jobs and recovery records.
    if method not in METHOD_NAMES or parameters.get('upscale_version') != METHOD_VERSIONS[method]:
        raise ValueError('El método de escalado registrado no es compatible.')
    return method


def media(path):
    run = subprocess.run([shutil.which('ffprobe') or 'ffprobe', '-v', 'error', '-count_frames',
        '-show_entries', 'stream=codec_type,width,height,nb_read_frames,avg_frame_rate,r_frame_rate,sample_aspect_ratio:stream_side_data=rotation',
        '-of', 'json', str(path)], capture_output=True, text=True, timeout=120,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if run.returncode:
        raise ValueError('No se pudo leer el resultado que se quiere escalar.')
    streams = json.loads(run.stdout).get('streams', [])
    videos = [s for s in streams if s.get('codec_type') == 'video']
    if len(videos) != 1:
        raise ValueError('El resultado debe contener una sola pista de vídeo.')
    v = videos[0]
    if (Fraction(v['avg_frame_rate']) != 24 or Fraction(v['r_frame_rate']) != 24
            or v.get('sample_aspect_ratio', '1:1') not in ('1:1', 'N/A')
            or any(float(s.get('rotation', 0)) % 360 for s in v.get('side_data_list', []))):
        raise ValueError('La copia requiere un resultado H3 a 24 fps, con píxeles cuadrados y orientación normalizada.')
    return dict(width=int(v['width']), height=int(v['height']), frames=int(v['nb_read_frames']),
                fps=24, audio=any(s.get('codec_type') == 'audio' for s in streams))


def target_size(width, height):
    return (1920, 1080) if width > height else (1080, 1920) if height > width else (1080, 1080)


def source(service, parent):
    if not parent or parent['parameters'].get('engine') != 'h3_direct' or parent['parameters'].get('operation') not in ALLOWED:
        raise ValueError('Selecciona un resultado terminado de H3 directo para crear la copia a 1080p.')
    sha, frames = result_identity(service, parent)
    path = service.root / 'results' / (parent['id'] + '.mp4')
    info = media(path)
    p = parent['parameters']
    start = p.get('long_context_frames', 0) if p.get('operation') in (None, 'geometry_copy') else 0
    if (type(start) is not int or start not in (0, 1) or not 1 <= frames <= 1440
            or info['frames'] != p.get('frames') or start + frames > info['frames']):
        raise ValueError('El resultado no coincide con los fotogramas registrados; no se escala otra versión.')
    w, h = target_size(info['width'], info['height'])
    if not 0 < min(info['width'], info['height']) or max(info['width'], info['height']) > 4096:
        raise ValueError('Resolución de origen no compatible.')
    if min(w / info['width'], h / info['height']) <= 1:
        raise ValueError('Este resultado ya alcanza o supera el tamaño de salida de 1080p.')
    return dict(path=path, sha256=sha, **info, start_frame=start, output_frames=frames,
                target_width=w, target_height=h)


def payload(parent, info, method=None):
    # Deliberately exclude scheduler IDs, generation settings and old correction state.
    result = dict(engine='h3_direct', operation='upscale_1080', upscale_version=VERSION,
                upscale_method='Real-ESRGAN ×2', upscale_parent_id=parent['id'],
                upscale_source_sha256=info['sha256'], upscale_source_start_frame=info['start_frame'],
                upscale_source_frames=info['frames'], upscale_source_width=info['width'],
                upscale_source_height=info['height'], upscale_source_audio=info['audio'],
                upscale_model_sha256=MODEL_SHA256, frames=info['output_frames'],
                resolution=f"{info['target_width']}x{info['target_height']}")
    if method is not None:
        if method not in METHOD_NAMES:
            raise ValueError('El método de escalado no es compatible.')
        result.update(upscale_method_id=method, upscale_version=METHOD_VERSIONS[method],
                      upscale_method=METHOD_NAMES[method],
                      upscale_model_sha256='')
    return result


def original(service, job):
    parent = service.store.job(job['parameters']['upscale_parent_id'])
    if not parent or parent['parameters'].get('engine') != 'h3_direct':
        raise ValueError('El resultado original no está disponible en este espacio.')
    sha, _ = result_identity(service, parent)
    if sha != job['parameters']['upscale_source_sha256']:
        raise ValueError('El original cambió después de solicitar la copia.')
    return service.root / 'results' / (parent['id'] + '.mp4')


def verify_result(service, job):
    if job['parameters'].get('operation') != 'upscale_1080':
        raise ValueError('Este trabajo no es una copia a 1080p.')
    result_identity(service, job)
    return service.root / 'results' / (job['id'] + '.mp4')


def register(app, settings):
    @app.get('/api/jobs/{job_id}/upscale-options')
    async def options(job_id: UUID):
        service = app.state.jobs
        parent = service.store.job(str(job_id))
        if not parent:
            raise HTTPException(404, 'Trabajo no encontrado.')
        try:
            info = await asyncio.to_thread(source, service, parent)
            methods = method_options(settings)
            ready = any(item['enabled'] for item in methods)
            return dict(enabled=ready, reason='' if ready else 'El escalador local no está disponible.',
                        width=info['target_width'], height=info['target_height'],
                        source_width=info['width'], source_height=info['height'],
                        resolution=f"{info['target_width']}x{info['target_height']}",
                        frames=info['output_frames'], duration=info['output_frames'] / 24,
                        method=METHOD_NAMES['natural'], methods=methods, default_method='natural')
        except (ValueError, KeyError, OSError) as error:
            return dict(enabled=False, reason=str(error))

    @app.post('/api/jobs/{job_id}/upscale-1080', status_code=202)
    async def submit(job_id: UUID, body: UpscaleRequest):
        service = app.state.jobs
        async with service.lock:
            parent = service.store.job(str(job_id))
            if not parent:
                raise HTTPException(404, 'Trabajo no encontrado.')
            if not method_enabled(settings, body.method):
                raise HTTPException(503, f'{METHOD_NAMES[body.method]} no está disponible en este equipo.')
            try:
                info = await asyncio.to_thread(source, service, parent)
                child = service.store.create_job(str(uuid4()), str(body.request_key), payload(parent, info, body.method))
            except (ValueError, KeyError, OSError) as error:
                raise HTTPException(409, str(error)) from error
            service.wake.set()
            return child


def validate_report(report, saved, p):
    w, h = map(int, p['resolution'].split('x'))
    method = method_for_job(p)
    expected = dict(version=p['upscale_version'], source_sha256=p['upscale_source_sha256'],
                    model_sha256=p['upscale_model_sha256'], frames=p['frames'], width=w, height=h, fps=24)
    if method:
        expected.update(method=method, source_start_frame=p['upscale_source_start_frame'],
                        source_frames=p['upscale_source_frames'])
    if any(report.get(k) != v for k, v in expected.items()) or not saved.is_file() or digest(saved) != report.get('result_sha256'):
        raise ValueError('La copia o su registro cambió; se conserva para diagnóstico.')
    actual = media(saved)
    if any(actual[k] != expected[k] for k in ('frames', 'width', 'height', 'fps')) or actual['audio'] != p['upscale_source_audio']:
        raise ValueError('La copia no conserva los fotogramas, audio o resolución esperados.')


def publish(saved, target, expected):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if digest(target) != expected:
            raise ValueError('Ya existe otra salida; se conserva sin sobrescribirla.')
        return
    # Atomic publication without replacing any existing result.
    temporary = target.with_suffix('.' + str(uuid4()) + '.tmp')
    try:
        shutil.copyfile(saved, temporary)
        if digest(temporary) != expected:
            raise ValueError('No se pudo verificar la copia antes de publicarla.')
        os.link(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


async def process(service, job):
    p = job['parameters']
    method = method_for_job(p)
    parent = service.store.job(p['upscale_parent_id'])
    info = await thread_checkpoint(service, job['id'], source, service, parent)
    if payload(parent, info, method) != p:
        raise ValueError('Cambió el original o la configuración de la copia a 1080p.')
    folder = service.root / 'jobs' / job['id']
    folder.mkdir(parents=True, exist_ok=True)
    target = service.root / 'results' / (job['id'] + '.mp4')
    record = folder / 'upscale-report.json'
    if record.exists():
        report = json.loads(record.read_text(encoding='utf-8'))
        saved = Path(report['saved_video']).resolve()
        if not saved.is_relative_to(folder.resolve()):
            raise ValueError('La salida registrada no pertenece a este trabajo.')
    else:
        if method is None:
            if not enabled(service.settings) or await thread_checkpoint(service, job['id'], digest, MODEL) != MODEL_SHA256:
                raise ValueError('El modelo de escalado no coincide con el SHA-256 instalado.')
        elif not method_enabled(service.settings, method):
            raise ValueError(f'{METHOD_NAMES[method]} no está disponible en este equipo.')
        checkpoint(service, job['id'])
        if not await service.idle():
            raise ValueError('El motor compartido está ocupado. Espera a que termine antes de escalar.')
        if method != 'natural':
            await service.release_ollama(job['id'])
            checkpoint(service, job['id'])
            await service.post(service.settings['comfy_url'], '/free', json={'unload_models': True, 'free_memory': True})
        checkpoint(service, job['id'])
        run = folder / ('upscale-' + str(uuid4()))
        run.mkdir()
        w, h = map(int, p['resolution'].split('x'))
        manifest = dict(source=str(info['path']), source_sha256=info['sha256'], model=str(MODEL),
                        model_sha256=MODEL_SHA256, run_root=str(run), parent_pid=os.getpid(),
                        frames=info['frames'], width=info['width'], height=info['height'],
                        target_width=w, target_height=h, start_frame=info['start_frame'],
                        output_frames=info['output_frames'], fps=24)
        if method:
            manifest.update(method=method, version=METHOD_VERSIONS[method])
            manifest.pop('model')
            manifest.pop('model_sha256')
        cfg = run / 'manifest.json'
        cfg.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        child = None
        with (run / 'stderr.log').open('wb') as log:
            try:
                label = METHOD_NAMES.get(method, 'Real-ESRGAN ×2')
                service.store.update(job['id'], status='refining', stage=f'Preparando copia a 1080p · {label}', progress=None)
                runtime = sys.executable if method == 'natural' else service.settings['face_python']
                script = 'upscale_alternative_video.py' if method else 'upscale_video.py'
                child = await asyncio.create_subprocess_exec(runtime, '-B', '-u',
                    str(ROOT / 'scripts' / script), str(cfg), stdout=asyncio.subprocess.PIPE, stderr=log,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                    env={**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'PYTHONIOENCODING': 'utf-8'})
                while line := await child.stdout.readline():
                    checkpoint(service, job['id'])
                    try:
                        event = json.loads(line)
                        if event.get('type') == 'progress':
                            service.store.update(job['id'], stage=event['stage'],
                                progress={'value': event['frame'], 'max': event['max']})
                    except (ValueError, KeyError):
                        pass
                if await child.wait():
                    error = run / 'error.json'
                    raise ValueError(json.loads(error.read_text(encoding='utf-8'))['error'] if error.exists()
                                     else 'Falló el escalado; se conserva el resultado original.')
            finally:
                if child and child.returncode is None:
                    try:
                        (run / 'cancel.request').touch()
                        try:
                            await asyncio.wait_for(child.wait(), 10)
                        except TimeoutError:
                            child.kill()
                            await asyncio.wait_for(child.wait(), 3)
                            service.store.update(job['id'], cancel_local_cleanup_error='El escalador necesitó una parada forzada; no se confirmó la limpieza de sus procesos auxiliares.')
                    except BaseException:
                        service.store.update(job['id'], cancel_local_cleanup_error='No se confirmó la limpieza completa del escalador local.')
                        raise
        checkpoint(service, job['id'])
        saved = run / 'upscaled.mp4'
        report = json.loads((run / 'report.json').read_text(encoding='utf-8'))
    await thread_checkpoint(service, job['id'], validate_report, report, saved, p)
    await thread_checkpoint(service, job['id'], original, service, job)
    if not record.exists():
        report['saved_video'] = str(saved)
        record.write_text(json.dumps(report, indent=2), encoding='utf-8')
    await thread_checkpoint(service, job['id'], publish, saved, target, report['result_sha256'])
    checkpoint(service, job['id'])
    service.store.update(job['id'], status='completed', stage='Copia a 1080p lista · Compara nitidez y movimiento',
        result_available=True, progress=None, scene_resolution=p['resolution'],
        upscale_result_sha256=report['result_sha256'], upscale_elapsed_seconds=report.get('elapsed_seconds'),
        upscale_review_required=True)
    service.event(job['id'], f"Copia {METHOD_NAMES.get(method, 'Real-ESRGAN ×2')} a 1080p terminada. Se conservan los fotogramas visibles y el audio; el original no cambió.")
