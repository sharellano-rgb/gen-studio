"""Single-shot H3 bridge. Uses native admission, never submits Comfy graphs itself."""
import asyncio
import hashlib
import json
from typing import Literal
from uuid import UUID, uuid4
from fastapi import Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from .repository import Repository, Problem, encode
from .analysis_store import AnalysisStore
from .analysis_media import sha256
from .voice_runtime import local_input


class PlanRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_revision:int=Field(ge=1,strict=True)
    appearance_id:UUID
    reference_ids:list[UUID]=Field(min_length=1,max_length=9)
    start_ms:int=Field(ge=0,le=120000,strict=True)
    duration_ms:int=Field(ge=1000,le=5000,strict=True)
    preset:Literal['hands','base']='hands'
    quality:Literal['768p','480p']='768p'
    seed:int=Field(default=314159,ge=0,le=9007199254740991,strict=True)
    clothing:Literal['source','reference']='reference'
    performance:Literal['source','new']='source'
    voice_preview_id:UUID|None=None
    voice_reviewed:bool=Field(default=False,strict=True)
    directions:str=Field(default='',max_length=1500)
    engine:Literal['h3_direct','pika_ref2va_native']='h3_direct'
    scene_asset_id:UUID|None=None
    scene_reviewed:bool=Field(default=False,strict=True)


def request_payload(body):
    # Keep pre-speech accepted requests/idempotency byte-for-byte compatible.
    exclude={'voice_preview_id','voice_reviewed','directions'} if body.voice_preview_id is None else set()
    if body.engine=='h3_direct':exclude|={'engine','scene_asset_id','scene_reviewed'}
    return body.model_dump(mode='json',exclude=exclude)


class ExecuteRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    plan_hash:str=Field(pattern=r'^[a-f0-9]{64}$')
    approved:bool=Field(strict=True)


class RenderStore(AnalysisStore):
    def __init__(self,root):
        self.folder=Repository(root).path.parent/'renders';self.path=self.folder/'tasks.sqlite3'
        if not self.path.resolve().is_relative_to(Repository(root).path.parent.resolve()):raise Problem(409,'INVALID_STORAGE','Almacenamiento fuera del espacio.')

    def create_plan(self,project,key,body,plan):
        record=super().create(project,key,body,None,project['source'])
        return self.update(project['id'],record['id'],version=plan['version'],status='planned',stage='Plan listo para revisar; no se inició generación',
                           plan=plan,plan_hash=hashlib.sha256(encode(plan).encode()).hexdigest(),native_request_key=str(uuid4()),native_job_id=None)


