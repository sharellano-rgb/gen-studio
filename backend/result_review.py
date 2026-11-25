"""Frame-specific human observations, bound to one immutable result."""
import asyncio
import json
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, Field, ConfigDict
from .head import digest
from .store import now


class NoteRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    expected_revision: int = Field(ge=0, strict=True)
    result_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    frame: int = Field(ge=0, strict=True)
    category: Literal['face', 'clothing', 'hands', 'background', 'motion', 'join', 'other']
    subject: str = Field(default='', max_length=180)
    comment: str = Field(default='', max_length=1000)
    status: Literal['open', 'resolved'] = 'open'


def result_identity(service, job):
    if not job or job['status'] != 'completed' or not job.get('result_available'):
        raise ValueError('Selecciona un resultado terminado para revisar sus fotogramas.')
    p = job['parameters']
    engine, operation = p.get('engine'), p.get('operation')
    if engine == 'h3_direct':
        if operation in ('long_character', 'long_assembly'):
            path = service.root / 'jobs' / job['id'] / 'long-result.json'
            expected = json.loads(path.read_text(encoding='utf-8'))['sha256']
        elif operation == 'geometry_copy':
            expected = job.get('geometry_result_sha256')
        elif operation == 'upscale_1080':
            expected = job.get('upscale_result_sha256')
        elif operation == 'h3_face_refine':
            expected = job.get('h3_face_result_sha256')
        elif operation is None:
            expected = job.get('direct_result_sha256')
        else:
            raise ValueError('Este tipo de resultado no admite revisión por fotogramas.')
    elif engine == 'viggle_remake' and operation in ('viggle_swap','viggle_motion'):
        expected = job.get('viggle_result_sha256')
    elif engine == 'pika_ref2va' and operation in ('pika_swap','pika_motion'):
        expected = job.get('pika_result_sha256')
    elif engine == 'viggle_motion' and operation in ('motion_hybrid', 'motion_bunny', 'motion_layers'):
        expected = job.get('motion_result_sha256')
    elif engine == 'h3_mdmz' and operation == 'mdmz_swap':
        expected = job.get('mdmz_result_sha256')
    elif engine == 'h3_alisson' and operation in (None, 'alisson_swap'):
        expected = job.get('alisson_result_sha256')
    elif engine == 'h3_ninja' and operation in ('ninja_render', 'ninja_shots_render'):
        expected = job.get('ninja_result_sha256')
    else:
        raise ValueError('Este tipo de resultado no admite revisión por fotogramas.')
    frames = p.get('long_keep_frames', p.get('frames'))
    if type(frames) is not int or frames < 1 or frames > p.get('frames', 0):
        raise ValueError('La duración registrada del resultado no es válida.')
    output = service.root / 'results' / (job['id'] + '.mp4')
    if not expected or not output.is_file() or digest(output) != expected:
        raise ValueError('El resultado cambió. No se guardan marcas sobre otro archivo.')
    return expected, frames


def review_data(service, job):
    sha, frames = result_identity(service, job)
    review = job.get('quality_review') or dict(version='frame-review-v1', revision=0, result_sha256=sha, notes=[])
    if review.get('result_sha256') != sha:
        raise ValueError('Las marcas guardadas pertenecen a otro resultado.')
    segments = []
    if job['parameters'].get('operation') in ('long_character', 'long_assembly'):
        from .long_video import parent_of, load_plan
        parent = parent_of(service, job)
        for s in (load_plan(service, parent) or {}).get('segments', []):
            segments.append({k: s[k] for k in ('index', 'start', 'keep', 'passthrough', 'job_id')})
    return dict(job_id=job['id'], frames=frames, review=review, segments=segments)


def register(app):
    @app.get('/api/jobs/{job_id}/quality-notes')
    async def get_notes(job_id: UUID):
        service = app.state.jobs
        job = service.store.job(str(job_id))
        if not job:
            raise HTTPException(404, 'Trabajo no encontrado.')
        try:
            return await asyncio.to_thread(review_data, service, job)
        except (ValueError, KeyError, OSError) as error:
            raise HTTPException(409, str(error)) from error

    @app.put('/api/jobs/{job_id}/quality-notes/{note_id}')
    async def save_note(job_id: UUID, note_id: UUID, body: NoteRequest):
        service = app.state.jobs
        async with service.lock:
            job = service.store.job(str(job_id))
            if not job:
                raise HTTPException(404, 'Trabajo no encontrado.')
            try:
                data = await asyncio.to_thread(review_data, service, job)
                review = data['review']
                if body.result_sha256 != review['result_sha256']:
                    raise ValueError('La marca pertenece a otra versión del vídeo.')
                if body.frame >= data['frames']:
                    raise ValueError('El fotograma está fuera del intervalo visible del resultado.')
                values = body.model_dump(exclude={'expected_revision', 'result_sha256'})
                existing = next((n for n in review['notes'] if n['id'] == str(note_id)), None)
                # Retrying a confirmed save is idempotent, even after a lost response.
                if existing and all(existing.get(k) == v for k, v in values.items()):
                    return data
                if body.expected_revision != review['revision']:
                    raise ValueError('Las marcas cambiaron en otra revisión. Actualiza las marcas antes de guardar.')
                if not existing and len(review['notes']) >= 200:
                    raise ValueError('Esta toma ya tiene 200 marcas. Reutiliza una marca existente.')
                timestamp = now()
                note = dict(id=str(note_id), **values, created_at=existing['created_at'] if existing else timestamp,
                            updated_at=timestamp)
                notes = [n for n in review['notes'] if n['id'] != str(note_id)] + [note]
                updated = dict(**review)
                updated.update(revision=review['revision'] + 1, notes=sorted(notes, key=lambda n: (n['frame'], n['id'])))
                service.store.update(job['id'], quality_review=updated)
                return {**data, 'review': updated}
            except (ValueError, KeyError, OSError) as error:
                raise HTTPException(409, str(error)) from error
