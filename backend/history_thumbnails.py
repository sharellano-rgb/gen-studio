"""Small cached previews, resolved through the existing workspace result checks."""
import asyncio
import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from fastapi.responses import FileResponse
from .workspaces import current_workspace


def create_preview(source, directory):
    source = Path(source).resolve(strict=True)
    stat = source.stat()
    key = hashlib.sha256(f'{source}|{stat.st_mtime_ns}|{stat.st_size}|v1'.encode()).hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (key + '.jpg')
    if target.is_file():
        return target
    executable = shutil.which('ffmpeg')
    if not executable:
        raise ValueError('FFmpeg no está disponible.')
    temporary = directory / (key + '.' + uuid4().hex + '.jpg')
    try:
        result = subprocess.run([
            executable, '-nostdin', '-hide_banner', '-loglevel', 'error',
            '-threads', '1', '-filter_threads', '1', '-i', str(source),
            '-frames:v', '1', '-vf', 'scale=480:480:force_original_aspect_ratio=decrease',
            '-q:v', '5', '-an', '-y', str(temporary)],
            capture_output=True, timeout=20,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode or not temporary.is_file() or not temporary.stat().st_size:
            raise ValueError('No se pudo preparar la vista previa.')
        if source.stat().st_mtime_ns != stat.st_mtime_ns or source.stat().st_size != stat.st_size:
            raise ValueError('El vídeo cambió durante la preparación.')
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def register(app, result_response, workspace_ids=()):
    slots = asyncio.Semaphore(2)

    async def preview(response, root):
        source = Path(response.path).resolve(strict=True)
        root = root.resolve(strict=True)
        if not source.is_relative_to(root):
            raise HTTPException(404, 'Vista previa fuera del espacio de trabajo.')
        try:
            async with slots:
                path = await asyncio.to_thread(create_preview, source, root / 'thumbnails')
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            raise HTTPException(404, 'Vista previa no disponible.') from error
        return FileResponse(path, media_type='image/jpeg')

    @app.get('/api/jobs/{job_id}/thumbnail')
    async def thumbnail(job_id: UUID, variant: Literal['final', 'motion-approved'] = 'final'):
        response = await result_response(job_id, download=False, variant=variant)
        return await preview(response, app.state.jobs.root)

    @app.get('/api/images/{job_id}/thumbnail')
    async def image_thumbnail(job_id: UUID):
        from .image_generation import image_job, result_path
        service = app.state.jobs
        job = image_job(service, job_id)
        try:
            path = await asyncio.to_thread(result_path, service, job)
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise HTTPException(404, 'Imagen no disponible.') from error
        return await preview(FileResponse(path), service.root)

    def register_guest(workspace_id):
        video_base = '/api/sonny-videos' if workspace_id == 'sonny' else f'/api/admin/workspaces/{workspace_id}/videos'
        image_base = '/api/sonny-images' if workspace_id == 'sonny' else f'/api/admin/workspaces/{workspace_id}/images'
        @app.get(image_base + '/{job_id}/thumbnail')
        async def sonny_image_thumbnail(job_id: UUID):
            from .sonny_gallery import _image_path
            if current_workspace.get() != 'owner':
                raise HTTPException(403, 'Esta vista solo está disponible para el propietario.')
            target = app.state.jobs.services.get(workspace_id)
            if target is None:
                raise HTTPException(404, 'El espacio solicitado no está disponible.')
            job = target.store.job(str(job_id))
            if (not job or job.get('parameters', {}).get('operation') != 'image_generation'
                    or job.get('status') != 'completed' or not job.get('result_available')):
                raise HTTPException(404, 'Imagen no disponible en el espacio solicitado.')
            try:
                path = await asyncio.to_thread(_image_path, target, job)
            except (OSError, ValueError, TypeError, KeyError) as error:
                raise HTTPException(404, 'Imagen no disponible.') from error
            return await preview(FileResponse(path), target.root)
    
        @app.get(video_base + '/{job_id}/thumbnail')
        async def sonny_thumbnail(job_id: UUID):
            if current_workspace.get() != 'owner':
                raise HTTPException(403, 'Esta vista solo está disponible para el propietario.')
            target = app.state.jobs.services.get(workspace_id)
            if target is None:
                raise HTTPException(404, 'El espacio solicitado no está disponible.')
            job = target.store.job(str(job_id))
            if (not job or not job.get('result_available') or job['status'] not in {'completed', 'failed', 'cancelled'}
                    or job['parameters'].get('operation') == 'image_generation'
                    or job['parameters'].get('engine') == 'moss_image'):
                raise HTTPException(404, 'Vídeo no disponible en el espacio solicitado.')
            token = current_workspace.set(workspace_id)
            try:
                response = await result_response(job_id, download=False, variant='final')
                return await preview(response, target.root)
            finally:
                current_workspace.reset(token)

    for workspace_id in dict.fromkeys(['sonny', *workspace_ids]):
        if workspace_id != 'owner':
            register_guest(workspace_id)
