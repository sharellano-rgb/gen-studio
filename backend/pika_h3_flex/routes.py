import asyncio
import json
from typing import Literal
from uuid import UUID,uuid4
from fastapi import HTTPException
from pydantic import BaseModel,ConfigDict,Field,field_validator
from .profiles import ENGINE,VERSION,PROFILE,SIZES,capabilities,inventory,PROFILES,DIRECT_ENGINE,dimensions

class Request(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_key:UUID
    reference_id:UUID
    prompt:str=Field(min_length=3,max_length=3000)
    format:Literal['landscape','portrait']='landscape'
    profile:Literal['pika-i2va-480p-5s-v1','pika-i2va-720p-5s-v1','h3-i2va-480p-5s-v1','h3-i2va-720p-5s-v1']=PROFILE
    seed:int=Field(default=42,ge=0,le=9007199254740991,strict=True)

    @field_validator('prompt')
    @classmethod
    def prompt_text(cls,value):
        if len(value.strip())<3:raise ValueError('Describe el movimiento que quieres generar.')
        return value.strip()

def register(app,settings):
    @app.get('/api/engines/pika-h3-flex/capabilities')
    async def available():return await asyncio.to_thread(capabilities,settings)

    @app.get('/api/engines/h3-i2va/capabilities')
    async def direct_available():return await asyncio.to_thread(capabilities,settings,DIRECT_ENGINE)

    @app.get('/api/engines/pika-h3-flex/preflight')
    async def preflight():return await asyncio.to_thread(inventory,settings)

    @app.post('/api/pika-h3-flex/jobs',status_code=202)
    async def submit(body:Request):
        service=app.state.jobs.current
        p=body.model_dump(mode='json',exclude={'request_key'})
        entry=PROFILES[body.profile];w,h=dimensions(p,output=True)
        p.update(engine=entry['engine'],operation='h3_i2va' if entry['engine']==DIRECT_ENGINE else 'pika_i2va',pika_version=VERSION,frames=124,steps=entry['steps'],resolution=f'{w}x{h}')
        # A lost acknowledgement must remain recoverable even if an engine is
        # temporarily unavailable. Lookup is confined to this private Store.
        with service.store.connect() as db:
            row=db.execute('SELECT payload,state FROM jobs WHERE request_key=?',(str(body.request_key),)).fetchone()
        if row:
            if json.loads(row[0])!=p:raise HTTPException(409,'Esta clave de petición ya se usó con otros parámetros.')
            return service.store.job(json.loads(row[1])['id'])
        if not settings.get('generation_enabled'):raise HTTPException(503,'La generación está desactivada.')
        if not settings.get('qwen_director_enabled'):raise HTTPException(503,'Activa el director Qwen para preparar esta toma.')
        if body.format not in settings.get('pika_h3_flex_formats',['landscape']):raise HTTPException(422,'Este formato todavía no está validado en esta instalación.')
        from ..qwen_director import asset
        try:asset(service,str(body.reference_id),'image')
        except ValueError as error:raise HTTPException(404,'Imagen no encontrada en este espacio.') from error
        check=await asyncio.to_thread(inventory,settings)
        if not check['ok']:raise HTTPException(503,' · '.join(check['errors']))
        async with service.lock:
            try:job=service.store.create_job(str(uuid4()),str(body.request_key),p)
            except ValueError as error:raise HTTPException(409,str(error)) from error
            service.wake.set()
            return job
