import asyncio
from uuid import UUID
from fastapi import Header, HTTPException
from fastapi.responses import FileResponse
from .repository import Repository, Problem
from .montage import MontageStore, MontageRequest, compile_montage, fingerprint,request_payload
from .render import ExecuteRequest
from .montage_runtime import execute
from .analysis_media import sha256
from .lipsync_runtime import availability


def register(router,service,run,settings):
    def visible(current,record):
        value=dict(record);task=getattr(current,'parody_montage_tasks',{}).get(record['id'])
        if record['status'] in ('running','cancelling') and (task is None or task.done()):
            value.update(status='interrupted',stage='Servicio interrumpido; reanuda este mismo montaje')
        try:value['stale']=Repository(current.root).read(record['project_id'])['revision']!=record['project_revision']
        except Problem:value['stale']=True
        return value

    @router.get('/montage-capabilities')
    def capabilities():
        service();lip=availability();lip['enabled']=settings.get('parody_lipsync_enabled') is True
        from .timing_integration import availability as timing_availability
        return dict(lipsync=lip,audio_timing=timing_availability(settings),audio_separation=False,fps=24,max_seconds=120,original_shots=True,original_reframe=True,compact_native_dialogue=True)

    @router.get('/projects/{project_id}/montages')
    def listing(project_id:UUID):
        current=service();run(lambda:Repository(current.root).read(str(project_id)))
        return {'montages':[visible(current,r) for r in MontageStore(current.root).listing(str(project_id))]}

    @router.get('/projects/{project_id}/montages/{montage_id}')
    def detail(project_id:UUID,montage_id:UUID):
        current=service();return visible(current,run(lambda:MontageStore(current.root).read(str(project_id),str(montage_id))))

    @router.post('/projects/{project_id}/montages',status_code=201)
    async def prepare(project_id:UUID,body:MontageRequest,idempotency_key:UUID=Header()):
        current=service();p=str(project_id);store=MontageStore(current.root);payload=request_payload(body)
        if any(s.lipsync!='none' for s in body.segments) and settings.get('parody_lipsync_enabled') is not True:
            raise HTTPException(503,'Lip sync experimental no está habilitado.')
        async with current.lock:
            prior=run(lambda:store.prior(p,str(idempotency_key),payload))
            if prior:return visible(current,prior)
            project=run(lambda:Repository(current.root).read(p))
            plan=await asyncio.to_thread(lambda:run(lambda:compile_montage(current,project,body,compact_default=True)))
            return visible(current,run(lambda:store.create_plan(project,str(idempotency_key),payload,plan)))

    @router.post('/projects/{project_id}/montages/{montage_id}/execute',status_code=202)
    async def submit(project_id:UUID,montage_id:UUID,body:ExecuteRequest):
        current=service();p=str(project_id);ident=str(montage_id);store=MontageStore(current.root)
        async with current.lock:
            record=run(lambda:store.read(p,ident));state=visible(current,record)
            if not body.approved or record['plan_hash']!=body.plan_hash:raise HTTPException(409,'Confirma exactamente el montaje y las voces revisadas.')
            if state['status'] in ('running','cancelling','review_pending'):return state
            if state['status']=='timing_review_required':raise HTTPException(409,'Revisa el plano y prepara una nueva versión; no se repite el mismo ajuste indefinidamente.')
            project=run(lambda:Repository(current.root).read(p))
            request=MontageRequest(expected_revision=project['revision'],segments=[s['inputs']['selection'] for s in record['plan']['segments']])
            accepted={i:s['inputs']['timing_runtime'] for i,s in enumerate(record['plan']['segments']) if 'timing_runtime' in s['inputs']}
            plan=await asyncio.to_thread(lambda:run(lambda:compile_montage(current,project,request,accepted)))
            if fingerprint(plan)!=record['plan_hash']:raise HTTPException(409,'Cambió una entrada; prepara otro montaje. Los segmentos intactos se reutilizan.')
            if any(s.lipsync!='none' for s in request.segments) and settings.get('parody_lipsync_enabled') is not True:raise HTTPException(503,'Lip sync desactivado.')
            if current.enhancer_active or current.store.unfinished_jobs() or not current.reserve_analysis():raise HTTPException(409,'Otro trabajo usa el turno; reintenta este mismo montaje al terminar.')
            try:
                record=store.update(p,ident,status='running',stage='Preparando montaje',cancel_requested=False,error=None,resources_released=False,timing_review=None)
                current.enhancer_active=True;task=asyncio.create_task(execute(current,record));current.enhancer_task=task
                if not hasattr(current,'parody_montage_tasks'):current.parody_montage_tasks={}
                current.parody_montage_tasks[ident]=task
                return record
            except BaseException:
                current.enhancer_active=False;current.release_analysis();current.wake.set();raise

    @router.post('/projects/{project_id}/montages/{montage_id}/cancel')
    def cancel(project_id:UUID,montage_id:UUID):
        current=service();store=MontageStore(current.root);record=run(lambda:store.read(str(project_id),str(montage_id)))
        if visible(current,record)['status'] in ('running','cancelling'):
            record=store.update(str(project_id),str(montage_id),cancel_requested=True,status='cancelling',stage='Cancelando proceso propio; conservando segmentos')
        return visible(current,record)

    @router.get('/projects/{project_id}/montages/{montage_id}/video')
    def video(project_id:UUID,montage_id:UUID,download:bool=False):
        current=service();store=MontageStore(current.root);record=run(lambda:store.read(str(project_id),str(montage_id)))
        if record['status']!='review_pending' or not record.get('artifact'):raise HTTPException(409,'El montaje todavía no está validado.')
        path=(store.directory(str(montage_id))/record['artifact']['file']).resolve()
        if not path.is_relative_to(store.directory(str(montage_id))) or not path.is_file() or sha256(path)!=record['artifact']['sha256']:raise HTTPException(409,'Cambió el montaje guardado.')
        return FileResponse(path,media_type='video/mp4',filename='parodia-sintetica-'+str(montage_id)+'.mp4',headers={'Cache-Control':'no-store'},content_disposition_type='attachment' if download else 'inline')
