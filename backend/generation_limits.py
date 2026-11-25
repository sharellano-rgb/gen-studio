"""Persistent per-workspace admission of complete video jobs, before GPU leasing."""
from datetime import datetime, timedelta, timezone
import json
from .job_activity import update_activity

WAIT_STAGE = 'Servidor ocupado. Tu vídeo sigue en cola; se iniciará cuando haya disponibilidad.'
NON_GENERATION = {'identity_anchor', 'motion_anchor', 'geometry_copy', 'upscale_1080', 'video_upscale_assembly',
                  'mdmz_diagnostics', 'ninja_prepare', 'ninja_shots_plan', 'ninja_shots_prepare', 'long_assembly'}
TERMINAL = {'completed', 'failed', 'cancelled', 'submission_unknown'}


def utcnow():
    return datetime.now(timezone.utc)


def interval(service):
    value = service.settings.get('generation_interval_seconds', 0)
    return value if type(value) is int and value > 0 else 0


def video_job(state):
    p = state.get('parameters') or {}
    return bool(p.get('engine') in {'h3_direct', 'h3_sam3', 'h3_ninja', 'h3_alisson', 'h3_mdmz', 'viggle_motion','pika_h3_flex','h3_i2va','pika_ref2va','tessera','viggle_remake'}
                and p.get('operation') not in NON_GENERATION
                and not p.get('sequence_parent_id') and not p.get('ninja_sequence_owner') and not p.get('hybrid_parent_id')
                and not (p.get('long_parent_id') and type(p.get('long_segment_index')) is int))


def timestamp(value):
    try:
        result = datetime.fromisoformat(value)
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError, TypeError):
        return None


def accepted_at(db, state, depth=0):
    # Legacy accepted work resumes without using another slot after deployment.
    marker = timestamp(state.get('generation_admitted_at'))
    if marker:
        return marker
    if state.get('prompt_id') or (state.get('status') == 'completed' and state.get('result_available')):
        return timestamp((state.get('activity') or {}).get('started_at')) or timestamp(state.get('created_at'))
    if depth < 3:
        for field, link in [('sequence_active_child','sequence_parent_id'),('long_active_child', 'long_parent_id'),
                            ('ninja_active_child', 'ninja_sequence_owner'),
                            ('hybrid_identity_child', 'hybrid_parent_id')]:
            ident = state.get(field)
            row = db.execute('SELECT state FROM jobs WHERE id=?', (ident,)).fetchone() if ident else None
            child = json.loads(row[0]) if row else None
            if child and child.get('parameters', {}).get(link) == state['id']:
                at = accepted_at(db, child, depth+1)
                if at:
                    return at
    return None


def _save(db, state, at, **changes):
    activity = update_activity(state, changes, at.isoformat())
    state.update(changes, updated_at=at.isoformat(), activity=activity)
    db.execute('UPDATE jobs SET state=? WHERE id=?', (json.dumps(state), state['id']))


def _clear_wait(db, state, at):
    if state.get('status') != 'queued' or not video_job(state) or accepted_at(db, state):
        return
    if state.get('generation_wait_until') or state.get('stage') == WAIT_STAGE:
        changes = {'generation_wait_until': None}
        if state.get('stage') == WAIT_STAGE:
            changes.update(stage='Esperando turno del motor compartido', progress=None)
        _save(db, state, at, **changes)


def clear_waiting(service):
    """Remove only quota waits; accepted renders and other queue reasons survive."""
    with service.store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        at = utcnow()
        for row in db.execute('SELECT state FROM jobs').fetchall():
            _clear_wait(db, json.loads(row[0]), at)


def check(service, job, reserve=False):
    """Return True if eligible; reserve only once the shared engine is acquired.

    The transaction covers the last-start check and persistent reservation. No
    waiting task holds an engine lease. Internal segments run under their parent.
    A failed/cancelled admitted render still consumes its start slot.
    """
    seconds = interval(service)
    if not video_job(job) or (not seconds and not service.settings.get('generation_limit_managed') and service.settings.get('workspace_id') != 'sonny'):
        return True
    at = utcnow()
    with service.store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT state FROM jobs WHERE id=?', (job['id'],)).fetchone()
        if not row:
            return False
        current = json.loads(row[0])
        if current['status'] in TERMINAL:
            return False
        started = accepted_at(db, current)
        if started:
            if reserve and not current.get('generation_admitted_at'):
                _save(db, current, at, generation_admitted_at=started.isoformat(), generation_wait_until=None)
            return True
        if not seconds:
            _clear_wait(db, current, at)
            if reserve:
                # Track starts while unrestricted, so switching the limit back on
                # uses the last complete video, not a later internal segment.
                _save(db, current, at, generation_admitted_at=at.isoformat(),
                      generation_wait_until=None)
            return True
        last = None
        for row in db.execute('SELECT state FROM jobs WHERE id != ?', (job['id'],)):
            prior = json.loads(row[0])
            if video_job(prior):
                value = accepted_at(db, prior)
                if value and (last is None or value > last):
                    last = value
        until = last + timedelta(seconds=seconds) if last else None
        if until and at < until:
            if current.get('generation_wait_until') != until.isoformat() or current.get('stage') != WAIT_STAGE or current['status'] != 'queued':
                _save(db, current, at, status='queued', stage=WAIT_STAGE, progress=None,
                      generation_wait_until=until.isoformat())
            return False
        if reserve:
            _save(db, current, at, generation_admitted_at=at.isoformat(),
                  generation_wait_until=None, stage='Preparando tu toma')
        return True
