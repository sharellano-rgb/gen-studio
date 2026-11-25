"""Owner-only, read-only access to guests’ generated videos and images."""
import asyncio
import hashlib
import json
import re
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException, Query
from fastapi.responses import FileResponse
from .workspaces import current_workspace


def _image_path(target, job):
    """Use the image publisher's binding and integrity checks in Sonny's store."""
    from .image_generation import result_path

    root = target.root.resolve(strict=True)
    inputs = (root / 'inputs').resolve(strict=True)
    if not inputs.is_relative_to(root) or inputs == root:
        raise ValueError('Image outside workspace')
    path = result_path(target, job).resolve(strict=True)
    if not path.is_relative_to(inputs) or path.suffix.lower() != '.png':
        raise ValueError('Image outside workspace')
    return path


def _comparison_path(target, job_id):
    """Validate an administrative review manifest without changing either store."""
    def require(condition):
        if not condition:
            raise ValueError('Comparison unavailable')

    def strict_object(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result)
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError('Invalid JSON constant')

    def digest(path):
        with path.open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest()

    root = target.root.resolve(strict=True)
    diagnostics = (root / 'diagnostics').resolve(strict=True)
    require(diagnostics.is_relative_to(root) and diagnostics != root)
    review_root = (diagnostics / 'video-reviews').resolve(strict=True)
    require(review_root.is_relative_to(diagnostics))
    manifest_path = (review_root / (job_id + '.json')).resolve(strict=True)
    require(manifest_path.is_relative_to(review_root) and manifest_path.is_file())
    require(manifest_path.stat().st_size <= 16384)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'),
                          object_pairs_hook=strict_object, parse_constant=invalid_constant)
    fields = {'version', 'parent_job_id', 'parent_result_sha256',
              'artifact', 'artifact_sha256', 'kind'}
    require(isinstance(manifest, dict) and set(manifest) == fields)
    require(type(manifest['version']) is int and manifest['version'] == 1)
    require(manifest['kind'] == 'visible-mouth-comparison' and manifest['parent_job_id'] == job_id)
    for field in ('parent_result_sha256', 'artifact_sha256'):
        require(isinstance(manifest[field], str) and re.fullmatch(r'[0-9a-f]{64}', manifest[field]))
    job = target.store.job(job_id)
    require(isinstance(job, dict) and job.get('id') == job_id and job.get('status') == 'completed'
            and job.get('result_available') is True)
    parameters = job.get('parameters')
    require(isinstance(parameters, dict) and parameters.get('engine') == 'h3_direct')
    require(job.get('direct_result_sha256') == manifest['parent_result_sha256'])
    results = (root / 'results').resolve(strict=True)
    require(results.is_relative_to(root) and results != root)
    parent_result = (results / (job_id + '.mp4')).resolve(strict=True)
    require(parent_result.is_relative_to(results) and parent_result.is_file())
    require(digest(parent_result) == manifest['parent_result_sha256'])
    require(isinstance(manifest['artifact'], str) and manifest['artifact'])
    relative = Path(manifest['artifact'])
    require(not relative.anchor and '..' not in relative.parts)
    artifact = (root / relative).resolve(strict=True)
    require(artifact.is_relative_to(diagnostics) and artifact.is_file() and artifact.suffix.lower() == '.mp4')
    require(digest(artifact) == manifest['artifact_sha256'])
    return artifact


