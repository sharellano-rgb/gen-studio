"""Unmodified source shots for montage, with explicit full-frame normalization."""
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .repository import Problem,Repository
from .voice_runtime import local_input
from .reframe import Reframe,VERSION as REFRAME_VERSION,validate_source


class OriginalSegment(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source_shot_id:UUID
    source_project_id:UUID|None=None
    start_frame:int=Field(default=0,ge=0,le=2879,strict=True)
    end_frame:int=Field(gt=0,le=2880,strict=True)
    resolution:str=Field(pattern=r'^\d{2,4}x\d{2,4}$')
    fit:Literal['contain','reframe']
    reframe:Reframe|None=None
    audio:Literal['source']='source'
    lipsync:Literal['none']='none'
    timing:None=None

    @model_validator(mode='after')
    def valid(self):
        if (self.fit=='reframe')!=(self.reframe is not None):
            raise ValueError('Elige encuadre completo o proporciona un reencuadre explícito.')
        if not 0<self.end_frame-self.start_frame<=120:
            raise ValueError('Selecciona hasta cinco segundos del plano original.')
        w,h=map(int,self.resolution.split('x'))
        if any(v<16 or v>2048 or v%2 for v in (w,h)) or w*h>2097152:
            raise ValueError('Resolución de montaje no compatible; utiliza dimensiones pares de hasta 2 MP.')
        return self


def compile_original(service,project,selection):
    if selection.source_project_id is not None:
        project=Repository(service.root).read(str(selection.source_project_id))
    if project['archived']:raise Problem(409,'SOURCE_ARCHIVED','El proyecto original está archivado.')
    source=project.get('source')
    if not source:raise Problem(422,'SOURCE_REQUIRED','Selecciona una fuente.')
    shot=next((s for s in project['draft']['shots'] if s['id']==str(selection.source_shot_id)),None)
    if not shot:raise Problem(422,'SOURCE_SHOT','El plano original no pertenece al proyecto.')
    if shot['start_ms']<0 or shot['end_ms']>source['duration_ms']:
        raise Problem(422,'SOURCE_RANGE','El plano sale del vídeo fuente.')
    if selection.end_frame*1000>(shot['end_ms']-shot['start_ms'])*24:
        raise Problem(422,'TRIM_RANGE','El recorte supera el plano original; no debe cruzar otro plano.')
    try:
        path=local_input(service,source)
        asset=service.store.asset(source['asset_id'])
        if asset['kind']!='video':raise ValueError('La fuente no es un vídeo.')
        if selection.reframe:validate_source(path,selection.reframe)
    except ValueError as error:
        raise Problem(409,'SOURCE_CHANGED',str(error)) from error
    width,height=map(int,selection.resolution.split('x'))
    return dict(version=REFRAME_VERSION if selection.reframe else 'parody-original-segment-2',source_project_id=project['id'],source=dict(source),shot=dict(shot),
        video_sha256=source['sha256'],selection=selection.model_dump(mode='json',exclude={'timing'}|({'reframe'} if selection.reframe is None else set())),
        source_start_seconds=shot['start_ms']/1000+selection.start_frame/24,
        frames=selection.end_frame-selection.start_frame,width=width,height=height,fps=24,voices=[])
