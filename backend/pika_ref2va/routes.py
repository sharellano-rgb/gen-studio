import asyncio,json
from typing import Literal
from uuid import UUID,uuid4
from fastapi import HTTPException
from pydantic import BaseModel,ConfigDict,Field,field_validator
from .profiles import ENGINE,VERSION,PROFILE,MAX_FRAMES,dimensions,inventory,capabilities
from . import detailed

class Request(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_key:UUID
    video_id:UUID
    reference_id:UUID
    mode:Literal['swap','motion']='swap'
    prompt:str=Field(min_length=3,max_length=3000)
    target:str=Field(default='main foreground performer',min_length=1,max_length=300)
    frames:int=Field(default=120,ge=5,le=MAX_FRAMES,strict=True)
    start_seconds:float=Field(default=0,ge=0,le=86400,allow_inf_nan=False)
    format:Literal['portrait','landscape']='portrait'
    seed:int=Field(default=42,ge=0,le=9007199254740991,strict=True)
    profile:Literal['pika-ref2va-480p-6steps-v1','pika-ref2va-768p-6steps-v1']=PROFILE

    @field_validator('prompt','target')
    @classmethod
    def text(cls,v):
        if not v.strip():raise ValueError('Escribe las indicaciones de la toma.')
        return v.strip()

def register(app,settings):
    @app.get('/api/engines/pika-ref2va/capabilities')
    async def available():return await asyncio.to_thread(capabilities,settings)

    @app.get('/api/engines/pika-ref2va-detailed/capabilities')
    async def detailed_capabilities():
        result=await asyncio.to_thread(capabilities,settings)
        return {**result,'name':detailed.NAME,'method':detailed.METHOD,'analysis_policy':detailed.VERSION}

    @app.post('/api/pika-ref2va/jobs',status_code=202)
    async def submit(body:Request):return await accept(body)

    @app.post('/api/pika-ref2va-detailed/jobs',status_code=202)
    async def submit_detailed(body:Request):return await accept(body,detailed.VERSION)

    async def accept(body:Request,analysis_policy=None):
        service=app.state.jobs.current;request=body.model_dump(mode='json',exclude={'request_key'})
        async with service.lock:
            with service.store.connect() as db:
                row=db.execute('SELECT state FROM jobs WHERE request_key=?',(str(body.request_key),)).fetchone()
            if row:
                job=json.loads(row[0])
                if job['parameters'].get('pika_ref2va_request')!=request or job['parameters'].get(detailed.FIELD)!=analysis_policy:raise HTTPException(409,'Esta clave corresponde a otra solicitud.')
                return service.store.job(job['id'])
            if not settings.get('generation_enabled'):raise HTTPException(503,'La generación está desactivada.')
            if not settings.get('qwen_director_enabled') or not settings.get('video_sequences_enabled'):
                raise HTTPException(503,'Pika Ref2VA requiere Qwen y la generación por tramos.')
            if body.format not in settings.get('pika_ref2va_formats',['portrait']):raise HTTPException(422,'Este formato aún no está validado para Ref2VA.')
            from ..qwen_director import asset
            from ..media import probe
            from ..head import digest
            try:
                source=asset(service,str(body.video_id),'video');reference=asset(service,str(body.reference_id),'image')
            except ValueError as error:raise HTTPException(404,'Entrada no encontrada en este espacio.') from error
            check=await asyncio.to_thread(inventory,settings)
            if not check['ok']:raise HTTPException(503,' · '.join(check['errors']))
            info=await asyncio.to_thread(probe,source);start=round(body.start_seconds*24)
            if start+body.frames>round(info['duration']*24):raise HTTPException(422,'El intervalo supera la duración del vídeo.')
            w,h=dimensions({'profile':body.profile,'format':body.format})
            p={**request,'engine':ENGINE,'operation':'pika_'+body.mode,'pika_ref2va_version':VERSION,
                'pika_ref2va_request':request,'start_seconds':start/24,'start_frame':start,
                'resolution':f'{w}x{h}','steps':6,'source_sha256':await asyncio.to_thread(digest,source),
                'reference_sha256':await asyncio.to_thread(digest,reference)}
            if analysis_policy:p[detailed.FIELD]=analysis_policy
            if body.mode=='motion':
                from .motion_direction import FIELD,VERSION as direction_version
                p[FIELD]=direction_version
            elif body.mode=='swap':
                from .swap_direction import FIELD,VERSION as direction_version
                p[FIELD]=direction_version
            job=service.store.create_job(str(uuid4()),str(body.request_key),p);service.wake.set();return job
