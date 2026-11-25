"""Private scene library metadata and verified imports for optional refinement."""
import asyncio
import json
import shutil
from uuid import UUID, uuid5, NAMESPACE_URL

from fastapi import HTTPException, Query
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from .head import digest
from .media import probe


# Keep this precedence aligned with frontend/src/sceneResults.js parentTake().
SCENE_PARENT_FIELDS = (
    'sequence_parent_id',
    'reuse_character_job_id',
    'upscale_parent_id', 'ninja_sequence_owner', 'ninja_sequence_parent_id',
    'long_variant_root_id', 'long_variant_parent_id', 'source_result_parent_id',
    'long_parent_id', 'hybrid_parent_id', 'h3_face_parent_id', 'geometry_parent_id',
    'ninja_parent_id', 'head_parent_id', 'anchor_parent_id',
)
ASSET_PUBLIC_FIELDS = ('id', 'name', 'kind', 'width', 'height', 'duration')


class ScenePatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str | None = None
    selected_job_id: UUID | None = None

    @model_validator(mode='before')
    @classmethod
    def nonempty_patch(cls, value):
        if isinstance(value, dict) and not ({'name', 'selected_job_id'} & value.keys()):
            raise ValueError('Indica el nombre o la versión elegida.')
        return value

    @field_validator('name', mode='before')
    @classmethod
    def scene_name(cls, value):
        if not isinstance(value, str):
            raise ValueError('El nombre debe ser texto.')
        value = value.strip()
        if len(value) > 100:
            raise ValueError('El nombre admite hasta 100 caracteres.')
        return value


def parent_take(job):
    parameters = job.get('parameters') or {}
    if parameters.get('engine') in ('pika_h3_flex','h3_i2va','pika_ref2va','viggle_remake') and job.get('qwen_feedback_parent'):
        return job['qwen_feedback_parent']
    return next((parameters.get(key) for key in SCENE_PARENT_FIELDS if parameters.get(key)), None) or (
        parameters.get('motion_anchor_id') if parameters.get('operation') == 'motion_hybrid' else None)


def scene_root(db, job, cache):
    """Resolve only through this workspace, independent of pagination or row order."""
    seen = set()
    while job:
        if job['id'] in seen or len(seen) >= 256:
            raise ValueError('La relación entre versiones de esta escena no es válida.')
        seen.add(job['id'])
        parent_id = parent_take(job)
        if not parent_id:
            return job['id']
        if not isinstance(parent_id, str):
            raise ValueError('La relación entre versiones de esta escena no es válida.')
        if parent_id not in cache:
            row = db.execute('SELECT state FROM jobs WHERE id=?', (parent_id,)).fetchone()
            cache[parent_id] = json.loads(row[0]) if row else None
        job = cache[parent_id]
    raise ValueError('No se encontró la escena original en tu espacio de trabajo.')


def asset_references(value):
    """Nested parameter records may contain images as well as a source video."""
    if isinstance(value, dict):
        for item in value.values():
            yield from asset_references(item)
    elif isinstance(value, list):
        for item in value:
            yield from asset_references(item)
    elif isinstance(value, str):
        try:
            yield str(UUID(value))
        except ValueError:
            pass


def history_page(store, before, result_root):
    with store.connect() as db:
        rows = db.execute("SELECT rowid, state FROM jobs WHERE rowid < ? AND json_extract(payload,'$.sequence_parent_id') IS NULL AND COALESCE(json_extract(payload, '$.operation'), '') != 'image_generation' ORDER BY rowid DESC LIMIT 101",
                          (before if before is not None else 9223372036854775807,)).fetchall()
        jobs = [json.loads(row[1]) for row in rows[:100]]
        cache = {job['id']: job for job in jobs}
        roots = set()
        for job in jobs:
            try:
                roots.add(scene_root(db, job, cache))
            except ValueError:
                # Corrupt/legacy ancestry must not hide otherwise readable history.
                continue
        metadata = store.scene_metadata(roots)
        original_ids = set(cache)
        selected_jobs = []
        for root_id, value in metadata.items():
            selected_id = value.get('selected_job_id')
            if not selected_id:
                continue
            selected_cache = dict(cache)
            if selected_id not in selected_cache:
                row = db.execute('SELECT state FROM jobs WHERE id=?', (selected_id,)).fetchone()
                selected_cache[selected_id] = json.loads(row[0]) if row else None
            selected = selected_cache[selected_id]
            try:
                valid = (selected and selected['status'] == 'completed' and selected.get('result_available') and
                         scene_root(db, selected, selected_cache) == root_id and
                         (result_root / (selected_id + '.mp4')).is_file())
            except ValueError:
                valid = False
            if valid:
                cache.update(selected_cache)
                if selected_id not in original_ids:
                    selected_jobs.append(selected)
            else:
                # A stale preference cannot make an unavailable output appear chosen.
                value['selected_job_id'] = None
        referenced = set()
        for job in cache.values():
            if job:
                referenced.update(asset_references(job.get('parameters') or {}))
        assets = []
        for asset_id in sorted(referenced):
            row = db.execute('SELECT metadata FROM assets WHERE id=?', (asset_id,)).fetchone()
            if row:
                asset = json.loads(row[0])
                assets.append({key: asset[key] for key in ASSET_PUBLIC_FIELDS if key in asset})
    page_ids = {job['id'] for job in jobs}
    selected_ids = {job['id'] for job in selected_jobs}
    ancestors = [job for key, job in cache.items() if job and key not in page_ids and key not in selected_ids]
    return {'jobs': jobs, 'next_before': rows[99][0] if len(rows) > 100 else None,
            'ancestor_jobs': ancestors, 'selected_jobs': selected_jobs, 'assets': assets, 'scene_metadata': metadata}


