"""Durable job activity. Engine contact is never evidence of generation progress."""
from copy import deepcopy
from datetime import datetime
import math

VERSION = 1
TERMINAL = {'completed', 'failed', 'cancelled', 'submission_unknown'}
NO_ADVANCE_TEXT = ('reconect', 'conexión', 'conexion', 'seguimiento por historial',
    'esperando', 'en cola', 'se detendrá', 'se detendra', 'cancelando', 'deteniendo',
    'sin conexión', 'sin conexion', 'pendiente de recuperar', 'envío sin confirmar',
    'envio sin confirmar', 'motor no responde', 'recuperando el estado', 'retomando el seguimiento')


def seconds_between(start, end):
    try:
        return max(0.0, (datetime.fromisoformat(end)-datetime.fromisoformat(start)).total_seconds())
    except (TypeError, ValueError):
        return 0.0


def _phase(state, activity):
    if state.get('status') in TERMINAL:
        return 'finished'
    if state.get('status') == 'running' and activity.get('engine_state') == 'unreachable':
        return activity.get('phase') if activity.get('phase') in ('queued', 'processing') else 'processing'
    if state.get('status') == 'queued' or (state.get('status') == 'running' and activity.get('engine_state') == 'queued'):
        return 'queued'
    return 'processing'


def initial_activity(state, at, complete_timing=True):
    phase = 'queued' if state.get('status') == 'queued' else ('finished' if state.get('status') in TERMINAL else 'processing')
    return dict(version=VERSION, complete_timing=complete_timing, recovered=False,
        cached_execution=False, sample_observed=False, phase=phase, phase_since=at,
        queued_seconds=0.0, processing_seconds=0.0,
        started_at=at if phase == 'processing' else None,
        finished_at=at if phase == 'finished' and complete_timing else None,
        last_advance_at=None, last_advance_kind=None,
        stage_started_at=at, stage_label=state.get('stage'),
        last_engine_contact_at=None, engine_state=None, engine_state_at=None,
        current_node=None, node_key=None, progress_key=None, progress_value=None, progress_max=None)


def _progress_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def update_activity(state, changes, at):
    """Pure reducer. Private controls describe observed events, never UI polling."""
    previous = state.get('activity')
    activity = deepcopy(previous) if isinstance(previous, dict) and previous.get('version') == VERSION else initial_activity(state, at, False)
    after = {**state, **changes}
    recovered = bool(changes.get('_activity_recovered'))
    if recovered:
        # The server was absent for an unknown interval. Preserve measured time only.
        phase = activity.get('phase')
        if phase in ('queued', 'processing'):
            activity[phase+'_seconds'] += seconds_between(activity.get('phase_since'), state.get('updated_at'))
        activity.update(complete_timing=False, recovered=True, phase_since=at)
    if 'prompt_id' in changes and changes['prompt_id'] != state.get('prompt_id'):
        activity.update(current_node=None, node_key=None, progress_key=None, progress_value=None, progress_max=None,
            engine_state=None, engine_state_at=None)
    contact = changes.get('_engine_contact')
    if changes.get('_engine_contact_error'):
        activity['complete_timing'] = False
        if activity.get('engine_state') != 'unreachable':
            activity.update(engine_state='unreachable', engine_state_at=at)
    if contact:
        activity['last_engine_contact_at'] = at
        if contact == 'event' and activity.get('engine_state') == 'unreachable':
            activity.update(engine_state='connected', engine_state_at=at)
        if contact in ('running', 'queued', 'history') and activity.get('engine_state') != contact:
            activity.update(engine_state=contact, engine_state_at=at)
    event = changes.get('_activity_event') or {}
    kind = None
    active = after.get('status') not in TERMINAL and not recovered
    stage = changes.get('stage')
    productive_stage = bool(stage and not any(text in stage.casefold() for text in NO_ADVANCE_TEXT))
    duplicate_node = event.get('kind') == 'node' and event.get('key') == activity.get('node_key')
    if active and productive_stage and stage != activity.get('stage_label') and not duplicate_node:
        activity['stage_label'] = stage
        # WebSocket text can alternate between a node label and its step label.
        # Only the node identity or an increasing step proves another advance.
        if event.get('kind') not in ('node', 'progress'):
            activity.update(stage_started_at=at, progress_key=None, progress_value=None, progress_max=None,
                current_node=None, node_key=None)
            kind = 'stage'
        elif not activity.get('node_key'):
            activity['stage_started_at'] = at
    if active and event.get('kind') == 'node' and event.get('key') != activity.get('node_key'):
        activity.update(node_key=event.get('key'), current_node=event.get('node'), stage_started_at=at,
            progress_key=None, progress_value=None, progress_max=None)
        kind = 'node'
    progress = changes.get('progress')
    if event.get('kind') == 'progress':
        progress = event
    if active and isinstance(progress, dict):
        value, maximum = progress.get('value'), progress.get('max')
        key = event.get('key') if event.get('kind') == 'progress' else (after.get('guided_phase'), activity.get('stage_label'))
        if isinstance(key, tuple):key = list(key)
        same = activity.get('progress_key') == key
        previous_value = activity.get('progress_value')
        if (_progress_number(value) and _progress_number(maximum) and maximum > 0 and 0 <= value <= maximum
                and value > 0 and (not same or not _progress_number(previous_value) or value > previous_value)):
            activity.update(progress_key=key, progress_value=value, progress_max=maximum)
            kind = 'progress'
            if event.get('sample_observed'):activity['sample_observed'] = True
    if event.get('cached_execution'):
        activity['cached_execution'] = True
    if not recovered and after.get('status') == 'completed' and state.get('status') != 'completed':
        kind = 'completed'
    if kind:
        activity.update(last_advance_at=at, last_advance_kind=kind)
    phase = _phase(after, activity)
    old_phase = activity.get('phase')
    if phase != old_phase:
        if old_phase in ('queued', 'processing'):
            activity[old_phase+'_seconds'] += seconds_between(activity.get('phase_since'), at)
        activity.update(phase=phase, phase_since=at)
        if phase == 'processing' and not activity.get('started_at'):activity['started_at'] = at
        if phase == 'finished':activity['finished_at'] = at
        elif old_phase == 'finished':
            activity.update(complete_timing=False, recovered=True, finished_at=None)
    return activity


def project_activity(parent, child):
    """A parent exposes a validated live child's signals without changing its clock."""
    result = deepcopy(parent)
    result.pop('progress_source', None)
    if not child or parent.get('status') in TERMINAL or child.get('status') in TERMINAL:
        return result
    p = child.get('parameters') or {}
    linked = ((parent.get('sequence_active_child') == child.get('id') and p.get('sequence_parent_id') == parent.get('id'))
        or (parent.get('long_active_child') == child.get('id') and p.get('long_parent_id') == parent.get('id'))
        or (parent.get('ninja_active_child') == child.get('id') and p.get('ninja_sequence_owner') == parent.get('id'))
        or (parent.get('hybrid_identity_child') == child.get('id') and p.get('hybrid_parent_id') == parent.get('id')))
    if linked:
        result['progress_source'] = dict(job_id=child['id'], stage=child.get('stage'), progress=deepcopy(child.get('progress')),
            activity=deepcopy(child.get('activity')), segment_index=p.get('sequence_index',p.get('long_segment_index', p.get('ninja_shot_index'))))
        if parent.get('hybrid_identity_child')==child.get('id') and child.get('progress_source'):
            result['progress_source']=deepcopy(child['progress_source'])
    return result