def compile_plan(service,project,body,accepted=None):
    if body.engine=='pika_ref2va_native':
        from .ref2va import compile_native
        old=body.model_copy(update=dict(engine='h3_direct',scene_asset_id=None,scene_reviewed=False,voice_preview_id=None,voice_reviewed=False,directions='',performance='source'))
        return compile_native(service,project,body,compile_plan(service,project,old),accepted)
    if body.scene_asset_id or body.scene_reviewed:
        raise Problem(422,'SCENE_MODE','El fotograma preparado corresponde únicamente al modo Ref2VA nativo.')
    if body.voice_preview_id is not None:
        from .speech import compile_speech
        old=body.model_copy(update=dict(voice_preview_id=None,voice_reviewed=False,directions='',performance='source'))
        return compile_speech(service,project,body,compile_plan(service,project,old),accepted)
    if body.voice_reviewed or body.directions:
        raise Problem(422,'VOICE_REQUIRED','Las indicaciones de actuación nueva requieren una voz preparada.')
    if project['archived'] or project['revision']!=body.expected_revision:raise Problem(409,'REVISION_CONFLICT','Guarda y abre la revisión actual del proyecto.')
    if body.performance!='source':raise Problem(422,'NEEDS_ACTING_GUIDE','Un gesto nuevo necesita una guía de actuación validada. Esta prueba conserva el movimiento original.')
    source=project['source']
    if not source:raise Problem(422,'SOURCE_REQUIRED','Selecciona una fuente.')
    local_input(service,source)
    appearance=next((a for a in project.get('cast',{}).get('appearances',[]) if a['id']==str(body.appearance_id)),None)
    if not appearance or not appearance['reviewed'] or appearance['source_sha256']!=source['sha256']:
        raise Problem(422,'APPEARANCE_REVIEW','Confirma primero al sujeto original en la ficha del plano.')
    shot=next((s for s in project['draft']['shots'] if s['id']==appearance['shot_id']),None)
    character=next((c for c in project['cast']['characters'] if c['id']==appearance['character_id']),None)
    if not shot or not character:raise Problem(409,'INVALID_CAST','La asignación ya no corresponde al reparto.')
    if body.start_ms<shot['start_ms'] or body.start_ms+body.duration_ms>shot['end_ms']:
        raise Problem(422,'SHOT_INTERVAL','La prueba debe quedar dentro de un solo plano guardado, durante 1–5 segundos.')
    start=(body.start_ms*24+999)//1000;end=(body.start_ms+body.duration_ms)*24//1000
    ids=list(map(str,body.reference_ids));refs={r['id']:r for r in character['references']}
    if len(set(ids))!=len(ids):raise Problem(422,'REFERENCES','Selecciona referencias distintas.')
    selected=[]
    for ident in ids:
        ref=refs.get(ident)
        if not ref or ref['role'] not in ('front','profile','body','wardrobe','accessory'):raise Problem(422,'REFERENCES','Las imágenes deben pertenecer a la ficha seleccionada.')
        path=local_input(service,ref)
        if service.store.asset(ref['asset_id'])['kind']!='image':raise Problem(422,'REFERENCES','Se requiere una imagen.')
        selected.append(dict(ref))
    from ..main import JobRequest
    from ..character_bindings import compile_prompt
    from ..media import source_resolution
    image_ids=[r['asset_id'] for r in selected];target=appearance['target_label']
    if len(set(image_ids))!=len(image_ids):raise Problem(422,'REFERENCES','No repitas el mismo archivo en varias referencias.')
    policy=dict(version=1,scope='character',clothing=body.clothing,background='source',background_description='')
    cards=[dict(id=character['id'],name=character['name'][:80],target=target,reference_ids=image_ids,views=[r['role'] for r in selected])]
    prompt=compile_prompt([target]*len(selected),len(selected),'',edit_policy=policy,characters=cards)
    payload=JobRequest(request_key=uuid4(),video_id=source['asset_id'],reference_id=image_ids[0],
        engine='h3_direct',subject=target,prompt=prompt,direct_binding_mode='assigned',direct_targets=[target]*len(selected),
        direct_reference_ids=image_ids,direct_characters=cards,direct_edit_policy=policy,
        direct_preset=body.preset,direct_character_version='v1.5',steps=8 if body.preset=='hands' else 20,
        lora_strength=.75 if body.preset=='hands' else 1,frames=end-start,direct_start_seconds=start/24,
        resolution='1344x768' if body.quality=='768p' else '864x480',match_source_orientation=True,
        direct_crop_face=False,direct_video_only=True,direct_test=True,seed=body.seed).model_dump(mode='json')
    # Native recipe decides its strength; don't duplicate a potentially revised value.
    if body.preset=='hands':
        from ..character_lora import hands_strength
        payload['lora_strength']=hands_strength('v1.5')
    payload.pop('request_key')
    effective_resolution=source_resolution(service.store.asset(source['asset_id']),payload['resolution'],match_aspect=True)
    return dict(version='parody-h3-shot-1',source=dict(source),shot=dict(shot),character_id=character['id'],appearance=dict(appearance),
        references=selected,request=request_payload(body),native_payload=payload,
        timing=dict(start_frame=start,end_frame_exclusive=end,frames=end-start,fps=24,start_ms=start*1000/24,end_ms=end*1000/24),
        resolution=effective_resolution,audio='original_source',performance='original_source',
        warnings=['Prueba visual: no aplica diálogos, voces, risas ni actuaciones nuevas del guion.',
                  'H3 intenta conservar escenario y sujetos no asignados; requiere revisión visual, sin garantía de píxeles idénticos.',
                  '24 fps nativos; el intervalo se ajusta hacia dentro de los límites seleccionados.',
                  'Cada ejecución usa el director Qwen y la cola de H3 existentes.'])