def register(app, result_response, workspace_id='sonny'):
    video_base = '/api/sonny-videos' if workspace_id == 'sonny' else f'/api/admin/workspaces/{workspace_id}/videos'
    image_base = '/api/sonny-images' if workspace_id == 'sonny' else f'/api/admin/workspaces/{workspace_id}/images'
    def service():
        if current_workspace.get() != 'owner':
            raise HTTPException(403, 'Esta vista solo está disponible para el propietario.')
        target = app.state.jobs.services.get(workspace_id)
        if target is None:
            raise HTTPException(404, 'El espacio solicitado no está disponible.')
        return target

    @app.get(video_base)
    async def videos(before: int | None = Query(default=None, ge=1)):
        target = service()
        with target.store.connect() as db:
            rows = db.execute(
                "SELECT rowid, state FROM jobs WHERE json_extract(state, '$.result_available') = 1 "
                "AND json_extract(state, '$.status') IN ('completed','failed','cancelled') "
                "AND COALESCE(json_extract(payload, '$.operation'), '') != 'image_generation' "
                "AND COALESCE(json_extract(payload, '$.engine'), '') != 'moss_image' "
                "AND (? IS NULL OR rowid < ?) ORDER BY rowid DESC LIMIT 51", (before, before)
            ).fetchall()
        page = [(rowid, json.loads(state)) for rowid, state in rows[:50]]
        metadata = target.store.scene_metadata([job['id'] for _, job in page])
        items = []
        for _, job in page:
            params = job.get('parameters') or {}
            method = {'h3_direct':'H3 directo','h3_mdmz':'MDMZ Character Swap','h3_alisson':'Alisson',
                      'h3_ninja':'Refinamiento facial','viggle_motion':'Motion Transfer'}.get(params.get('engine'),'Vídeo')
            frames = params.get('frames')
            items.append({'id':job['id'], 'name':metadata.get(job['id'],{}).get('name') or method,
                          'method':method, 'created_at':job['created_at'],
                          'seconds':round(frames / 24, 2) if isinstance(frames,(int,float)) else None,
                          'resolution':job.get('scene_resolution') or params.get('resolution'),
                          'url':video_base+'/'+job['id']+'/result'})
        return {'videos':items, 'next_before':page[-1][0] if len(rows)>50 else None}

    @app.get(video_base + '/{job_id}/result')
    async def video(job_id: UUID, download: bool = False):
        target = service()
        job = target.store.job(str(job_id))
        if (not job or not job.get('result_available')
                or job['status'] not in {'completed','failed','cancelled'}
                or job['parameters'].get('operation') == 'image_generation'
                or job['parameters'].get('engine') == 'moss_image'):
            raise HTTPException(404, 'Vídeo no disponible en el espacio solicitado.')
        # Resolve the existing final-result behavior inside this request only.
        token = current_workspace.set(workspace_id)
        try:
            response = await result_response(job_id, download=download, variant='final')
            if not Path(response.path).resolve().is_relative_to(target.root.resolve()):
                raise HTTPException(404, 'Vídeo fuera del espacio solicitado.')
            return response
        finally:
            current_workspace.reset(token)

    @app.get(image_base)
    async def images(before: int | None = Query(default=None, ge=1)):
        target = service()
        with target.store.connect() as db:
            rows = db.execute(
                "SELECT rowid, state FROM jobs WHERE json_extract(payload, '$.operation') = 'image_generation' "
                "AND json_extract(state, '$.result_available') = 1 "
                "AND json_extract(state, '$.status') = 'completed' "
                "AND (? IS NULL OR rowid < ?) ORDER BY rowid DESC LIMIT 51", (before, before)
            ).fetchall()
        page = [(rowid, json.loads(state)) for rowid, state in rows[:50]]
        metadata = target.store.scene_metadata([job['id'] for _, job in page])
        items = []
        for _, job in page:
            params = job.get('parameters') or {}
            model = 'Qwen Image 2.1' if params.get('engine') == 'qwen_image21' else 'MOSS'
            action = {'edit': 'Edición', 'upscale': 'Upscale'}.get(params.get('mode'), 'Imagen')
            method = model + ' · ' + action
            items.append({'id': job['id'], 'name': metadata.get(job['id'], {}).get('name') or method,
                          'method': method, 'created_at': job['created_at'],
                          'resolution': params.get('resolution'),
                          'url': image_base + '/' + job['id'] + '/result'})
        return {'images': items, 'next_before': page[-1][0] if len(rows) > 50 else None}

    @app.get(image_base + '/{job_id}/result')
    async def image(job_id: UUID, download: bool = False):
        target = service()
        job = target.store.job(str(job_id))
        if (not job or job.get('parameters', {}).get('operation') != 'image_generation'
                or job.get('status') != 'completed' or not job.get('result_available')):
            raise HTTPException(404, 'Imagen no disponible en el espacio solicitado.')
        try:
            path = await asyncio.to_thread(_image_path, target, job)
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise HTTPException(404, 'Imagen no disponible en el espacio solicitado.') from error
        return FileResponse(path, media_type='image/png',
                            filename='Gen-Studio-' + workspace_id.title() + '-' + job['id'][:8] + '.png' if download else None,
                            content_disposition_type='attachment' if download else 'inline',
                            headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})


    @app.get(video_base + '/{job_id}/comparison')
    async def comparison(job_id: UUID):
        target = service()
        try:
            path = await asyncio.to_thread(_comparison_path, target, str(job_id))
        except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
            raise HTTPException(404, 'Comparación no disponible.') from error
        return FileResponse(path, media_type='video/mp4',
                            headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})
