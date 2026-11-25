import asyncio,json
from typing import Literal
from uuid import UUID,uuid4
from fastapi import HTTPException
from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator
from .profiles import ENGINE,VERSION,MAX_FRAMES,SIZES,inventory,capabilities

class Character(BaseModel):
    model_config=ConfigDict(extra='forbid')
    target:str=Field(min_length=1,max_length=300)
    reference_id:UUID

    @field_validator('target')
    @classmethod
    def target_text(cls,v):
        if not v.strip() or '<' in v or '>' in v:raise ValueError('Describe el personaje original sin etiquetas.')
        return v.strip()

class Request(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_key:UUID
    video_id:UUID
    reference_id:UUID
    additional_characters:list[Character]=Field(default_factory=list,max_length=3)
    auto_keyframes:bool=Field(default=False,strict=True)
    auto_detail_corrections:bool=Field(default=False,strict=True)
    mode:Literal['swap','motion']='swap'
    prompt:str=Field(min_length=3,max_length=3000)
    target:str=Field(default='main foreground performer',min_length=1,max_length=300)
    frames:int=Field(default=120,ge=5,le=MAX_FRAMES,strict=True)
    start_seconds:float=Field(default=0,ge=0,le=86400,allow_inf_nan=False)
    format:Literal['landscape','portrait']='landscape'
    short_edge:Literal[736]=736
    seed:int=Field(default=42,ge=0,le=9007199254739991,strict=True)
    reference_mode:Literal['appearance','painted_frame']='appearance'
    pin_frame:int|None=Field(default=None,ge=0,strict=True)

    @field_validator('prompt','target')
    @classmethod
    def text(cls,v):
        if not v.strip():raise ValueError('Escribe las indicaciones de la toma.')
        if '<' in v or '>' in v:raise ValueError('Escribe texto sin etiquetas de referencias.')
        return v.strip()

    @model_validator(mode='after')
    def pin(self):
        if self.auto_detail_corrections and not self.auto_keyframes:raise ValueError('La corrección de detalles requiere fotogramas automáticos.')
        if self.auto_keyframes and self.reference_mode!='appearance':raise ValueError('Los fotogramas automáticos requieren referencias de apariencia.')
        if self.additional_characters:
            if self.reference_mode!='appearance':raise ValueError('Para varios personajes usa imágenes de apariencia, una asignación por personaje.')
            targets=[self.target,*[c.target for c in self.additional_characters]]
            if len({x.casefold() for x in targets})!=len(targets):raise ValueError('Describe cada personaje original de forma diferente.')
        if self.reference_mode=='painted_frame':
            if self.pin_frame is None or self.pin_frame>=self.frames:raise ValueError('Indica el fotograma de la referencia editada dentro de la toma.')
        elif self.pin_frame is not None:raise ValueError('Una referencia de apariencia no fija la pose de un fotograma.')
        return self

def register(app,settings):
    @app.get('/api/viggle-remake/jobs/{job_id}/keyframe')
    async def keyframe(job_id:UUID):
        from pathlib import Path
        from fastapi.responses import FileResponse
        from .state import digest
        service=app.state.jobs.current;job=service.store.job(str(job_id))
        if not job or job['parameters'].get('engine')!=ENGINE:raise HTTPException(404,'Toma no encontrada.')
        child=service.store.job(job.get('viggle_anchor_job_id','')) if job.get('video_sequence_version') else job
        if not child or (child is not job and child['parameters'].get('sequence_parent_id')!=job['id']):raise HTTPException(404,'Fotograma no disponible.')
        folder=(service.root/'jobs'/child['id']).resolve();receipt=folder/'viggle-painted-anchor.json'
        from .shot_anchor import enabled as shot_enabled
        if shot_enabled(child['parameters']):receipt=folder/'shot-anchor-approved.json'
        if not receipt.exists():raise HTTPException(404,'Fotograma no disponible.')
        data=json.loads(receipt.read_text(encoding='utf-8'));path=(folder/data['filename']).resolve()
        if path.parent!=folder or not path.is_file() or await asyncio.to_thread(digest,path)!=data['sha256']:raise HTTPException(409,'El fotograma guardado cambió.')
        return FileResponse(path,media_type='image/png',headers={'Cache-Control':'no-store'})

    @app.get('/api/engines/viggle-remake/capabilities')
    async def available():return await asyncio.to_thread(capabilities,settings)

    @app.post('/api/viggle-remake/jobs',status_code=202)
    async def submit(body:Request):
        service=app.state.jobs.current;request=body.model_dump(mode='json',exclude={'request_key'})
        if not body.auto_keyframes:request.pop('auto_keyframes',None)
        if not body.auto_detail_corrections:request.pop('auto_detail_corrections',None)
        if not body.additional_characters:request.pop('additional_characters',None) # Preserve historical idempotent requests.
        async with service.lock:
            with service.store.connect() as db:
                row=db.execute('SELECT state FROM jobs WHERE request_key=?',(str(body.request_key),)).fetchone()
            if row:
                old=json.loads(row[0])
                if old['parameters'].get('viggle_request')!=request:raise HTTPException(409,'Esta clave corresponde a otra solicitud.')
                return service.store.job(old['id'])
            if not settings.get('generation_enabled'):raise HTTPException(503,'La generación está desactivada.')
            if not settings.get('qwen_director_enabled') or not settings.get('video_sequences_enabled'):
                raise HTTPException(503,'Viggle requiere Qwen y la generación por ventanas.')
            if body.format not in settings.get('viggle_remake_formats',['landscape']):raise HTTPException(422,'Este formato aún no está validado para Viggle.')
            if body.additional_characters and not settings.get('viggle_multi_enabled',False):raise HTTPException(503,'El reemplazo múltiple de Viggle está desactivado.')
            if body.auto_keyframes and not settings.get('viggle_keyframes_enabled',False):raise HTTPException(503,'Los fotogramas automáticos de Viggle están desactivados.')
            if body.auto_detail_corrections and not settings.get('viggle_detail_corrections_enabled',False):raise HTTPException(503,'La corrección experimental de detalles está desactivada.')
            if body.auto_keyframes:
                from ..qwen_image21 import preflight
                editor=await preflight(service,'edit')
                if not editor['ok']:raise HTTPException(503,'El editor local no está disponible: '+' · '.join(editor['errors']))
            from ..qwen_director import asset
            from ..media import probe
            from ..qwen_runtime import make_binding
            from .state import digest
            try:
                source=asset(service,str(body.video_id),'video');reference=asset(service,str(body.reference_id),'image')
                extra=[asset(service,str(c.reference_id),'image') for c in body.additional_characters]
            except ValueError as error:raise HTTPException(404,'Entrada no encontrada en este espacio.') from error
            if any(path.suffix.lower() not in ('.png','.jpg','.jpeg','.webp') for path in [reference,*extra]):raise HTTPException(422,'Las referencias deben ser PNG, JPG o WebP.')
            check=await asyncio.to_thread(inventory,settings,'swap')
            if not check['ok']:raise HTTPException(503,' · '.join(check['errors']))
            info=await asyncio.to_thread(probe,source);start=round(body.start_seconds*24)
            if start+body.frames>round(info['duration']*24):raise HTTPException(422,'El intervalo supera la duración del vídeo.')
            if body.auto_keyframes:
                from .painted import canvas
                from .geometry import inventory as pose_inventory
                await asyncio.to_thread(pose_inventory,settings)
                w,h=canvas(info)
            else:w,h=SIZES[body.short_edge][body.format]
            p={**request,'engine':ENGINE,'operation':'viggle_'+body.mode,'viggle_version':VERSION,'viggle_native_mode':'swap',
               'viggle_request':request,'start_seconds':start/24,'start_frame':start,'resolution':f'{w}x{h}','steps':6,
               'source_sha256':await asyncio.to_thread(digest,source),'reference_sha256':await asyncio.to_thread(digest,reference)}
            if body.auto_keyframes:
                from .painted import FIELD,VERSION as PAINTED_VERSION
                p[FIELD]=PAINTED_VERSION
                if settings.get('viggle_shot_review_enabled',False):
                    from .shot_anchor import FIELD as SHOT_FIELD,VERSION as SHOT_VERSION,preflight as shot_preflight
                    try:await shot_preflight(service)
                    except ValueError as error:raise HTTPException(503,str(error)) from error
                    p[SHOT_FIELD]=SHOT_VERSION
                    if settings.get('viggle_anchor_selection_enabled',False):
                        from .anchor_selection import FIELD as SELECT_FIELD,VERSION as SELECT_VERSION
                        p[SELECT_FIELD]=SELECT_VERSION
            if body.auto_detail_corrections:
                from .detail_director import FIELD as DETAIL_FIELD,VERSION as DETAIL_VERSION
                p[DETAIL_FIELD]=DETAIL_VERSION
            if extra:
                from .characters import VERSION as CHARACTERS_VERSION
                p.update(viggle_characters_version=CHARACTERS_VERSION,additional_reference_sha256=[await asyncio.to_thread(digest,path) for path in extra])
            job=service.store.create_job(str(uuid4()),str(body.request_key),p,qwen_binding=make_binding('qwen3.8:27b',0))
            service.wake.set();return job