def native_endpoint(app,path,method):
    candidates=[r.endpoint for r in app.router.routes if getattr(r,'path',None)==path and method in getattr(r,'methods',set())]
    if len(candidates)!=1:raise HTTPException(503,'La admisión nativa cambió; no se ejecuta el adaptador.')
    return candidates[0]


def checked_plan(service,project,body,accepted=None):
    try:return compile_plan(service,project,body,accepted)
    except (ValueError,OSError) as error:raise Problem(422,'INVALID_RENDER_PLAN',str(error)) from error


async def native_submit(app,request,payload):
    # Same authenticated request context and workspace. No localhost HTTP or forged identity.
    async def receive():return {'type':'http.request','body':encode(payload).encode(),'more_body':False}
    return await native_endpoint(app,'/api/jobs','POST')(Request(request.scope,receive=receive))


def lookup_job(service,record):
    with service.store.connect() as db:
        row=db.execute('SELECT state FROM jobs WHERE request_key=?',(record['native_request_key'],)).fetchone()
    return json.loads(row[0]) if row else None


def public(service,record):
    job=lookup_job(service,record)
    value=dict(record)
    if job:
        value.update(native_job_id=job['id'],status=job['status'],stage=job['stage'],
                     job={k:job.get(k) for k in ('id','status','stage','progress','error','result_available','qwen_waiting','qwen_question_job_id','scene_resolution')})
        child=service.store.job(job['sequence_active_child']) if job.get('sequence_active_child') else None
        if child and child['parameters'].get('sequence_parent_id')==job['id'] and job['status'] not in ('completed','failed','cancelled'):
            value['stage']=job['stage']+' · '+child['stage']
            value['job']['progress']=child.get('progress')
    try:value['stale']=Repository(service.root).read(record['project_id'])['revision']!=record['project_revision']
    except Problem:value['stale']=True
    from .speech import is_speech,status
    if is_speech(record['plan']):value=status(service,value)
    return value