def scene_context(store, job_id):
    job = store.job(job_id)
    if not job:
        raise LookupError('Toma no encontrada.')
    with store.connect() as db:
        cache = {job_id: job}
        root_id = scene_root(db, job, cache)
        root = cache[root_id]
        metadata = store.scene_metadata([root_id]).get(root_id, {})
        source_name = ''
        parameters = root.get('parameters') or {}
        for key in ('source_video_id', 'video_id'):
            asset_id = parameters.get(key)
            if not isinstance(asset_id, str):
                continue
            row = db.execute('SELECT metadata FROM assets WHERE id=?', (asset_id,)).fetchone()
            asset = json.loads(row[0]) if row else {}
            if asset.get('kind') == 'video' and isinstance(asset.get('name'), str) and asset['name'].strip():
                source_name = asset['name'].strip().replace('\\', '/').rsplit('/', 1)[-1]
                break
    return {'scene_id': root_id, 'name': metadata.get('name', ''), 'source_name': source_name}


def verified_result(service, job):
    if not job or job['status'] != 'completed' or not job.get('result_available') or job['parameters'].get('engine') != 'h3_direct':
        raise ValueError('Selecciona un resultado H3 directo terminado.')
    path = service.root / 'results' / (job['id'] + '.mp4')
    operation = job['parameters'].get('operation')
    if operation in ('long_character', 'long_assembly'):
        report = service.root / 'jobs' / job['id'] / 'long-result.json'
        expected = json.loads(report.read_text(encoding='utf-8'))['sha256']
    elif operation in (None, 'h3_face_refine'):
        expected = job.get('h3_face_result_sha256') or job.get('direct_result_sha256')
    else:
        raise ValueError('Abre la toma base o su tramo para preparar el refinamiento.')
    if not expected or not path.is_file() or digest(path) != expected:
        raise ValueError('El resultado cambió; se conserva y no se usa para otra pasada.')
    return path, expected


def register(app, settings):
    from .result_review import register as register_result_review
    register_result_review(app)

    @app.get('/api/scene-history')
    async def scene_history(before: int | None = Query(default=None, ge=1)):
        return await asyncio.to_thread(history_page, app.state.jobs.store, before, app.state.jobs.root / 'results')

    @app.get('/api/jobs/{job_id}/scene-context')
    async def get_scene_context(job_id: UUID):
        try:
            return await asyncio.to_thread(scene_context, app.state.jobs.store, str(job_id))
        except LookupError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.patch('/api/scenes/{scene_id}')
    async def patch_scene(scene_id: UUID, body: ScenePatch):
        service = app.state.jobs
        async with service.lock:
            store = service.store
            job = store.job(str(scene_id))
            if not job:
                raise HTTPException(404, 'Escena no encontrada.')
            try:
                with store.connect() as db:
                    cache = {job['id']: job}
                    root_id = scene_root(db, job, cache)
                    changes = body.model_dump(exclude_unset=True)
                    selected_id = changes.get('selected_job_id')
                    if selected_id is not None:
                        selected_id = str(selected_id)
                        selected = store.job(selected_id)
                        if not selected:
                            raise HTTPException(404, 'Versión no encontrada.')
                        if selected['parameters'].get('tessera_segment_index') is not None:
                            raise ValueError('Incorpora el tramo mediante «Usar esta versión en el montaje» antes de elegir el vídeo completo.')
                        if scene_root(db, selected, cache) != root_id:
                            raise ValueError('La versión elegida debe pertenecer a esta escena.')
                        if selected['status'] != 'completed' or not selected.get('result_available') or not (
                                service.root / 'results' / (selected_id + '.mp4')).is_file():
                            raise ValueError('Elige una versión terminada con su vídeo disponible.')
                        changes['selected_job_id'] = selected_id
                return store.update_scene_metadata(root_id, changes)
            except ValueError as error:
                raise HTTPException(409, str(error)) from error

    @app.post('/api/jobs/{job_id}/refinement-source')
    async def refinement_source(job_id: UUID):
        service = app.state.jobs
        async with service.lock:
            try:
                job = service.store.job(str(job_id))
                path, sha = await asyncio.to_thread(verified_result, service, job)
                asset_id = str(uuid5(NAMESPACE_URL, 'gen-studio:refinement:' + job['id'] + ':' + sha))
                old = service.store.asset(asset_id)
                if old:
                    target = service.root / 'inputs' / old['filename']
                    if not target.is_file() or await asyncio.to_thread(digest, target) != sha:
                        raise ValueError('La copia de trabajo cambió; no se sobrescribe.')
                    return old
                info = await asyncio.to_thread(probe, path)
                target = service.root / 'inputs' / (asset_id + '.mp4')
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    await asyncio.to_thread(shutil.copyfile, path, target)
                if await asyncio.to_thread(digest, target) != sha:
                    raise ValueError('No se pudo verificar la copia de trabajo.')
                return service.store.add_asset({
                    'id': asset_id, 'kind': 'video', 'filename': target.name,
                    'name': 'Resultado para refinar · ' + job['id'][:8],
                    'sha256': sha, 'bytes': target.stat().st_size,
                    'source_result_parent_id': job['id'], **info,
                })
            except (ValueError, KeyError, OSError) as error:
                raise HTTPException(409, str(error)) from error
