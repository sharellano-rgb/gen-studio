import asyncio
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Request, UploadFile, File, HTTPException
from pydantic import BaseModel, Field, model_validator
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .workflows import ROOT, describe_workflow, preflight, character_description, character_graph, check_graph, prepare_character
from .jobs import JobService
from .anchor_routes import register as register_anchor
from .guided_composition import register as register_guided
from .guided_head import register as register_guided_head
from .long_video import register as register_long_video
from .long_plan import SHORT_FRAMES, MAX_FRAMES, SCENE_CUT_MAX_FRAMES
from .h3_face_refine import register as register_h3_face_refine
from .ninja_swap import register as register_ninja
from .ninja_shots import register as register_ninja_shots
from .alisson_swap import register as register_alisson
from .direct_character import register as register_direct
from .geometry_copy import register as register_geometry
from .upscale import register as register_upscale
from .prompt_enhancer import register as register_prompt_enhancer
from .motion_transfer import register as register_motion_transfer
from .motion_versions import register as register_motion_versions, approved_result as approved_motion_result
from .motion_h3 import register as register_motion_h3, validate_reviewed_take
from .head import digest
from .media import save_upload, source_resolution
from .face import available as face_available
from .studio_editor import EditPolicy, CharacterBinding, ShotEdit, register as register_studio_editor, validate_editor_payload
from .studio_results import register as register_studio_results
from . import nsfw_adapters
from .workspaces import WorkspaceServices, current_workspace, profiles, identify, public_profile

CONFIG = json.loads((ROOT / 'backend/config.json').read_text(encoding='utf-8'))


class TestOrigin(BaseModel):
    direct_video_only: bool = Field(default=False, strict=True)
    frames: int = Field(ge=5, le=2880, strict=True)
    direct_start_seconds: float = Field(default=0, ge=0, le=3600, allow_inf_nan=False)
    direct_scene_cuts: bool = False
    direct_dynamic_prompts: bool = False
    direct_continuity: bool = False
    direct_shot_plan_id: UUID | None = None
    direct_shot_edits: list[ShotEdit] = Field(default_factory=list, max_length=240)
    direct_targets: list[str] = Field(default_factory=list, max_length=9)
    direct_characters: list[CharacterBinding] = Field(default_factory=list, max_length=9)
    direct_edit_policy: EditPolicy | None = None
    direct_direction: str = Field(default='', max_length=1500)


class JobRequest(BaseModel):
    request_key: UUID
    direct_continuity: bool = False
    video_id: UUID
    reference_id: UUID
    preserve_face: bool = False
    face_reference_id: UUID | None = None
    guide_face_reference: bool = False
    ref_image_size: Literal['match', 'max'] = 'match'
    prompt: str = Field(max_length=4000)
    subject: str = Field(min_length=1, max_length=300)
    resolution: Literal['960x544', '864x480', '480x864', '512x512','1344x768','768x1344','1024x1024'] = '960x544'
    frames: int = Field(default=107,ge=3,le=2880,strict=True)
    steps: int = Field(default=20, ge=8, le=30)
    seed: int = Field(default=314159, ge=0, le=9007199254740991)
    lora_strength: float = Field(default=1, ge=0, le=1.5)
    mask_grow_spatial: int = Field(default=16, ge=0, le=64)
    engine: Literal['h3_sam3','h3_direct'] = 'h3_sam3'
    direct_reference_ids: list[UUID] = Field(default_factory=list,max_length=9)
    h3_location_provider: Literal["local","intel"] = "local"
    h3_plan_provider: Literal["local","intel"] = "local"
    direct_preset: Literal['base','turbo8','realism','hands','fasth3_omni'] = 'base'
    omni_experiment: Literal['none','sol','character_v15'] = 'none'
    direct_character_version: Literal['v1','v1.5'] = 'v1'
    direct_crop_face: bool = True
    direct_reference_type: Literal['auto','human','nonhuman'] = 'auto'
    direct_start_seconds: float = Field(default=0,ge=0,le=3600,allow_inf_nan=False)
    match_source_orientation: bool = True
    direct_binding_mode: Literal['manual','assigned'] = 'manual'
    direct_targets: list[str] = Field(default_factory=list,max_length=9)
    direct_direction: str = Field(default='',max_length=1500)
    direct_high_quality: bool = False
    direct_vsa: bool = False
    direct_video_only: bool = Field(default=False, strict=True)
    direct_scene_cuts: bool = False
    direct_dynamic_prompts: bool = False
    direct_global_goal: str = Field(default="", max_length=1500)
    direct_edit_policy: EditPolicy | None = None
    direct_characters: list[CharacterBinding] = Field(default_factory=list, max_length=9)
    direct_shot_plan_id: UUID | None = None
    direct_shot_edits: list[ShotEdit] = Field(default_factory=list, max_length=240)
    direct_test: bool = False
    direct_test_source_frames: int | None = Field(default=None, ge=5, le=2880, strict=True)
    direct_test_origin: TestOrigin | None = None
    nsfw_h3_motion_booster: bool = False
    nsfw_penis_v2: bool = False
    nsfw_hm_pussy: bool = False
    nsfw_minimax_unlocked: bool = False
    nsfw_strength: float = Field(default=1.0, ge=0, le=2, allow_inf_nan=False)

    @model_validator(mode='after')
    def validate_scene_mode(self):
        # Assigned H3 requests compile their prompt from validated character bindings
        # in submit(). An unused editor prompt must not block that automatic path.
        if not (self.engine == 'h3_direct' and self.direct_binding_mode == 'assigned') and len(self.prompt.strip()) < 10:
            raise ValueError('Escribe un prompt de al menos 10 caracteres o usa las referencias asignadas de H3 directo.')
        if self.frames>1440 and not (self.engine=='h3_direct' and self.direct_preset=='hands' and not self.direct_scene_cuts):
            raise ValueError('Más de 60 segundos requiere H3 Directo con Realismo Manos en modo continuo.')
        if self.omni_experiment!='none' and (self.engine!='h3_direct' or self.direct_preset!='fasth3_omni'):
            raise ValueError('Los ensayos Omni sólo se aplican a FastH3 Omni.')
        if self.direct_preset=='fasth3_omni':
            if self.engine!='h3_direct' or self.steps!=8 or not 48<=self.frames<=1440 or self.direct_scene_cuts or self.direct_vsa or self.direct_continuity:
                raise ValueError('FastH3 Omni experimental requiere H3 directo, 8 pasos y una toma continua de 2 a 10 s, sin VSA ni continuidad entre tramos.')
        if self.direct_video_only and self.engine!='h3_direct':
            raise ValueError('La entrada sin audio requiere H3 directo.')
        if self.direct_continuity and (self.engine!='h3_direct' or self.direct_preset!='hands'
                or self.direct_character_version!='v1.5' or self.direct_scene_cuts or self.frames<=240):
            raise ValueError('La continuidad entre tramos requiere Realismo Manos v1.5 y una toma continua de más de 10 s.')
        if self.direct_test and (self.engine != 'h3_direct' or self.frames > 120 or self.direct_scene_cuts or self.direct_shot_plan_id):
            raise ValueError('La prueba corta usa hasta 5 segundos de un solo plano.')
        if self.direct_vsa and (self.engine!='h3_direct' or self.direct_preset!='hands' or self.direct_character_version!='v1.5' or self.steps!=8):
            raise ValueError('VSA requiere H3 directo, Realismo Manos v1.5 y 8 pasos.')
        if self.direct_dynamic_prompts and (not self.direct_scene_cuts or self.direct_binding_mode!="assigned" or not self.direct_global_goal.strip()):
            raise ValueError("Prompts por tramo requiere cortes automáticos, referencias asignadas y un objetivo global.")
        if self.direct_scene_cuts:
            if self.frames>SCENE_CUT_MAX_FRAMES:raise ValueError('El modo por cortes admite hasta 60 segundos.')
            if self.engine!='h3_direct' or self.direct_preset!='hands' or self.direct_start_seconds!=0:
                raise ValueError('Cortes automáticos requiere Realismo Manos e inicio 0.')
        elif self.frames>MAX_FRAMES and self.engine!='h3_direct':
            raise ValueError('Usa hasta 15 segundos, o selecciona H3 directo con cortes automáticos para hasta 60 segundos.')
        return self


