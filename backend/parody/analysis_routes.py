import asyncio
from uuid import UUID
from fastapi import Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from .. import qwen_runtime as qr
from .repository import Repository, Problem
from .analysis_store import AnalysisStore, ACTIVE
from .analysis_media import sha256
from .analysis import execute


class AnalyzeRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    acting_reference_id: UUID | None = None
    expected_revision: int = Field(ge=1,strict=True)


def register(router, service, run, source_reference):
    from . import acting_audio
    acting_audio.register(router, service, run, source_reference)

    def visible(current, record):
        value=dict(record)
        task=getattr(current,'parody_analysis_tasks',{}).get(record['id'])
        if record['status'] in ACTIVE and (task is None or task.done()):
            value.update(status='interrupted',stage='El servicio se interrumpió; conserva evidencia parcial',
                         error='Inicia otro análisis para completar. No se reenvía automáticamente.')
        return value

    @router.get('/projects/{project_id}/analyses')
    def analyses(project_id:UUID):
        current=service()
        run(lambda:Repository(current.root).read(str(project_id)))
        return {'analyses':[visible(current,r) for r in AnalysisStore(current.root).listing(str(project_id))]}

    @router.get('/projects/{project_id}/analyses/{analysis_id}')
    def analysis(project_id:UUID,analysis_id:UUID):
        current=service()
        return visible(current,run(lambda:AnalysisStore(current.root).read(str(project_id),str(analysis_id))))

    @router.post('/projects/{project_id}/analyses',status_code=202)
    async def analyze(project_id:UUID,body:AnalyzeRequest,idempotency_key:UUID=Header()):
        # Capture the actual authenticated service before creating any task.
        current=service(); project_id=str(project_id); key=str(idempotency_key)
        store=AnalysisStore(current.root); payload=body.model_dump(mode="json",exclude_none=True)
        async with current.lock:
            prior=run(lambda:store.prior(project_id,key,payload))
            if prior:return visible(current,prior)
            project=run(lambda:Repository(current.root).read(project_id))
            if project['archived'] or not project['source']:
                raise HTTPException(409,detail={'code':'SOURCE_REQUIRED','message':'Abre un proyecto activo con vídeo fuente guardado.'})
            if project['revision']!=body.expected_revision:
                raise HTTPException(409,detail={'code':'REVISION_CONFLICT','message':'El proyecto cambió. Recarga antes de analizar.'})
            try:
                target = project['source']
                if body.acting_reference_id:
                    target = next((r for c in project['cast']['characters'] for r in c['references']
                                   if r['id'] == str(body.acting_reference_id) and r['role'] == 'acting'), None)
                    if not target:
                        raise Problem(422,'REFERENCE_REQUIRED','Guarda el clip de actuación en la ficha.')
                source=await asyncio.to_thread(source_reference,current,target['asset_id'])
                if source['sha256']!=target['sha256']:raise Problem(409,'SOURCE_CHANGED','Cambió el vídeo fuente guardado.')
            except Problem as e:raise HTTPException(e.status,detail=e.detail) from e
            if current.enhancer_active or current.store.unfinished_jobs():
                raise HTTPException(409,detail={'code':'ENGINE_BUSY','message':'Espera a que termine el trabajo actual.'})
            if not current.reserve_analysis():
                raise HTTPException(409,detail={'code':'ENGINE_BUSY','message':'El motor compartido está ocupado.'})
            try:
                record=run(lambda:store.create(project,key,payload,qr.make_binding(),source))
                asset=current.store.asset(source['asset_id'])
                path=(current.root/'inputs'/asset['filename']).resolve()
                current.enhancer_active=True
                task=asyncio.create_task(execute(current,record,path))
                current.enhancer_task=task
                if not hasattr(current,'parody_analysis_tasks'):current.parody_analysis_tasks={}
                current.parody_analysis_tasks[record['id']]=task
                return record
            except BaseException:
                current.enhancer_active=False;current.release_analysis();current.wake.set()
                raise

    @router.post('/projects/{project_id}/analyses/{analysis_id}/cancel')
    def cancel(project_id:UUID,analysis_id:UUID):
        current=service();store=AnalysisStore(current.root)
        record=run(lambda:store.read(str(project_id),str(analysis_id)))
        if record['status'] in ACTIVE:
            record=store.update(str(project_id),str(analysis_id),cancel_requested=True,status='cancelling',
                stage='Cancelación solicitada; esperando la operación actual y la descarga de Qwen')
        return visible(current,record)

    @router.get('/projects/{project_id}/analyses/{analysis_id}/frames/{frame_index}')
    def frame(project_id:UUID,analysis_id:UUID,frame_index:int):
        current=service();store=AnalysisStore(current.root)
        record=run(lambda:store.read(str(project_id),str(analysis_id)))
        sample=next((s for w in (record.get('plan') or {}).get('windows',[]) for s in w['samples'] if s['frame_index']==frame_index),None)
        if not sample:raise HTTPException(404,'Fotograma no encontrado.')
        path=(store.directory(str(analysis_id))/sample['filename']).resolve()
        if not path.is_relative_to(store.directory(str(analysis_id))) or not path.is_file() or sha256(path)!=sample['sha256']:
            raise HTTPException(409,'Cambió la evidencia del análisis.')
        return FileResponse(path,media_type='image/jpeg',headers={'Cache-Control':'no-store'})
