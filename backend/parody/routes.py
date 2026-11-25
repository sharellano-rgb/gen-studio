import hashlib
import json
import math
from pathlib import Path
from uuid import UUID
from fastapi import APIRouter, Header, HTTPException, Query
from .repository import Repository, Problem
from .schemas import CreateProject, UpdateProject, UpdateDraft, ProjectGuide, GuideNavigation


def source_reference(service, asset_id):
    if asset_id is None:
        return None
    asset = service.store.asset(str(asset_id))
    if not asset or asset.get('kind') != 'video':
        raise Problem(404, 'SOURCE_NOT_FOUND', 'Vídeo no encontrado en este espacio.')
    root = (service.root / 'inputs').resolve()
    if not root.is_relative_to(service.root.resolve()):
        raise Problem(404, 'SOURCE_NOT_FOUND', 'La fuente no pertenece a este espacio.')
    path = (root / asset['filename']).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise Problem(404, 'SOURCE_NOT_FOUND', 'El archivo fuente no está disponible.')
    duration = asset.get('duration')
    if not isinstance(duration, (float, int)) or not math.isfinite(duration) or not 0 < duration <= 120:
        raise Problem(422, 'SOURCE_DURATION', 'Selecciona un vídeo de hasta 120 segundos con duración conocida.')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return dict(asset_id=str(asset_id), name=asset.get('name', 'Vídeo'), sha256=digest.hexdigest(), duration_ms=round(duration * 1000))