def register(router,app,service,run,settings):
    def compatible(current):
        if not settings.get('generation_enabled') or not current.settings.get('video_sequences_enabled') or not current.settings.get('qwen_director_enabled'):
            raise HTTPException(503,'Esta prueba necesita la generación, los tramos y el director Qwen existentes habilitados.')

    @router.get('/projects/{project_id}/render-plans')
    def listing(project_id:UUID):
        current=service();run(lambda:Repository(current.root).read(str(project_id)))
        return {'plans':[public(current,r) for r in RenderStore(current.root).listing(str(project_id))]}

    @router.get('/projects/{project_id}/render-plans/{plan_id}')
    def detail(project_id:UUID,plan_id:UUID):
        current=service();return public(current,run(lambda:RenderStore(current.root).read(str(project_id),str(plan_id))))

    @router.post('/projects/{project_id}/render-plans',status_code=201)
    async def prepare(project_id:UUID,body:PlanRequest,idempotency_key:UUID=Header()):
        current=service();compatible(current);ident=str(project_id);store=RenderStore(current.root);payload=request_payload(body)
        prior=run(lambda:store.prior(ident,str(idempotency_key),payload))
        if prior:return public(current,prior)
        project=run(lambda:Repository(current.root).read(ident))
        plan=await asyncio.to_thread(lambda:run(lambda:checked_plan(current,project,body)))
        return public(current,run(lambda:store.create_plan(project,str(idempotency_key),payload,plan)))

    @router.post('/projects/{project_id}/render-plans/{plan_id}/execute',status_code=202)
    async def execute(project_id:UUID,plan_id:UUID,body:ExecuteRequest,request:Request):
        current=service();compatible(current);store=RenderStore(current.root);p=str(project_id);i=str(plan_id)
        record=run(lambda:store.read(p,i))
        if not body.approved or body.plan_hash!=record['plan_hash']:raise HTTPException(409,'Confirma exactamente el plan mostrado antes de generar.')
        from .speech import is_speech
        if is_speech(record['plan']):
            from .speech_runtime import start
            return await start(current,record,run)
        if lookup_job(current,record):return public(current,record)
        project=run(lambda:Repository(current.root).read(p))
        rebuilt=await asyncio.to_thread(lambda:run(lambda:checked_plan(current,project,PlanRequest.model_validate(record['plan']['request']))))
        if hashlib.sha256(encode(rebuilt).encode()).hexdigest()!=record['plan_hash']:raise HTTPException(409,'Cambió una entrada del plan. Prepara otra versión.')
        store.update(p,i,status='submitting',stage='Enviando la petición nativa de H3; conserva este plan para reintentar')
        try:
            job=await native_submit(app,request,dict(rebuilt['native_payload'],request_key=record['native_request_key']))
            accepted=job['parameters']
            if accepted.get('direct_source_sha256')!=rebuilt['source']['sha256'] or accepted.get('direct_reference_hashes')!=[r['sha256'] for r in rebuilt['references']]:
                from ..job_cancel import request as request_cancel
                await request_cancel(current,job['id'])
                raise HTTPException(409,'Una entrada cambió durante la admisión; se canceló esta prueba. Prepara otro plan.')
        except Exception:
            store.update(p,i,status='submission_unknown',stage='Comprueba el envío y reintenta este mismo plan; su clave no cambia')
            raise
        record=store.update(p,i,native_job_id=job['id'],status='accepted',stage='Trabajo nativo aceptado')
        return public(current,record)

    @router.post('/projects/{project_id}/render-plans/{plan_id}/cancel')
    async def cancel(project_id:UUID,plan_id:UUID):
        current=service();record=run(lambda:RenderStore(current.root).read(str(project_id),str(plan_id)));job=lookup_job(current,record)
        from .speech import is_speech
        if is_speech(record['plan']):
            if record['status'] in ('running','cancelling','submission_unknown','interrupted'):
                record=RenderStore(current.root).update(str(project_id),str(plan_id),cancel_requested=True,status='cancelling',
                    stage='Cancelación solicitada; se espera el trabajo propio de Comfy, sin interrumpir otros')
            return public(current,record)
        if not job:raise HTTPException(409,'El plan no tiene trabajo aceptado.')
        from ..job_cancel import request as request_cancel
        try:await request_cancel(current,job['id'])
        except ValueError as error:raise HTTPException(409,str(error)) from error
        return public(current,record)

    @router.get('/projects/{project_id}/render-plans/{plan_id}/video')
    async def video(project_id:UUID,plan_id:UUID,request:Request,download:bool=False):
        current=service();record=run(lambda:RenderStore(current.root).read(str(project_id),str(plan_id)));job=lookup_job(current,record)
        from .speech import is_speech,result_path
        if is_speech(record['plan']):
            from fastapi.responses import FileResponse
            path=run(lambda:result_path(current,record))
            return FileResponse(path,media_type='video/mp4',filename='parodia-sintetica-'+record['id']+'.mp4',headers={'Cache-Control':'no-store'},content_disposition_type='attachment' if download else 'inline')
        if not job:raise HTTPException(404,'Todavía no hay resultado.')
        return await native_endpoint(app,'/api/jobs/{job_id}/result','GET')(UUID(job['id']),download=download,variant='final')

    @router.post('/projects/{project_id}/render-plans/{plan_id}/review')
    async def review(project_id:UUID,plan_id:UUID,body:ExecuteRequest):
        current=service();store=RenderStore(current.root)
        async with current.lock:
            record=run(lambda:store.read(str(project_id),str(plan_id)))
            from .speech import is_speech,result_path
            if not is_speech(record['plan']) or record['status'] not in ('review_pending','completed'):
                raise HTTPException(409,'Termina el plano guiado por voz antes de aprobarlo.')
            if not body.approved or body.plan_hash!=record['plan_hash']:raise HTTPException(409,'Confirma el plano exacto que revisaste.')
            run(lambda:result_path(current,record))
            record=store.update(str(project_id),str(plan_id),status='completed',stage='Naturalidad y sincronización aprobadas por el usuario',
                human_review=dict(artifact_sha256=record['artifact']['sha256'],naturalness=True,synchronization=True),review_required=False)
            return public(current,record)
