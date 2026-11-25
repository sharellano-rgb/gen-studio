"""Authenticated anchor endpoints, attached to the existing FastAPI app."""
import asyncio, json, re
from pathlib import Path
from uuid import UUID, uuid4
from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from .face import available
from .head import digest
from .media import probe

class AnchorRequest(BaseModel):
    request_key: UUID
    face_reference_id: UUID
    source: str = Field(default='original',pattern=r'^(original|[a-z0-9-]{1,80})$')
    region: tuple[int,int,int]

def resolve(service,parent,source):
    if source=='original':
        if parent['parameters'].get('operation')=='head_detail':
            raise ValueError('Selecciona una toma de escena completa.')
        path=service.root/'results'/(parent['id']+'.mp4')
        name='Generación base de esta toma'
    else:
        if not re.fullmatch(r'[a-z0-9-]{1,80}',source): raise ValueError('Toma no válida.')
        folder=service.root/'identity-tests'/source; report_path=folder/'video-report.json'
        if not report_path.is_file(): raise ValueError('Prueba no disponible.')
        report=json.loads(report_path.read_text(encoding='utf-8'))
        if report.get('source_job')!=parent['id'] or not report.get('review_eligible') or report.get('cpu_face_swap'):
            raise ValueError('Esta prueba no está disponible como base de corrección.')
        path=folder/'corrected.mp4';name=report.get('name',source)
    if not path.is_file(): raise ValueError('El vídeo base no está disponible.')
    return path,name

def register(app,settings):
    @app.get('/api/jobs/{job_id}/anchor-options')
    async def options(job_id:UUID):
        service=app.state.jobs; parent=service.store.job(str(job_id))
        if not parent: raise HTTPException(404,'Trabajo no encontrado.')
        choices=['original']+[p.parent.name for p in (service.root/'identity-tests').glob('*/video-report.json')]
        sources=[]
        for source in choices:
            try:
                path,name=resolve(service,parent,source)
                metadata=await asyncio.to_thread(probe,path)
                sources.append({'id':source,'name':name,**metadata,
                    'url':f'/api/jobs/{job_id}/result?variant=original' if source=='original' else f'/api/identity-tests/{source}/video'})
            except (ValueError,KeyError): continue
        return {'sources':sources}

    @app.post('/api/jobs/{job_id}/anchor-face',status_code=202)
    async def submit(job_id:UUID,body:AnchorRequest):
        if not settings['generation_enabled'] or not available(settings):
            raise HTTPException(503,'La corrección facial local no está disponible.')
        service=app.state.jobs
        async with service.lock:
            parent=service.store.job(str(job_id))
            if not parent: raise HTTPException(404,'Trabajo no encontrado.')
            if parent['status'] not in ('completed','failed') or not parent.get('result_available') or parent['parameters'].get('operation'):
                raise HTTPException(409,'Elige una generación de escena terminada.')
            face=service.store.asset(str(body.face_reference_id))
            if not face or face['kind']!='image': raise HTTPException(400,'Selecciona una foto real del rostro.')
            try:
                path,name=resolve(service,parent,body.source)
                metadata=await asyncio.to_thread(probe,path)
                x,y,size=body.region
                if min(x,y)<0 or size<32 or x+size>metadata['width'] or y+size>metadata['height']:
                    raise ValueError('La zona de cabeza está fuera del vídeo.')
                if metadata['duration']>5.18: raise ValueError('Usa una toma de hasta 5,17 segundos.')
                payload={**parent['parameters'],'operation':'identity_anchor','anchor_parent_id':parent['id'],
                    'anchor_source':str(path),'anchor_source_name':name,'anchor_sha256':await asyncio.to_thread(digest,path),
                    'anchor_region':list(body.region),'face_reference_id':str(body.face_reference_id),'preserve_face':False}
                child=service.store.create_job(str(uuid4()),str(body.request_key),payload)
            except ValueError as error: raise HTTPException(409,str(error)) from error
            service.wake.set()
            return child

    @app.get('/api/jobs/{job_id}/anchor-detail')
    async def detail(job_id:UUID,download:bool=False):
        service=app.state.jobs;job=service.store.job(str(job_id))
        path=service.root/'results'/(str(job_id)+'.detail.mp4')
        if not job or job['parameters'].get('operation')!='identity_anchor' or not job.get('face_result_available') or not path.is_file():
            raise HTTPException(404,'El primer plano todavía no está disponible.')
        return FileResponse(path,media_type='video/mp4',filename=f'GEN-STUDIO-{job_id}-detail.mp4' if download else None)