def register(app, settings):
    config = json.loads(Path(__file__).with_name('config.json').read_text(encoding='utf-8'))
    enabled = settings.get('parody_enabled', config.get('enabled', False)) is True
    analysis_enabled = enabled and settings.get('parody_analysis_enabled', config.get('analysis_enabled',False)) is True
    characters_enabled = enabled and settings.get('parody_characters_enabled', config.get('characters_enabled', False)) is True
    script_enabled = characters_enabled and settings.get('parody_script_enabled', config.get('script_enabled', False)) is True
    voice_enabled = script_enabled and settings.get('parody_voice_enabled', config.get('voice_enabled', False)) is True
    proposals_enabled = script_enabled and settings.get('parody_script_proposals_enabled', False) is True
    render_enabled = characters_enabled and settings.get('parody_render_enabled',False) is True
    montage_enabled = render_enabled and settings.get('parody_montage_enabled',False) is True
    router = APIRouter(prefix='/api/parody/v1')

    def service():
        if not enabled:
            raise HTTPException(403, detail={'code': 'MODULE_DISABLED', 'message': 'Parody Maker todavía no está activado.'})
        return app.state.jobs.current

    def run(action):
        try:
            return action()
        except Problem as error:
            raise HTTPException(error.status, detail=error.detail) from error

    @router.get('/speech-capabilities')
    def speech_capabilities():
        service()
        return dict(enabled=render_enabled and voice_enabled and settings.get('parody_speech_enabled') is True,
                    version='parody-h3-speech-1',max_seconds=4,max_voices=1,max_references=1,requires_review=True,
                    ref2va=dict(enabled=render_enabled and voice_enabled and settings.get('parody_ref2va_enabled') is True,
                        version='parody-pika-ref2va-1',frames=124,fps=24,resolution='1344x768',audio='native_generated',requires_prepared_scene=True,
                        scene_preparation=render_enabled and settings.get('parody_scene_preparation_enabled') is True,
                        scene_image=render_enabled and settings.get('parody_scene_image_enabled') is True))

    @router.get('/capabilities')
    def capabilities():
        return dict(schema_version=1, phase=7 if montage_enabled else 6 if render_enabled else 5 if script_enabled else 4 if characters_enabled else 3 if analysis_enabled else 2, module_enabled=enabled, metadata_editing=enabled, guided_setup=characters_enabled, character_editing=characters_enabled, script_editing=script_enabled, script_proposals=proposals_enabled, generation_enabled=render_enabled and settings.get('generation_enabled') is True, analysis='visual_review' if analysis_enabled else 'disabled_by_phase', voice='cpu_preview' if voice_enabled else 'disabled_by_phase', rendering='h3_single_shot' if render_enabled else 'disabled_by_phase', montage=montage_enabled, exports='review_preview' if montage_enabled else 'disabled_by_phase')

    @router.get('/projects')
    def projects(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        return run(lambda: Repository(service().root).listing(limit, offset))

    @router.get('/projects/{project_id}')
    def project(project_id: UUID, revision: int | None = Query(None, ge=1)):
        repo = Repository(service().root)
        return run(lambda: repo.guide_position(str(project_id)) if revision is None else repo.read(str(project_id), revision))

    @router.put('/projects/{project_id}/guide-position')
    def guide_position(project_id: UUID, body: GuideNavigation):
        return run(lambda: Repository(service().root).guide_position(str(project_id), body))

    @router.post('/projects', status_code=201)
    def create(body: CreateProject, idempotency_key: UUID = Header()):
        current = service()
        def transform(value):
            value.update(name=body.name, source=source_reference(current, body.source_asset_id))
            if body.guided:
                value['guide'] = ProjectGuide().model_dump(mode='json')
            return value
        payload = body.model_dump(mode='json', exclude=set() if body.guided else {'guided'})
        return run(lambda: Repository(current.root).mutate('create', str(idempotency_key), payload, transform))

    @router.patch('/projects/{project_id}')
    def update(project_id: UUID, body: UpdateProject, idempotency_key: UUID = Header()):
        current = service()
        def transform(value):
            if body.name is not None: value['name'] = body.name
            if body.archived is not None: value['archived'] = body.archived
            return value
        return run(lambda: Repository(current.root).mutate('update:' + str(project_id), str(idempotency_key), body.model_dump(mode='json'), transform, str(project_id)))

    @router.put('/projects/{project_id}/draft')
    def draft(project_id: UUID, body: UpdateDraft, idempotency_key: UUID = Header()):
        current = service()
        def transform(value):
            if value['archived']:
                raise Problem(409, 'ARCHIVED', 'Restaura el proyecto antes de editarlo.')
            source = source_reference(current, body.source_asset_id)
            if source and any(s.end_ms > source['duration_ms'] for s in body.shots):
                raise Problem(422, 'SHOT_INTERVAL', 'Un plano supera la duración de la fuente.')
            if body.cast is not None:
                if not characters_enabled:
                    raise Problem(403, 'PHASE_NOT_ENABLED', 'Las fichas de personajes no están habilitadas.')
                from .characters import validate_cast
                value['cast'] = validate_cast(current, str(project_id), body.cast, source, body.shots)
            elif value.get('cast', {}).get('appearances'):
                # Legacy clients must not silently discard or detach accepted assignments.
                old_shots = {s['id']: s for s in value['draft']['shots']}
                new_shots = {str(s.id): s.model_dump(mode='json') for s in body.shots}
                if source != value['source'] or any(old_shots.get(a['shot_id']) != new_shots.get(a['shot_id']) for a in value['cast']['appearances']):
                    raise Problem(409, 'CAST_REVIEW_REQUIRED', 'Revisa las asignaciones de personajes antes de cambiar su fuente o planos.')
            if body.script is not None:
                if not script_enabled:raise Problem(403,'PHASE_NOT_ENABLED','El editor de guion no está habilitado.')
                from .script import validate_script
                value['script']=validate_script(body.script,source,body.shots,value.get('cast'))
            elif value.get('script',{}).get('lines') or value.get('script',{}).get('required_phrase'):
                from .script import Script, validate_script
                if source!=value['source']:raise Problem(409,'SCRIPT_REVIEW_REQUIRED','Revisa el guion antes de cambiar su fuente.')
                validate_script(Script.model_validate(value['script']),source,body.shots,value.get('cast'))
            if body.guide is not None:
                guide = body.guide.model_dump(mode='json')
                for asset_id in guide['original_images']:
                    asset = current.store.asset(asset_id)
                    if not asset or asset['kind'] != 'image':
                        raise Problem(422, 'GUIDE_IMAGE', 'Elige una imagen del sujeto original de este espacio.')
                value['guide'] = guide
            elif 'guide' in value and source != value['source']:
                value['guide'] = ProjectGuide().model_dump(mode='json')
            value.update(source=source, draft={'notes': body.notes, 'shots': [s.model_dump(mode='json') for s in body.shots]})
            return value
        # Preserve phase 2/3 request hashes for uncertain sends made before cast existed.
        payload = body.model_dump(mode='json', exclude={key for key in ('cast','script','guide') if getattr(body,key) is None})
        return run(lambda: Repository(current.root).mutate('draft:' + str(project_id), str(idempotency_key), payload, transform, str(project_id)))

    # Explicitly reject execution. No job, outbox or provider is created here.
    def phase_blocked():
        service()
        raise HTTPException(403, detail={'code': 'PHASE_NOT_ENABLED', 'message': 'La generación se habilitará en una fase posterior.'})

    if analysis_enabled:
        from .analysis_routes import register as register_analysis
        register_analysis(router,service,run,source_reference)
    if voice_enabled:
        from .voice_routes import register as register_voice
        register_voice(router,service,run)
    if proposals_enabled:
        from .script_proposals import register as register_proposals
        register_proposals(router,service,run,source_reference)
    if render_enabled:
        from .render import register as register_render
        register_render(router,app,service,run,settings)
        from .scene_preparation import register as register_scene_preparation
        register_scene_preparation(router,service,run,settings)
        from .scene_image import register as register_scene_image
        register_scene_image(router,service,run,settings)
    if montage_enabled:
        from .montage_routes import register as register_montage
        register_montage(router,service,run,settings)
    for suffix in (() if analysis_enabled else ('analyses',)) + (() if voice_enabled else ('voice-previews',)) + (() if render_enabled else ('render-plans','render-plans/{plan_id}/execute')) + ('exports',):
        router.add_api_route('/projects/{project_id}/' + suffix, phase_blocked, methods=['POST'])
    app.include_router(router)