class HeadRefinementRequest(BaseModel):
    request_key: UUID
    face_reference_id: UUID | None = None
    direction: str = Field(default='Keep the exact fabric cap shape, crown, seams, curved brim and front patch from the face reference through every head turn, if a cap is present.', max_length=1000)


def create_app(transport=None, config=None):
    settings = {'data_root': str(ROOT / 'data'), 'generation_enabled': False, **CONFIG, **(config or {})}
    spaces=profiles(settings)
    settings['allowed_logins']=[login for profile in spaces.values() for login in profile['logins']]

    @asynccontextmanager
    async def lifespan(app):
        log_root = Path(settings['data_root'])
        log_root.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_root / 'service.log', encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        logger = logging.getLogger('gen_studio')
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        async with httpx.AsyncClient(timeout=8, trust_env=False, transport=transport) as client:
            app.state.client = client
            app.state.jobs = WorkspaceServices(client, settings, spaces)
            restart_operation=app.state.restart.operation()
            app.state.jobs.coordinator.maintenance=bool(restart_operation and restart_operation['state']=='restarting')
            if Path(settings['data_root']).resolve()==(ROOT/'data').resolve():
                import os
                (ROOT/'.runtime').mkdir(exist_ok=True)
                (ROOT/'.runtime/server.pid').write_text(str(os.getpid()),encoding='utf-8')
            if settings['generation_enabled']:
                await app.state.jobs.start()
            from .segment_alerts import worker
            alerts=asyncio.create_task(worker(app.state.jobs.services))
            try:
                yield
            finally:
                alerts.cancel()
                await asyncio.gather(alerts,return_exceptions=True)
                await app.state.jobs.close()
                logger.removeHandler(handler)
                handler.close()

    app = FastAPI(title='GEN STUDIO', version='0.1.0', lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    from .segment_alerts import register as register_alerts
    register_alerts(app)
    from .sequence_partial import register as register_partial
    register_partial(app)
    from .studio_restart import register as register_restart
    register_restart(app,settings)
    from .sonny_generation_control import register as register_sonny_generation_control
    register_sonny_generation_control(app)

    from .parody import register as register_parody
    register_parody(app, settings)

    register_anchor(app, settings)
    register_guided(app, settings)
    register_guided_head(app, settings)
    register_long_video(app,settings)
    register_direct(app,settings)
    register_h3_face_refine(app,settings)
    register_ninja(app,settings)
    register_ninja_shots(app,settings)
    register_alisson(app,settings)
    from .secourses_swap import register as register_secourses
    register_secourses(app,settings)
    register_geometry(app,settings)
    register_upscale(app,settings)
    from .tessera import register as register_tessera
    register_tessera(app,settings)
    from .omni_background import register as register_omni_background
    register_omni_background(app,settings)
    register_prompt_enhancer(app,settings)
    from .qwen_models import register as register_qwen_models
    register_qwen_models(app,settings)
    from .qwen_director import register as register_qwen_director
    register_qwen_director(app,settings)
    from .video_sequences import register as register_sequences, rejected_preview_url
    register_sequences(app)
    from .motion_bunny import register as register_motion_bunny
    register_motion_bunny(app,settings)
    from .motion_layers import register as register_motion_layers
    register_motion_layers(app,settings)
    register_motion_transfer(app,settings)
    register_motion_versions(app,settings)
    register_motion_h3(app,settings)
    register_studio_editor(app,settings)
    register_studio_results(app,settings)
    from .image_generation import register as register_images
    register_images(app,settings)
    from .job_activity_routes import register_job_activity
    register_job_activity(app)
    from .motion_hybrid import register as register_motion_hybrid
    register_motion_hybrid(app,settings)
    from .pika_h3_flex import register as register_pika
    register_pika(app,settings)
    from .pika_ref2va import register as register_pika_ref2va
    register_pika_ref2va(app,settings)
    from .viggle_remake.routes import register as register_viggle_remake
    register_viggle_remake(app,settings)

    from .history_delivery import HistoryCompression
    app.add_middleware(HistoryCompression)

    @app.middleware('http')
    async def access(request: Request, call_next):
        host = request.headers.get('host', '').lower()
        local = host in {f"127.0.0.1:{settings.get('port',8790)}", f"localhost:{settings.get('port',8790)}", '127.0.0.1:5174', 'localhost:5174', 'testserver'}
        remote_host = settings['tailscale_origin'].split('://', 1)[1].lower()
        if host != remote_host and not local:
            return JSONResponse({'detail': 'Host no permitido.'}, status_code=403)
        login = request.headers.get('tailscale-user-login', '').lower()
        proxied = bool(request.headers.get('x-forwarded-for') or request.headers.get('x-forwarded-proto'))
        if (not local or login or proxied) and login not in settings['allowed_logins']:
            return JSONResponse({'detail': 'Cuenta Tailscale no autorizada.'}, status_code=403)
        origin = request.headers.get('origin')
        allowed_origins = {settings['tailscale_origin'], f"http://127.0.0.1:{settings.get('port',8790)}", f"http://localhost:{settings.get('port',8790)}", 'http://127.0.0.1:5174', 'http://localhost:5174'}
        if origin and origin not in allowed_origins:
            return JSONResponse({'detail': 'Origen no permitido.'}, status_code=403)
        if request.url.path in ('/api/assets','/api/parody/v1/voice-assets') and request.method == 'POST':
            length = request.headers.get('content-length')
            if not length:
                return JSONResponse({'detail': 'Se requiere Content-Length.'}, status_code=411)
            try:
                if request.url.path.endswith('/voice-assets') and int(length) > 26 * 1024 ** 2:
                    return JSONResponse({'detail':'La muestra de voz supera 25 MB.'}, status_code=413)
                if int(length) > 201 * 1024 ** 2:
                    return JSONResponse({'detail': 'La carga supera 200 MB.'}, status_code=413)
            except ValueError:
                return JSONResponse({'detail': 'Content-Length inválido.'}, status_code=400)
        workspace=identify(spaces,login,local=local and not proxied)
        if workspace is None:
            return JSONResponse({'detail':'No tienes acceso a un espacio de Gen Studio.'},status_code=403)
        if request.method not in {'GET','HEAD','OPTIONS'} and request.url.path != '/api/system/restart' and getattr(app.state.jobs.coordinator,'maintenance',False):
            return JSONResponse({'detail':'Gen Studio se está reiniciando. Espera a que vuelva la conexión.'},status_code=503)
        if workspace!='owner' and request.url.path=='/guided-preview.html':
            return JSONResponse({'detail':'Vista no disponible.'},status_code=404)
        if request.url.path in ('/api/workflows/motion-transfer/preflight','/api/workflows/motion-transfer/h3/preflight','/api/workflows/motion-hybrid/preflight','/api/workflows/motion-scene/preflight'):
            return JSONResponse({'detail':'Este método se retiró. Usa Bunny en Motion Transfer.','retired':True},status_code=410)
        if request.method=='POST' and (request.url.path=='/api/motion-transfer/anchors' or any(request.url.path.endswith(tail) for tail in ('/motion-transfer','/motion-transfer-hybrid','/motion-transfer-scene-trial','/motion-transfer-h3','/motion-h3-retime'))):
            return JSONResponse({'detail':'Este método se retiró. Usa Bunny en Motion Transfer.'},status_code=410)
        token=current_workspace.set(workspace)
        try:response = await call_next(request)
        finally:current_workspace.reset(token)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['X-Gen-Studio-Workspace'] = workspace
        if request.url.path.startswith('/api'):
            cache_tokens = {value.strip().lower() for value in response.headers.get('Cache-Control', '').split(',')}
            response.headers['Cache-Control'] = 'private, no-store' if 'private' in cache_tokens else 'no-store'
        return response

    @app.get('/api/workspace')
    async def workspace_profile():return public_profile(spaces,current_workspace.get())

    async def get_service(base, path):
        try:
            response = await app.state.client.get(base + path)
            response.raise_for_status()
            return {'online': True, 'data': response.json()}
        except (httpx.HTTPError, ValueError) as error:
            return {'online': False, 'error': str(error)}

    @app.get('/api/health')
    async def health():
        return {'app': 'GEN STUDIO', 'version': '0.1.0', 'mode': 'studio' if settings['generation_enabled'] else 'audit', 'generation_enabled': settings['generation_enabled'], 'boot_id':app.state.restart.boot_id}

    @app.get('/api/status')
    async def status():
        from .video_acceleration import public_status as acceleration_status
        await app.state.restart.status()
        comfy, queue, ollama = await asyncio.gather(
            get_service(settings['comfy_url'], '/system_stats'),
            get_service(settings['comfy_url'], '/queue'),
            get_service(settings['ollama_url'], '/api/ps'))
        if queue['online']:
            queue['data'] = {kind: [{'busy': True} for item in queue['data'].get(kind, [])] for kind in ('queue_running', 'queue_pending')}
        return {'checked_at': datetime.now(timezone.utc).isoformat(),
                'comfy': comfy, 'queue': queue, 'ollama': ollama,
                'generation_enabled': settings['generation_enabled'],
                'queue_submissions_enabled': bool(app.state.jobs.settings.get('generation_limit_managed')) or app.state.jobs.settings.get('generation_interval_seconds',0)>0,
                'video_acceleration': acceleration_status(app.state.jobs.store.video_acceleration_config),
                'face_processing': {'available': face_available(settings), 'provider': 'CPU'},
                'remote': {'configured_origin': settings['tailscale_origin'], 'activation': 'active' if settings.get('remote_enabled') else 'pending_approval'}}

    @app.get('/api/workflows/minimax-h3')
    async def workflow():
        return describe_workflow()

    @app.get('/api/workflows/minimax-h3/preflight')
    async def validate():
        try:
            return await preflight(app.state.client, settings['comfy_url'])
        except (httpx.HTTPError, ValueError) as error:
            return JSONResponse({'ok': False, 'errors': [str(error)]}, status_code=503)

    @app.get('/api/workflows/character-replacement')
    async def character_workflow():
        return character_description()

    @app.get('/api/workflows/character-replacement/preflight')
    async def character_validate(preserve_face: bool = False, guide_face_reference: bool = False,
                                 ref_image_size: Literal['match', 'max'] = 'match'):
        try:
            graph = prepare_character({**character_description()['defaults'], 'preserve_face': preserve_face,
                                       'guide_face_reference': guide_face_reference, 'ref_image_size': ref_image_size},
                                      'pending-upload.mp4', 'pending-upload.png', 'preflight',
                                      detail_reference_name='pending-detail.png' if guide_face_reference else None)
            result = await check_graph(app.state.client, settings['comfy_url'], graph, character_description()['integrity_ok'],
                                       {('6', 'image'), ('7', 'video'), ('31', 'image')})
            if preserve_face and not face_available(settings):
                result['ok'] = False
                result['errors'].append('Faltan las rutas locales del procesamiento facial.')
            result['scope'] += ' Los archivos se comprueban por trabajo después de cargarlos.'
            return result
        except (httpx.HTTPError, ValueError) as error:
            return JSONResponse({'ok': False, 'errors': [str(error)]}, status_code=503)

    @app.post('/api/assets', status_code=201)
    async def upload(file: UploadFile = File()):
        try:
            return await save_upload(file, app.state.jobs.root, app.state.jobs.store)
        except (ValueError, TimeoutError) as error:
            raise HTTPException(400, str(error)) from error

    @app.get('/api/assets/{asset_id}')
    async def asset_media(asset_id: UUID):
        asset = app.state.jobs.store.asset(str(asset_id))
        if not asset:
            raise HTTPException(404, 'Carga no encontrada.')
        return FileResponse(app.state.jobs.root / 'inputs' / asset['filename'])

    @app.get('/api/assets')
    async def assets():
        return {'assets': app.state.jobs.store.assets()}

    @app.post('/api/jobs', status_code=202)
    async def submit(request: Request):
        if not settings['generation_enabled']:
            raise HTTPException(403, 'Generación pendiente de autorización e integración del control de VRAM.')
        try:
            body = JobRequest.model_validate(await request.json())
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        payload = body.model_dump(mode='json')
        try:
            if nsfw_adapters.applied(payload):
                payload['nsfw_applied'] = await asyncio.to_thread(nsfw_adapters.verify, payload)
            else:
                payload['nsfw_applied'] = []
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        if body.direct_test_origin:
            payload['direct_test_origin'] = body.direct_test_origin.model_dump(mode='json', exclude_unset=True)
        key = payload.pop('request_key')
        direct=payload['engine']=='h3_direct'
        if payload.get('h3_location_provider') == 'intel':
            if not direct or not settings.get('qwen_director_enabled') or not settings.get('h3_intel_planner'):
                raise HTTPException(400,'La localización visual Intel requiere H3 Directo y el proveedor configurado.')
        else:
            payload.pop('h3_location_provider',None)
        if payload.get('h3_plan_provider') == 'intel':
            if not direct or not settings.get('qwen_director_enabled') or not settings.get('h3_intel_planner'):
                raise HTTPException(400,'El proveedor Intel de indicaciones requiere H3 Directo y el piloto habilitado.')
        else:
            payload.pop('h3_plan_provider',None)
        if not payload.get('direct_vsa'):payload.pop('direct_vsa',None)
        if not payload.get('direct_video_only'):payload.pop('direct_video_only',None)
        if not payload.get('direct_continuity'):payload.pop('direct_continuity',None)
        # Absent editor fields must not change the payload of an uncertain legacy request.
        for field in ('direct_edit_policy','direct_characters','direct_shot_plan_id','direct_shot_edits','direct_test','direct_test_source_frames','direct_test_origin'):
            if not direct or not payload.get(field):payload.pop(field,None)
        if direct:
            if any(edit.get('disabled_character_ids') for edit in payload.get('direct_shot_edits',[])):
                if not settings.get('video_sequences_enabled') or not settings.get('qwen_director_enabled'):
                    raise HTTPException(400,'Activa la generación por tramos y el director Qwen para asignar personajes por plano.')
            try:validate_editor_payload(app.state.jobs,payload)
            except ValueError as error:raise HTTPException(400,str(error)) from error
        if direct and payload['direct_binding_mode']=='assigned':
            from .character_bindings import compile_prompt
            try:
                payload['prompt']=compile_prompt(payload['direct_targets'],len(payload['direct_reference_ids']),payload['direct_global_goal'] if payload['direct_dynamic_prompts'] else payload['direct_direction'],edit_policy=payload.get('direct_edit_policy'),characters=payload.get('direct_characters'))
            except ValueError as error:raise HTTPException(400,str(error)) from error
        if not direct:
            for field in ('direct_binding_mode','direct_targets','direct_direction','direct_high_quality','direct_scene_cuts','direct_dynamic_prompts','direct_global_goal','direct_character_version'):payload.pop(field)
        if not direct:
            if payload['resolution'] in ('1344x768','768x1344','1024x1024'):
                raise HTTPException(400,'Esta resolución pertenece al motor H3 directo.')
            for field in ['engine','direct_reference_ids','direct_preset','direct_crop_face','direct_reference_type','direct_start_seconds','match_source_orientation']:payload.pop(field)
        video = app.state.jobs.store.asset(payload['video_id'])
        reference = app.state.jobs.store.asset(payload['reference_id'])
        if not video or video['kind'] != 'video' or not reference or reference['kind'] != 'image':
            raise HTTPException(400, 'Selecciona un vídeo y una imagen de referencia válidos.')
        if payload['preserve_face'] or payload['guide_face_reference']:
            if direct:
                raise HTTPException(400,'H3 directo genera la escena en una pasada; desactiva la corrección facial y la guía anteriores.')
            face = app.state.jobs.store.asset(payload['face_reference_id']) if payload['face_reference_id'] else None
            if not face or face['kind'] != 'image':
                raise HTTPException(400, 'Selecciona un primer plano del rostro para conservar la identidad.')
            if payload['preserve_face'] and not face_available(settings):
                raise HTTPException(503, 'La corrección facial local no está disponible. Revisa la configuración de GEN STUDIO.')
        if direct:
            if payload['direct_high_quality']:
                payload['resolution']='1344x768'
                payload['match_source_orientation']=True
            if payload['direct_reference_type']=='nonhuman':payload['direct_crop_face']=False
            if payload['match_source_orientation']:
                payload['resolution']=source_resolution(video,payload['resolution'],match_aspect=True)
            ids=payload['direct_reference_ids']
            if not ids or len(ids)!=len(set(ids)):
                raise HTTPException(400,'Selecciona entre 1 y 9 imágenes distintas, en orden.')
            if payload['reference_id'] not in ids:
                raise HTTPException(400,'La apariencia debe estar incluida en las referencias directas.')
            if payload['face_reference_id'] and payload['face_reference_id']!=ids[0]:
                raise HTTPException(400,'El rostro debe ser Picture 1 en el motor directo.')
            payload['face_reference_id']=ids[0]
            images=[app.state.jobs.store.asset(i) for i in ids]
            if any(not a or a['kind']!='image' for a in images):
                raise HTTPException(400,'Todas las referencias directas deben ser imágenes cargadas.')
            tags={int(i) for i in re.findall(r'<Picture (\d+)>',payload['prompt'])}
            if tags and (min(tags)<1 or max(tags)>len(ids)):
                raise HTTPException(400,'El prompt menciona una imagen que no está seleccionada.')
            if payload['direct_preset']=='fasth3_omni':
                if payload['frames']>240 and not settings.get('video_sequences_enabled'):raise HTTPException(400,'Activa los tramos para usar Omni durante más de 10 segundos.')
                payload['direct_crop_face']=False
                payload['lora_strength']=0
            if payload['direct_preset']=='hands':
                from .character_lora import hands_strength
                strength=hands_strength(payload['direct_character_version'])
                if payload['steps']!=8 or payload['lora_strength']!=strength or payload['frames']<5:
                    raise HTTPException(400,f'Realismo Manos {payload["direct_character_version"]} requiere 8 pasos, fuerza {strength:g} y al menos 5 fotogramas.')
                payload['direct_crop_face']=False
            if payload['direct_preset']=='realism':
                if payload['steps']!=8 or payload['lora_strength']!=1:
                    raise HTTPException(400,'Character Swap Realismo requiere de 1 a 9 referencias, 8 pasos y fuerza 1.')
            if payload['direct_preset']=='turbo8' and payload['steps']!=8:
                raise HTTPException(400,'El preset Turbo 8-step requiere 8 pasos.')
            if not settings.get('video_sequences_enabled') and payload['direct_preset'] not in ('hands','fasth3_omni') and payload['frames'] not in SHORT_FRAMES and payload['direct_start_seconds'] and not (payload['frames']>240 and not payload['direct_scene_cuts']):
                raise HTTPException(400,'En vídeos largos usa inicio 0; para comparar un intervalo usa una duración corta.')
            payload['ref_image_size']='match' if payload['direct_preset'] in ('realism','hands','fasth3_omni') else 'max'
            payload['direct_source_sha256']=await asyncio.to_thread(digest,app.state.jobs.root/'inputs'/video['filename'])
            payload['direct_reference_hashes']=[await asyncio.to_thread(digest,app.state.jobs.root/'inputs'/a['filename']) for a in images]
        if payload['frames'] + (round(payload['direct_start_seconds']*24) if direct else 0) > round(video['duration'] * 24) + 1:
            raise HTTPException(400, 'El vídeo es demasiado corto para el número de fotogramas elegido.')
        if '<Video 1>' not in payload['prompt'] or '<Picture 1>' not in payload['prompt']:
            raise HTTPException(400, 'Mantén las etiquetas <Video 1> y <Picture 1> en el prompt de H3.')
        if payload.get('direct_scene_cuts') or (payload['frames'] not in SHORT_FRAMES and not (direct and payload['direct_preset'] in ('hands','fasth3_omni'))):
            if payload['preserve_face']:
                raise HTTPException(400,'Para vídeos por tramos, desactiva la corrección CPU anterior y usa el detalle H3 de cada tramo.')
            payload['operation']='long_character'
        try:
            async with app.state.jobs.lock:
                with app.state.jobs.store.connect() as db:
                    prior=db.execute('SELECT payload FROM jobs WHERE request_key=?',(key,)).fetchone()
                previous=json.loads(prior[0]) if prior else None
                if direct and not payload.get('direct_scene_cuts'):
                    from .long_plan import CHUNK_SIZES, CONTINUOUS_CHUNK_VERSION, DIRECT_7S_VERSION
                    policy = previous.get('direct_chunk_version') if previous is not None else None
                    if previous is None:
                        if payload['direct_preset']=='fasth3_omni':
                            if payload['frames']>240:policy=CONTINUOUS_CHUNK_VERSION
                        elif payload['frames']>240 or (settings.get('video_sequences_enabled') and payload['frames']>120):
                            policy=DIRECT_7S_VERSION
                    if policy in CHUNK_SIZES:
                        payload.update(direct_chunk_frames=CHUNK_SIZES[policy],direct_chunk_version=policy)
                        if payload['frames']>240:payload['operation']='long_character'
                if payload.get('direct_continuity'):
                    from .continuity import VERSION
                    payload['direct_continuity_version']=VERSION
                if payload.get('direct_vsa') and payload['frames']>360 and not payload.get('direct_chunk_frames') and not settings.get('video_sequences_enabled'):
                    # A retry of an already accepted request must still recover its
                    # original record; the new limit only rejects new submissions.
                    with app.state.jobs.store.connect() as db:
                        existing=db.execute('SELECT 1 FROM jobs WHERE request_key=?',(key,)).fetchone()
                    if not existing:
                        raise HTTPException(400,'VSA admite hasta 15 segundos. Reduce la duración o desactiva Aceleración VSA para un vídeo más largo con cortes.')
                result = app.state.jobs.store.create_job(str(uuid4()), key, payload)
            app.state.jobs.wake.set()
            return result
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.post('/api/jobs/{job_id}/refine-head', status_code=202)
    async def refine_head(job_id: UUID, body: HeadRefinementRequest):
        if not settings['generation_enabled'] or not face_available(settings):
            raise HTTPException(503, 'El afinado local de cabeza no está disponible.')
        service = app.state.jobs
        async with service.lock:
            parent = service.store.job(str(job_id))
            if not parent:
                raise HTTPException(404, 'Trabajo no encontrado.')
            if parent['parameters'].get('operation') == 'head_detail' or parent['status'] not in {'completed','failed'}:
                raise HTTPException(409, 'Selecciona una generación terminada para crear otra copia.')
            source = service.store.asset(parent['parameters']['video_id'])
            face_id = str(body.face_reference_id) if body.face_reference_id else parent['parameters'].get('face_reference_id')
            face = service.store.asset(face_id) if face_id else None
            folder = service.root / 'jobs' / str(job_id)
            base = service.root / 'results' / (str(job_id) + ('.face.mp4' if parent.get('face_result_available') else '.mp4'))
            if not source or not face or face['kind'] != 'image' or not parent['result_available'] or not base.is_file() or not (folder / 'subject-mask.mp4').is_file():
                raise HTTPException(409, 'Se requiere una toma con máscara del sujeto y un primer plano del rostro.')
            payload = {**parent['parameters'], 'operation':'head_detail', 'head_parent_id':str(job_id),
                'face_reference_id':face_id, 'preserve_face':True, 'guide_face_reference':True,
                'ref_image_size':'max', 'resolution':'512x512',
                'prompt': 'Replace the selected person in the cropped <Video 1> with the SAME person from <Picture 1> and <Picture 2>. This is a close crop of the head and upper body. Keep exactly the source framing and head motion. Use the clothing from <Picture 1> and the exact face, headwear and accessories from <Picture 2>. Preserve camera, background, lighting and pose. Do not widen the shot, insert a full body or show reference backgrounds. ' + body.direction}
            try:
                result = service.store.create_job(str(uuid4()), str(body.request_key), payload)
            except ValueError as error:
                raise HTTPException(409, str(error)) from error
            service.wake.set()
            return result

    @app.get('/api/jobs')
    async def jobs():
        return {'jobs': app.state.jobs.store.jobs(exclude_images=True)}

    @app.get('/api/jobs/{job_id}')
    async def job_detail(job_id: UUID):
        job = app.state.jobs.store.job(str(job_id))
        if not job:
            raise HTTPException(404, 'Trabajo no encontrado.')
        if url:=rejected_preview_url(app.state.jobs.current,job):
            from .video_sequences import summary as sequence_summary
            return {**job,**sequence_summary(job,app.state.jobs.current)}
        return job

    @app.post('/api/jobs/{job_id}/cancel')
    async def cancel(job_id: UUID):
        from .job_cancel import request as request_cancel
        try:
            return await request_cancel(app.state.jobs.current, str(job_id))
        except KeyError:
            raise HTTPException(404, 'Trabajo no encontrado.')
        except ValueError as error:
            raise HTTPException(409, str(error))

    @app.get('/api/jobs/{job_id}/result')
    async def result(job_id: UUID, download: bool = False, variant: Literal['final', 'original', 'corrected', 'motion-approved'] = 'final'):
        job = app.state.jobs.store.job(str(job_id))
        if job and job['parameters'].get('engine')=='tessera':
            from .tessera import checked
            from .tessera_media import digest as tessera_digest
            if variant!='final' or job['status']!='completed' or not job.get('result_available'):raise HTTPException(404,'Versión de upscaling no disponible.')
            try:
                target=checked(app.state.jobs.root,app.state.jobs.root/'results'/(str(job_id)+'.mp4'))
                if await asyncio.to_thread(tessera_digest,target)!=job.get('tessera_result_sha256'):raise ValueError('El archivo cambió desde su validación.')
            except (ValueError,OSError) as error:raise HTTPException(409,str(error)) from error
            return FileResponse(target,media_type='video/mp4',filename=f'GEN-STUDIO-{job_id}-upscaling.mp4' if download else None)
        if job and job['parameters'].get('operation') == 'upscale_1080':
            from .upscale import original as upscale_original, verify_result as verify_upscale_result
            if variant not in ('final','original'):
                raise HTTPException(404,'Esta copia dispone del resultado a 1080p y su original.')
            try:
                target = await asyncio.to_thread(upscale_original if variant=='original' else verify_upscale_result,
                    app.state.jobs, job)
            except (ValueError, KeyError, OSError) as error:
                raise HTTPException(409,str(error)) from error
            suffix = 'original' if variant=='original' else '1080p'
            return FileResponse(target,media_type='video/mp4',filename=f'GEN-STUDIO-{job_id}-{suffix}.mp4' if download else None)
        if job and (job.get('motion_h3_review') or {}).get('approved'):
            try:await asyncio.to_thread(validate_reviewed_take,app.state.jobs,job)
            except (ValueError,OSError) as error:raise HTTPException(409,str(error)) from error
        if variant == 'motion-approved':
            if not job:
                raise HTTPException(404, 'Trabajo no encontrado.')
            try:
                target = await asyncio.to_thread(approved_motion_result, app.state.jobs, job)
            except (ValueError, OSError) as error:
                raise HTTPException(409, str(error)) from error
            return FileResponse(target, media_type='video/mp4', filename=f'GEN-STUDIO-{job_id}-motion-approved.mp4' if download else None)
        if job and job['parameters'].get('operation')=='geometry_copy' and variant=='original':
            parent=app.state.jobs.store.job(job['parameters']['geometry_parent_id'])
            from .geometry_copy import source as geometry_source
            try:target,*_=geometry_source(app.state.jobs,parent)
            except ValueError as e:raise HTTPException(404,str(e)) from e
            return FileResponse(target,media_type='video/mp4',filename=f'GEN-STUDIO-{job_id}-original.mp4' if download else None)
        if job and job['parameters'].get('operation') == 'guided_head' and variant == 'original':
            from .guided_composition import local_path
            target = local_path(app.state.jobs.root, job['parameters']['head_source'])
            if not target.is_file(): raise HTTPException(404, 'La escena base no está disponible.')
            return FileResponse(target, media_type='video/mp4', filename=f'GEN-STUDIO-{job_id}-scene.mp4' if download else None)
        if job and job['parameters'].get('operation') == 'identity_anchor' and variant == 'original':
            target = Path(job['parameters']['anchor_source'])
            if not target.is_file(): raise HTTPException(404, 'La toma base no está disponible.')
            return FileResponse(target, media_type='video/mp4', filename=f'GEN-STUDIO-{job_id}-base.mp4' if download else None)
        if job and job['parameters'].get('operation') == 'head_detail' and variant == 'final':
            target = app.state.jobs.root / 'results' / (str(job_id) + '.head.mp4')
            if not job.get('head_result_available') or not target.is_file():
                raise HTTPException(404, 'El afinado de cabeza todavía no está disponible; la toma original se conserva.')
            return FileResponse(target, media_type='video/mp4', filename=f'GEN-STUDIO-{job_id}-head.mp4' if download else None)
        corrected = bool(job and job.get('face_result_available')) and variant != 'original'
        if variant == 'corrected' and not corrected:
            raise HTTPException(404, 'La corrección facial aún no está disponible.')
        target = app.state.jobs.root / 'results' / (str(job_id) + ('.face.mp4' if corrected else '.mp4'))
        if not job or not job['result_available'] or not target.is_file():
            raise HTTPException(404, 'Resultado no disponible.')
        if (job['parameters'].get('motion_h3_segmented') or job['parameters'].get('operation') in ('motion_hybrid','motion_bunny','motion_layers')) and await asyncio.to_thread(digest,target)!=job.get('motion_result_sha256'):
            raise HTTPException(409,'Cambió el resultado H3 unido; se conserva el diagnóstico.')
        return FileResponse(target, media_type='video/mp4', filename=f'GEN-STUDIO-{job_id}{"-face" if corrected else "-base"}.mp4' if download else None)

    from .sonny_gallery import register as register_sonny_gallery
    register_sonny_gallery(app, result)
    for workspace_id in spaces:
        if workspace_id not in {'owner', 'sonny'}:
            register_sonny_gallery(app, result, workspace_id)
    # Preserve private historical diagnostics; new MDMZ work is retired.
    from .mdmz_diagnostics import register as register_mdmz_diagnostics
    register_mdmz_diagnostics(app)

    @app.get('/api/mdmz/options')
    @app.post('/api/mdmz/generate')
    @app.post('/api/mdmz/analyze')
    async def retired_mdmz():
        raise HTTPException(410, 'MDMZ fue retirado como método de generación. Selecciona H3 directo.')

    @app.get('/api/mdmz/analysis')
    async def retired_mdmz_analysis():
        return {'active': False, 'analysis': None}
    from .history_thumbnails import register as register_history_thumbnails
    register_history_thumbnails(app, result, spaces)

    @app.post('/api/jobs/{job_id}/retry-face', status_code=202)
    async def retry_face(job_id: UUID):
        if not settings['generation_enabled'] or not face_available(settings):
            raise HTTPException(503, 'La corrección facial local no está disponible.')
        service = app.state.jobs
        async with service.lock:
            job = service.store.job(str(job_id))
            if not job:
                raise HTTPException(404, 'Trabajo no encontrado.')
            if job['parameters'].get('operation') == 'head_detail':
                raise HTTPException(409, 'El afinado crea una copia independiente. Inicia otra desde la toma original.')
            if job['status'] not in {'completed','failed'} or service.store.unfinished_jobs():
                raise HTTPException(409, 'Espera a que terminen los trabajos antes de reintentar la corrección.')
            folder = service.root / 'jobs' / str(job_id)
            base = service.root / 'results' / (str(job_id)+'.mp4')
            if not job['parameters'].get('preserve_face') or not base.is_file() or not (folder / 'subject-mask.mp4').is_file():
                raise HTTPException(409, 'Se requiere generación base, máscara del sujeto y referencia facial de este trabajo.')
            corrected = service.root / 'results' / (str(job_id)+'.face.mp4')
            snapshot = folder / 'face-versions' / str(uuid4())
            snapshot.mkdir(parents=True,exist_ok=True)
            (snapshot / 'job.json').write_text(json.dumps(job,indent=2),encoding='utf-8')
            if corrected.is_file():
                await asyncio.to_thread(shutil.copy2,corrected,snapshot / 'corrected.mp4')
            if (folder / 'face-report.json').is_file():
                await asyncio.to_thread(shutil.copy2,folder / 'face-report.json',snapshot / 'face-report.json')
            result = service.store.update(str(job_id),status='refining',stage='Reintentando la corrección facial sobre el vídeo base',
                                          error=None,face_result_available=False,face_review=None,progress=None)
            service.wake.set()
            return result

    @app.get('/api/evidence')
    async def evidence():
        target = app.state.jobs.root / 'evidence.mp4'
        if not target.is_file():
            return JSONResponse({'detail': 'Vídeo de evidencia no disponible.'}, status_code=404)
        return FileResponse(target, media_type='video/mp4')

    @app.get('/api/jobs/{job_id}/identity-review')
    async def identity_review(job_id: UUID):
        if not app.state.jobs.store.job(str(job_id)):
            raise HTTPException(404, 'Trabajo no encontrado.')
        trials = []
        for report_path in (app.state.jobs.root / 'identity-tests').glob('*/video-report.json'):
            trial_id = report_path.parent.name
            if not re.fullmatch(r'[a-z0-9-]{1,80}', trial_id):
                continue
            report = json.loads(report_path.read_text(encoding='utf-8'))
            if report.get('review_eligible', True) and report.get('source_job') == str(job_id) and (report_path.parent / 'corrected.mp4').is_file():
                trials.append({'id': trial_id, 'identity_approved': report.get('identity_approved', False),
                               'accessory_approved': report.get('accessory_approved', False),
                               'name': report.get('name', 'Prueba de identidad facial'), 'description': report.get('description'),
                               'processed_frames': report.get('processed_frames', report.get('frames')), 'media': report.get('media'),
                               'video_url': f'/api/identity-tests/{trial_id}/video',
                               'comparison_url': f'/api/identity-tests/{trial_id}/comparison',
                               'reference_url': f'/api/identity-tests/{trial_id}/reference'})
        return {'trials': trials}

    @app.get('/api/identity-tests/{trial_id}/{artifact}')
    async def identity_artifact(trial_id: str, artifact: Literal['video', 'comparison', 'reference'], download: bool = False):
        if not re.fullmatch(r'[a-z0-9-]{1,80}', trial_id):
            raise HTTPException(404, 'Prueba no encontrada.')
        folder = app.state.jobs.root / 'identity-tests' / trial_id
        if not (folder / 'video-report.json').is_file():
            raise HTTPException(404, 'Prueba todavía no validada técnicamente.')
        report = json.loads((folder / 'video-report.json').read_text(encoding='utf-8'))
        if not report.get('review_eligible', True):
            raise HTTPException(404, 'Prueba descartada; se conserva su registro para diagnóstico.')
        filename, media_type = {'video': ('corrected.mp4', 'video/mp4'),
                               'comparison': ('face-comparison.png', 'image/png'),
                               'reference': ('reference-face.png', 'image/png')}[artifact]
        target = folder / filename
        if not target.is_file():
            raise HTTPException(404, 'Archivo de prueba no disponible.')
        return FileResponse(target, media_type=media_type, filename=f'GEN-STUDIO-{trial_id}-{filename}' if download else None)

    from .flowhmr_research import register as register_flowhmr_research
    register_flowhmr_research(app, settings, runner_sha256='7a6c3430cdc0ba566481083cfc08dda4e1d72bd53ddf4fea4f9733475bc8607c')

    from .flowhmr_control import register as register_flowhmr_control
    register_flowhmr_control(app, settings)

    dist = ROOT / 'frontend/dist'
    if dist.is_dir():
        app.mount('/', StaticFiles(directory=dist, html=True), name='frontend')
    return app


app = create_app()
