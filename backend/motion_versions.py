"""Reviewed copies are bound to one completed take; originals remain untouched."""
import asyncio
import json
import re
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, Field

from .head import digest
from .store import now

ARTIFACT = r'[a-z0-9][a-z0-9-]{0,79}'
SHA = r'[a-f0-9]{64}'


class MotionReview(BaseModel):
    artifact_id: str = Field(pattern='^' + ARTIFACT + '$')
    expected_sha256: str = Field(pattern='^' + SHA + '$')
    approved: bool
    feedback: str = Field(min_length=1, max_length=1000)


def completed_take(service, job_id):
    job = service.store.job(job_id)
    if not job:
        raise ValueError('Toma no encontrada.')
    p = job['parameters']
    if p.get('engine') != 'viggle_motion' or p.get('operation') != 'motion_transfer' or job['status'] != 'completed' or not job.get('result_available'):
        raise ValueError('Se requiere una toma terminada de Motion Transfer.')
    return job


def validated_copy(service, job, artifact_id, expected_sha256=None):
    if not re.fullmatch(ARTIFACT, artifact_id):
        raise ValueError('Identificador de copia inválido.')
    root = (service.root / 'identity-tests').resolve()
    folder = (root / artifact_id).resolve()
    if folder.parent != root:
        raise ValueError('La copia debe estar dentro de GEN STUDIO.')
    report_path, video = folder / 'video-report.json', folder / 'corrected.mp4'
    if not report_path.is_file() or not video.is_file() or video.resolve().parent != folder:
        raise ValueError('Copia no disponible.')
    try:
        report = json.loads(report_path.read_text(encoding='utf-8'))
    except (ValueError, OSError) as error:
        raise ValueError('Informe de copia inválido.') from error
    p = job['parameters']
    if not report.get('review_eligible') or report.get('category') != 'motion_transfer_turn_test' or report.get('role') != 'candidate' or report.get('source_job') != job['id']:
        raise ValueError('Esta copia no pertenece a la toma o es sólo una comparación.')
    source = service.store.asset(p.get('video_id'))
    source_path = service.root / 'inputs' / source['filename'] if source else None
    anchor = service.root / 'jobs' / str(p.get('motion_anchor_id')) / 'motion-anchor.png'
    original = service.root / 'results' / (job['id'] + '.mp4')
    bindings = [
        (source_path, report.get('source_sha256'), p.get('source_sha256')),
        (anchor, report.get('appearance_sha256'), p.get('anchor_sha256')),
        (original, report.get('original_sha256'), job.get('motion_result_sha256')),
    ]
    for path, recorded, expected in bindings:
        if not recorded or recorded != expected or not path or not path.is_file() or digest(path) != recorded:
            raise ValueError('Cambió la fuente, apariencia o resultado original de esta toma.')
    if report.get('frames') != p.get('frames') or report.get('resolution') != p.get('resolution'):
        raise ValueError('La duración o resolución no corresponde a esta toma.')
    recorded = report.get('sha256', '')
    if not re.fullmatch(SHA, recorded) or (expected_sha256 and recorded != expected_sha256) or digest(video) != recorded:
        raise ValueError('La copia cambió después de la revisión; requiere otra revisión.')
    return video, report, report_path


def approved_result(service, job):
    completed_take(service, job['id'])
    review = job.get('motion_review') or {}
    if not review.get('approved'):
        raise ValueError('Esta toma no tiene una copia de movimiento aprobada.')
    return validated_copy(service, job, review.get('artifact_id', ''), review.get('sha256'))[0]


def register(app, settings):
    @app.get('/api/jobs/{job_id}/motion-versions')
    async def versions(job_id: UUID):
        service = app.state.jobs
        try:
            job = completed_take(service, str(job_id))
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        copies = []
        for report_path in (service.root / 'identity-tests').glob('*/video-report.json'):
            try:
                _, report, _ = await asyncio.to_thread(validated_copy, service, job, report_path.parent.name)
            except (ValueError, OSError):
                continue
            review = job.get('motion_review') or {}
            copies.append({'id': report_path.parent.name, 'name': report.get('name'), 'sha256': report['sha256'],
                           'approved': bool(review.get('approved') and review.get('sha256') == report['sha256'] and review.get('artifact_id') == report_path.parent.name)})
        return {'copies': copies, 'original_url': f'/api/jobs/{job_id}/result?variant=original'}

    @app.post('/api/jobs/{job_id}/review-motion-version')
    async def review_copy(job_id: UUID, body: MotionReview):
        service = app.state.jobs
        async with service.lock:
            try:
                job = completed_take(service, str(job_id))
                _, report, report_path = await asyncio.to_thread(validated_copy, service, job, body.artifact_id, body.expected_sha256)
            except (ValueError, OSError) as error:
                raise HTTPException(409, str(error)) from error
            review = {'artifact_id': body.artifact_id, 'sha256': body.expected_sha256, 'approved': body.approved,
                      'turn_approved': body.approved, 'timing_approved': body.approved,
                      'feedback': body.feedback, 'reviewed_at': now(), 'scope': 'turn_and_timing_of_this_take'}
            # Identity/accessory flags are deliberately independent of motion approval.
            report['motion_review'] = review
            temporary = report_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
            temporary.replace(report_path)
            history = [*(job.get('motion_review_history') or []), review]
            return service.store.update(str(job_id), motion_review=review, motion_review_history=history)
