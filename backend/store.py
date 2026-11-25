import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .job_activity import initial_activity, update_activity, project_activity


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path, settings=None):
        from .video_acceleration import configuration
        self.video_acceleration_config = configuration(settings)
        self.video_sequences_enabled = bool((settings or {}).get('video_sequences_enabled',False))
        self.qwen_director_enabled = bool((settings or {}).get('qwen_director_enabled',False))
        self.h3_marked_person_runtime = bool((settings or {}).get('h3_marked_person_runtime'))
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            from .segment_alerts import initialize as initialize_segment_alerts
            initialize_segment_alerts(db)
            db.execute('CREATE TABLE IF NOT EXISTS assets (id TEXT PRIMARY KEY, metadata TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL)')
            db.execute("CREATE TABLE IF NOT EXISTS scene_metadata (scene_id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', selected_job_id TEXT)")

        from .qwen_preferences import initialize as initialize_qwen
        initialize_qwen(self)
        from .qwen_director_store import initialize
        initialize(self)

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def add_asset(self, metadata):
        with self.connect() as db:
            db.execute('INSERT INTO assets VALUES (?, ?)', (metadata['id'], json.dumps(metadata)))
        return metadata

    def asset(self, asset_id):
        with self.connect() as db:
            row = db.execute('SELECT metadata FROM assets WHERE id=?', (asset_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def assets(self):
        with self.connect() as db:
            rows = db.execute("SELECT metadata FROM assets WHERE COALESCE(json_extract(metadata,'$.sequence_internal'),0)=0 ORDER BY rowid DESC LIMIT 100").fetchall()
        return [json.loads(row[0]) for row in rows]

    def qwen_preference(self):
        from .qwen_preferences import preference
        with self.connect() as db:return preference(db)

    def set_qwen_preference(self, model_id, revision):
        from .qwen_preferences import update
        return update(self, model_id, revision)

    def create_job(self, job_id, key, payload, *, qwen_binding=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload, state FROM jobs WHERE request_key=?', (key,)).fetchone()
            if row:
                if json.loads(row[0]) != payload:
                    raise ValueError('Esta clave de petición ya se usó con otros parámetros.')
                return json.loads(row[1])
            state = {'id': job_id, 'status': 'queued', 'created_at': now(), 'updated_at': now(),
                     'prompt_id': None, 'client_id': None, 'stage': 'En cola de GEN STUDIO', 'progress': None, 'error': None,
                     'result_available': False, 'parameters': payload, 'vram_events': []}
            if payload.get('engine')=='h3_direct' and payload.get('direct_reference_ids'):
                owner=payload.get('sequence_parent_id')
                if owner:
                    parent=db.execute('SELECT state FROM jobs WHERE id=?',(owner,)).fetchone()
                    if parent and json.loads(parent[0]).get('reference_opening_guard'):
                        state['reference_opening_guard']=1
                else:
                    state['reference_opening_guard']=1
            from .qwen_preferences import job_binding
            state['qwen_model_binding'] = job_binding(db, payload, qwen_binding)
            from .sequence_plan import supported as sequence_supported, version_for as sequence_version
            if self.video_sequences_enabled and sequence_supported(payload):
                reuse=db.execute('SELECT state FROM jobs WHERE id=?',(payload.get('reuse_character_job_id',''),)).fetchone()
                if not reuse or json.loads(reuse[0]).get('video_sequence_version'):state['video_sequence_version']=sequence_version(payload)
            from .qwen_director_registry import H3_SHOT_SCOPE_FIELD, H3_SHOT_SCOPE_VERSION, supported, VERSION
            from . import h3_face_policy
            if self.qwen_director_enabled and supported(payload):
                parent_id=next((payload.get(k) for k in ('sequence_parent_id','long_parent_id','hybrid_parent_id','ninja_sequence_owner') if payload.get(k)),None)
                parent_row=db.execute('SELECT state FROM jobs WHERE id=?',(str(parent_id),)).fetchone() if parent_id else None
                if not parent_id or (parent_row and json.loads(parent_row[0]).get('qwen_director_version')):
                    state['qwen_director_version'] = VERSION
                    from .qwen_shadow_policy import VERSION as shadow_version, FIELD as shadow_field
                    # New roots adopt the current policy; implementation children
                    # retain their parent's exact policy (including legacy absence).
                    # This is state only: keep request/payload identity unchanged.
                    if parent_id:
                        inherited = json.loads(parent_row[0]).get(shadow_field)
                        if inherited:state[shadow_field] = inherited
                        from .h3_marked_target import FIELD as target_field
                        target_policy = json.loads(parent_row[0]).get(target_field)
                        if target_policy:state[target_field]=target_policy
                        from .h3_marked_target import REVIEW_FIELD
                        review_policy=json.loads(parent_row[0]).get(REVIEW_FIELD)
                        if review_policy:state[REVIEW_FIELD]=review_policy
                        from .h3_marked_target import CAST_FIELD
                        cast_policy=json.loads(parent_row[0]).get(CAST_FIELD)
                        if cast_policy:state[CAST_FIELD]=cast_policy
                        if json.loads(parent_row[0]).get('direct_joint_cast'):
                            state['direct_joint_cast']=json.loads(parent_row[0])['direct_joint_cast']
                        if json.loads(parent_row[0]).get('direct_sequence_ensemble'):
                            state['direct_sequence_ensemble']=json.loads(parent_row[0])['direct_sequence_ensemble']
                        if json.loads(parent_row[0]).get('direct_mouth_advisory'):
                            state['direct_mouth_advisory']=True
                        face_policy = json.loads(parent_row[0]).get(h3_face_policy.FIELD)
                        if face_policy:state[h3_face_policy.FIELD] = face_policy
                    else:
                        state[shadow_field] = shadow_version
                        state.update(h3_face_policy.creation_policy(payload))
                        if (payload.get('engine')=='h3_direct' and payload.get('direct_shot_plan_id')
                                and payload.get('direct_visual_target_proofs')):
                            from .h3_marked_target import FIELD as target_field, VERSION as target_version
                            state[target_field]=target_version
                            from .h3_marked_target import REVIEW_FIELD, REVIEW_VERSION
                            if state.get(h3_face_policy.FIELD)==h3_face_policy.GUIDED_VERSION:
                                state[REVIEW_FIELD]=REVIEW_VERSION
                                from .h3_marked_target import CAST_FIELD,CAST_VERSION
                                if len({proof['character_id'] for proof in payload['direct_visual_target_proofs']})>1:
                                    state[CAST_FIELD]=CAST_VERSION
                                    state['direct_joint_cast']='whole-frame-cast-1'
                                    state['direct_mouth_advisory']=True
                                    if state.get('video_sequence_version'):
                                        from .h3_sequence_ensemble import POLICY
                                        state['direct_sequence_ensemble']=POLICY
                    from . import h3_swap_policy
                    h3_swap_policy.pin(state, json.loads(parent_row[0]) if parent_row else None)
                    # The whole-video preflight is enrolled only where a sequence
                    # orchestrator will run it. Children inherit an already pinned
                    # root policy, even though their own jobs are not sequences.
                    from .qwen_global import VERSION as global_version, FIELD as global_field
                    if parent_id:
                        inherited = json.loads(parent_row[0]).get(global_field)
                        if inherited:state[global_field] = inherited
                        shot_policy = json.loads(parent_row[0]).get(H3_SHOT_SCOPE_FIELD)
                        if shot_policy:state[H3_SHOT_SCOPE_FIELD] = shot_policy
                    elif state.get('video_sequence_version'):
                        state[global_field] = global_version
                        if payload.get('engine') == 'h3_direct' and payload.get('direct_scene_cuts'):
                            state[H3_SHOT_SCOPE_FIELD] = H3_SHOT_SCOPE_VERSION
            # Pin the close-shot editor only when creating a take, including a
            # fresh sequence retry; existing children keep their accepted graphs.
            if state.get('direct_joint_cast')=='whole-frame-cast-1' and len(payload.get('direct_characters',[]))==2:
                state['direct_close_cast_guide']='joint-closeup-1'
                edit=payload.get('direct_edit_policy') or {}
                marked={(v.get('character_id'),v.get('target')) for v in payload.get('direct_visual_target_proofs',[])}
                if (edit.get('scope')=='character' and edit.get('clothing')=='reference' and
                        all((c['id'],c['target']) in marked for c in payload['direct_characters'])):
                    from .h3_cast_wardrobe import FIELD,VERSION
                    state[FIELD]=VERSION
                    from .h3_close_cast import STAGED
                    state['direct_close_cast_guide']=STAGED
                    if self.h3_marked_person_runtime and edit.get('background')=='source':
                        from .h3_rear_cast import CURRENT
                        state['direct_body_tracking']=CURRENT
            if payload.get('engine')=='h3_direct' and payload.get('direct_preset')=='hands':
                if state.get('direct_cast_wardrobe') and state.get('h3_face_motion_policy')=='source-face-11':
                    state['direct_temporal_marks']=1
                elif (state.get('direct_target_policy') and len(payload.get('direct_characters',[]))==1
                      and payload.get('direct_visual_target_proofs')):
                    state['direct_closeup_primary_sheet']=1
            from .video_acceleration import creation_profile
            acceleration = creation_profile(db, payload, self.video_acceleration_config)
            if acceleration is not None:
                state['video_acceleration'] = acceleration
            state['activity'] = initial_activity(state, state['created_at'])
            db.execute('INSERT INTO jobs VALUES (?, ?, ?, ?)', (job_id, key, json.dumps(payload), json.dumps(state)))
        return state

    def job(self, job_id):
        with self.connect() as db:
            row = db.execute('SELECT state FROM jobs WHERE id=?', (job_id,)).fetchone()
        return self.project_state(json.loads(row[0])) if row else None

    def project_state(self, state, depth=0):
        child_id = state.get('sequence_active_child') or state.get('long_active_child') or state.get('ninja_active_child') or state.get('hybrid_identity_child')
        child = None
        if child_id and state.get('status') not in {'completed', 'failed', 'cancelled', 'submission_unknown'}:
            with self.connect() as db:
                row = db.execute('SELECT state FROM jobs WHERE id=?', (child_id,)).fetchone()
            child = json.loads(row[0]) if row else None
            if child and depth<3:child=self.project_state(child,depth+1)
        return project_activity(state, child)

    def jobs(self, exclude_images=False):
        with self.connect() as db:
            where=" WHERE COALESCE(json_extract(payload,'$.operation'),'') != 'image_generation'" if exclude_images else ''
            rows = db.execute('SELECT state FROM jobs'+where+' ORDER BY rowid DESC LIMIT 100').fetchall()
        return [self.project_state(json.loads(row[0])) for row in rows]

    def unfinished_jobs(self):
        with self.connect() as db:
            rows = db.execute("SELECT state FROM jobs WHERE json_extract(state, '$.status') IN ('queued','preparing','submitting','running','downloading','refining','cancelling') ORDER BY rowid ASC").fetchall()
        return [self.project_state(json.loads(row[0])) for row in rows]

    def update(self, job_id, **changes):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            return self.update_in_transaction(db, job_id, **changes)

    def update_in_transaction(self, db, job_id, **changes):
        row = db.execute('SELECT state FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise KeyError(job_id)
        state = json.loads(row[0])
        if 'qwen_model_binding' in changes and changes['qwen_model_binding'] != state.get('qwen_model_binding'):
            raise ValueError('No se puede cambiar el modelo de una toma aceptada.')
        at = now()
        activity = update_activity(state, changes, at)
        public_changes = {key: value for key, value in changes.items() if not key.startswith('_activity_') and not key.startswith('_engine_')}
        from .segment_alerts import enqueue
        enqueue(db,state,public_changes)
        state.update(public_changes, updated_at=at, activity=activity)
        db.execute('UPDATE jobs SET state=? WHERE id=?', (json.dumps(state), job_id))
        return state

    def scene_metadata(self, scene_ids):
        """Presentation metadata stays separate from immutable generation requests."""
        keys = list(dict.fromkeys(scene_ids))
        values = {}
        if not keys:
            return values
        with self.connect() as db:
            # Each history page has at most 100 canonical scenes.
            placeholders = ','.join('?' for _ in keys)
            rows = db.execute(f'SELECT scene_id, name, selected_job_id FROM scene_metadata WHERE scene_id IN ({placeholders})', keys).fetchall()
        for scene_id, name, selected_job_id in rows:
            values[scene_id] = {'name': name, 'selected_job_id': selected_job_id}
        return values

    def update_scene_metadata(self, scene_id, changes):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT name, selected_job_id FROM scene_metadata WHERE scene_id=?', (scene_id,)).fetchone()
            value = {'name': row[0], 'selected_job_id': row[1]} if row else {'name': '', 'selected_job_id': None}
            value.update(changes)
            db.execute('INSERT INTO scene_metadata (scene_id, name, selected_job_id) VALUES (?, ?, ?) '
                       'ON CONFLICT(scene_id) DO UPDATE SET name=excluded.name, selected_job_id=excluded.selected_job_id',
                       (scene_id, value['name'], value['selected_job_id']))
        return {'scene_id': scene_id, **value}
