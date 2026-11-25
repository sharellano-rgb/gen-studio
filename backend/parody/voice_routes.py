import asyncio
from uuid import UUID
from fastapi import File, Header, HTTPException, UploadFile
from ..media import save_upload
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from .repository import Repository, Problem
from .script import line_binding
from .voice_store import VoiceStore
from .voice_runtime import availability, execute
from .analysis_media import sha256


class PreviewRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_revision:int=Field(ge=1,strict=True)
    line_id:UUID


def register(router,service,run):
    def visible(current,record):
        value=dict(record)
        task=getattr(current,'parody_voice_tasks',{}).get(record['id'])
        if record['status'] in ('running','cancelling') and (task is None or task.done()):
            value.update(status='interrupted',stage='El servicio se interrumpió; no se reenvía la frase automáticamente.')
        try:
            project=Repository(current.root).read(record['project_id'])
            _,fingerprint=line_binding(project,record['line_id'])
            value['stale']=fingerprint!=record['input_hash']
        except Problem:value['stale']=True
        return value

    @router.post('/voice-assets', status_code=201)
    async def upload_voice(file: UploadFile = File()):
        current = service()
        try:
            return await save_upload(file,current.root,current.store,voice_audio=True)
        except (ValueError,TimeoutError) as error:
            raise HTTPException(400,str(error)) from error

    @router.get('/voice-capabilities')
    async def capabilities():
        service()
        return await availability()

    @router.get('/projects/{project_id}/voice-previews')
    def listing(project_id:UUID):
        current=service();run(lambda:Repository(current.root).read(str(project_id)))
        return {'previews':[visible(current,r) for r in VoiceStore(current.root).listing(str(project_id))]}

    @router.get('/projects/{project_id}/voice-previews/{preview_id}')
    def preview(project_id:UUID,preview_id:UUID):
        current=service()
        return visible(current,run(lambda:VoiceStore(current.root).read(str(project_id),str(preview_id))))

    @router.post('/projects/{project_id}/voice-previews',status_code=202)
    async def submit(project_id:UUID,body:PreviewRequest,idempotency_key:UUID=Header()):
        current=service();project_id=str(project_id);store=VoiceStore(current.root);payload=body.model_dump(mode='json')
        async with current.lock:
            prior=run(lambda:store.prior(project_id,str(idempotency_key),payload))
            if prior:return visible(current,prior)
            project=run(lambda:Repository(current.root).read(project_id))
            if project['archived'] or project['revision']!=body.expected_revision:
                raise HTTPException(409,detail={'code':'REVISION_CONFLICT','message':'Abre la revisión actual de un proyecto activo antes de preparar voz.'})
            binding,fingerprint=run(lambda:line_binding(project,body.line_id))
            voice=binding['line']['voice']
            if binding['line']['kind']=='speech' and voice['mode']=='clone' and (not voice['authorized'] or len(voice['permission_note'])<10):
                raise HTTPException(422,detail={'code':'VOICE_PERMISSION','message':'Confirma el permiso y su alcance antes de clonar la voz.'})
            if current.enhancer_active or current.store.unfinished_jobs() or not current.reserve_analysis():
                raise HTTPException(409,detail={'code':'ENGINE_BUSY','message':'Hay otro trabajo usando el turno. Espera a que termine.'})
            try:
                record=run(lambda:store.create(project,str(idempotency_key),payload,binding,fingerprint))
                current.enhancer_active=True
                task=asyncio.create_task(execute(current,record));current.enhancer_task=task
                if not hasattr(current,'parody_voice_tasks'):current.parody_voice_tasks={}
                current.parody_voice_tasks[record['id']]=task
                return record
            except BaseException:
                current.enhancer_active=False;current.release_analysis();current.wake.set();raise

    @router.post('/projects/{project_id}/voice-previews/{preview_id}/cancel')
    def cancel(project_id:UUID,preview_id:UUID):
        current=service();store=VoiceStore(current.root)
        record=run(lambda:store.read(str(project_id),str(preview_id)))
        if record['status'] in ('running','cancelling'):
            record=store.update(str(project_id),str(preview_id),cancel_requested=True,status='cancelling',stage='Cancelando trabajador propio; esperando liberación')
        return visible(current,record)

    @router.get('/projects/{project_id}/voice-previews/{preview_id}/audio')
    def audio(project_id:UUID,preview_id:UUID):
        current=service();store=VoiceStore(current.root);record=run(lambda:store.read(str(project_id),str(preview_id)))
        if record['status']!='review_pending' or not record.get('artifact'):
            raise HTTPException(409,'La frase todavía no tiene un audio validado.')
        path=store.directory(str(preview_id))/'preview.wav'
        if not path.is_file() or sha256(path)!=record['artifact']['sha256']:raise HTTPException(409,'Cambió el audio guardado.')
        return FileResponse(path,media_type='audio/wav',filename='parodia-sintetica-'+str(preview_id)+'.wav',headers={'Cache-Control':'no-store'},content_disposition_type='inline')
