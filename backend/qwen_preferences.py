"""Workspace-local selection and atomic admission of model-bound jobs."""
import json
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from .qwen_models import default_model_id, model_definition, resolve_model, ModelResolutionError
from .qwen_runtime import FIELD, make_binding, checked_binding


def initialize(store):
    with store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS qwen_preference (singleton INTEGER PRIMARY KEY CHECK(singleton=1), model_id TEXT NOT NULL, revision INTEGER NOT NULL)')


def preference(db):
    row = db.execute('SELECT model_id,revision FROM qwen_preference WHERE singleton=1').fetchone()
    return {'model_id': row[0], 'revision': row[1]} if row else {'model_id': default_model_id(), 'revision': 0}


def update(store, model_id, revision):
    model_definition(model_id)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        current = preference(db)
        if revision != current['revision']:
            raise ModelResolutionError('selection_conflict', 'La selección cambió en otro dispositivo. Revisa el modelo actual e inténtalo de nuevo.')
        if current['model_id'] == model_id:
            return current
        result = {'model_id': model_id, 'revision': revision + 1}
        db.execute('INSERT INTO qwen_preference VALUES(1,?,?) ON CONFLICT(singleton) DO UPDATE SET model_id=excluded.model_id,revision=excluded.revision', (model_id, revision + 1))
        return result


def job_binding(db, payload, explicit=None):
    if explicit is not None:
        return checked_binding(explicit) if explicit else None
    keys = ('sequence_parent_id','long_parent_id','hybrid_parent_id','ninja_sequence_owner',
            'long_variant_parent_id','reuse_character_job_id')
    keys = (*keys, *(k for k in payload if k.endswith('_parent_id') and k not in keys))
    parent_id = next((payload[k] for k in keys if payload.get(k)), None)
    if parent_id:
        row = db.execute('SELECT state FROM jobs WHERE id=?', (str(parent_id),)).fetchone()
        if not row:
            # Preserve the existing orphan/foreign-parent admission semantics:
            # no policy is inherited, and never consult another workspace or
            # apply today's preference to historical internal work.
            return None
        value = json.loads(row[0]).get(FIELD)
        return checked_binding(value) if value else None
    current = preference(db)
    return make_binding(current['model_id'], current['revision'])


class Selection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model_id: str
    revision: StrictInt = Field(ge=0)


def register(app):
    @app.get('/api/qwen/selection')
    async def get_selection():
        return app.state.jobs.current.store.qwen_preference()

    @app.put('/api/qwen/selection')
    async def select(body: Selection):
        service = app.state.jobs.current
        try:
            await resolve_model(service.client, service.settings, body.model_id, required_capabilities=('text','vision'), context_tokens=24576)
            return service.store.set_qwen_preference(body.model_id, body.revision)
        except ModelResolutionError as error:
            raise HTTPException(error.status_code, detail={'code':error.code,'message':str(error),
                'selection':service.store.qwen_preference()}) from error
