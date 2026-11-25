"""Immutable edit decisions. H3 outputs are inputs, never regenerated here."""
import hashlib
import math
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .repository import Repository, Problem, encode
from .analysis_store import AnalysisStore
from .analysis_media import sha256
from .render import RenderStore, lookup_job, compile_plan, PlanRequest
from .script import line_binding
from .voice_store import VoiceStore
from .original_montage import OriginalSegment,compile_original


def fingerprint(value):return hashlib.sha256(encode(value).encode()).hexdigest()


class Region(BaseModel):
    model_config=ConfigDict(extra='forbid')
    x:int=Field(ge=0,strict=True)
    y:int=Field(ge=0,strict=True)
    width:int=Field(ge=128,le=1280,strict=True)
    height:int=Field(ge=128,le=1280,strict=True)


class AudioTiming(BaseModel):
    model_config=ConfigDict(extra='forbid')
    mode:Literal['auto','manual']
    delay_samples:int=Field(default=0,strict=True,ge=-48000,le=48000)
    region:Region|None=None
    target_confirmed:bool=False

    @model_validator(mode='after')
    def valid(self):
        if self.mode=='auto' and (self.delay_samples!=0 or not self.region or not self.target_confirmed):
            raise ValueError('Confirma la región del único personaje que habla antes del ajuste automático.')
        if self.mode=='manual' and (self.region or self.target_confirmed):
            raise ValueError('El ajuste manual no utiliza una región automática.')
        return self


class Segment(BaseModel):
    model_config=ConfigDict(extra='forbid')
    render_plan_id:UUID
    start_frame:int=Field(default=0,ge=0,le=123,strict=True)
    end_frame:int=Field(gt=0,le=124,strict=True)
    audio:Literal['source','replace']='source'
    voice_preview_ids:list[UUID]=Field(default_factory=list,max_length=12)
    lipsync:Literal['none','latentsync']='none'
    region:Region|None=None
    timing:AudioTiming|None=None
    compact_dialogue:bool|None=None
    dialogue_edit_version:Literal[1,2]|None=None

    @model_validator(mode='after')
    def valid(self):
        if self.end_frame<=self.start_frame:raise ValueError('El intervalo está vacío.')
        if len(set(self.voice_preview_ids))!=len(self.voice_preview_ids):raise ValueError('No repitas la misma voz.')
        if self.audio=='source' and self.voice_preview_ids:raise ValueError('Con audio original no se añaden voces.')
        if self.audio=='replace' and not self.voice_preview_ids:raise ValueError('Selecciona al menos una voz revisada.')
        if self.lipsync!='none' and (not self.region or self.audio!='replace'):raise ValueError('Lip sync requiere voz nueva y región de un único rostro.')
        if self.timing and (self.audio!='replace' or len(self.voice_preview_ids)!=1 or self.lipsync!='none'):
            raise ValueError('El ajuste temporal requiere una voz y no se combina con LatentSync.')
        return self


class MontageRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_revision:int=Field(ge=1,strict=True)
    segments:list[Segment|OriginalSegment]=Field(min_length=1,max_length=48)


def selection_payload(selection):
    # Do not change historical request hashes, accepted plans or cache keys.
    exclude={'timing'} if selection.timing is None else set()
    if isinstance(selection,OriginalSegment) and selection.reframe is None:exclude.add('reframe')
    if isinstance(selection,Segment) and selection.compact_dialogue is None:exclude.add('compact_dialogue')
    if isinstance(selection,Segment) and selection.dialogue_edit_version is None:exclude.add('dialogue_edit_version')
    return selection.model_dump(mode='json',exclude=exclude)


def request_payload(body):
    return dict(expected_revision=body.expected_revision,segments=[selection_payload(s) for s in body.segments])


class MontageStore(AnalysisStore):
    def __init__(self,root):
        self.folder=Repository(root).path.parent/'montages';self.path=self.folder/'tasks.sqlite3'
        if not self.path.resolve().is_relative_to(Repository(root).path.parent.resolve()):raise Problem(409,'INVALID_STORAGE','Montaje fuera del espacio.')

    def create_plan(self,project,key,body,plan):
        record=super().create(project,key,body,None,project['source'])
        return self.update(project['id'],record['id'],version='parody-montage-1',status='planned',stage='Montaje listo para confirmar',
            plan=plan,plan_hash=fingerprint(plan),artifact=None,resources_released=True)


def result_path(service,job):
    root=(service.root/'results').resolve();path=(root/(str(UUID(job['id']))+'.mp4')).resolve()
    if not root.is_relative_to(service.root.resolve()) or not path.is_relative_to(root) or not path.is_file():
        raise Problem(409,'RENDER_MISSING','Falta el vídeo generado en este espacio.')
    expected=job.get('direct_result_sha256')
    if not expected or sha256(path)!=expected:raise Problem(409,'RENDER_CHANGED','Cambió el resultado H3.')
    return path


def compile_montage(service,project,body,accepted_timing=None,*,compact_default=False):
    if project['archived'] or project['revision']!=body.expected_revision:raise Problem(409,'REVISION_CONFLICT','Guarda y abre el proyecto activo actual.')
    segments=[];output_frame=0;dimensions=None
    rule=project.get('script',{}).get('required_phrase')
    matching_segments=[]
    for selection in body.segments:
        if isinstance(selection,OriginalSegment):
            inputs=compile_original(service,project,selection)
            size=[inputs['width'],inputs['height']]
            if dimensions is None:dimensions=size
            if dimensions!=size:raise Problem(422,'MIXED_RESOLUTION','Elige para el plano original la misma resolución del montaje.')
            segments.append(dict(inputs=inputs,input_hash=fingerprint(inputs),output_start_frame=output_frame))
            output_frame+=inputs['frames']
            continue
        record=RenderStore(service.root).read(project['id'],str(selection.render_plan_id));old=record['plan']
        # A dialogue edit invalidates audio, not an unchanged accepted visual take.
        request=dict(old['request'],expected_revision=project['revision'])
        from .speech import is_speech,result_path as speech_path
        guided=is_speech(old)
        from .ref2va import is_native,validate_montage
        native=is_native(old)
        if selection.compact_dialogue and not native:
            raise Problem(422,'DIALOGUE_ENGINE','El recorte de diálogo requiere una recreación Ref2VA con voz de referencia.')
        if native:validate_montage(selection)
        elif selection.end_frame>120:raise Problem(422,'TRIM_RANGE','El motor anterior admite hasta 120 fotogramas por segmento.')
        rebuilt=compile_plan(service,project,PlanRequest.model_validate(request),old.get('speech'))
        expected=dict(old,request=request)
        if fingerprint(rebuilt)!=fingerprint(expected):raise Problem(409,'VISUAL_STALE','La fuente, asignación o referencias del plano cambiaron; revisa otra toma.')
        job=lookup_job(service,record)
        if guided:
            if record['status']!='completed' or record.get('human_review',{}).get('artifact_sha256')!=record.get('artifact',{}).get('sha256'):
                raise Problem(409,'SPEECH_REVIEW','Aprueba naturalidad y sincronización de este plano antes del montaje.')
            speech_path(service,record)
            job=dict(id=record['id'],status='completed',result_available=True,scene_resolution=old['resolution'],direct_result_sha256=record['artifact']['sha256'])
        if not job or job['status']!='completed' or not job.get('result_available'):raise Problem(409,'RENDER_PENDING','Termina y revisa primero el plano H3.')
        if not guided:result_path(service,job)
        if selection.end_frame>old['timing']['frames']:raise Problem(422,'TRIM_RANGE','El recorte supera la toma H3.')
        width,height=map(int,job['scene_resolution'].split('x'))
        if dimensions is None:dimensions=[width,height]
        if dimensions!=[width,height]:raise Problem(422,'MIXED_RESOLUTION','Selecciona tomas con la misma resolución; no se escala silenciosamente.')
        region=selection.region
        if region and (region.x+region.width>width or region.y+region.height>height):raise Problem(422,'REGION_RANGE','La región sale del encuadre.')
        frames=selection.end_frame-selection.start_frame
        if selection.lipsync!='none' and frames<24:raise Problem(422,'SHORT_LIPSYNC','Lip sync necesita al menos un segundo.')
        if selection.timing:
            if service.settings.get('parody_timing_enabled') is not True:raise Problem(503,'TIMING_DISABLED','El ajuste de voz por plano no está habilitado.')
            if selection.timing.mode=='auto':
                roi=selection.timing.region
                if roi.x+roi.width>width or roi.y+roi.height>height:raise Problem(422,'TIMING_REGION','La región del hablante sale del encuadre.')
                if frames<24:raise Problem(422,'TIMING_SHORT','Selecciona al menos un segundo para medir la voz.')
        start_ms=(old['timing']['start_frame']+selection.start_frame)*1000/24;duration_ms=frames*1000/24
        voices=[]
        for ident in selection.voice_preview_ids:
            voice=VoiceStore(service.root).read(project['id'],str(ident));binding,current_hash=line_binding(project,voice['line_id'])
            if current_hash!=voice['input_hash'] or voice['status']!='review_pending' or not voice.get('artifact'):
                raise Problem(409,'VOICE_STALE','Regenera la frase modificada antes del montaje.')
            line=binding['line'];artifact=voice['artifact'];offset=line['start_ms']-start_ms
            if line['kind']!='speech' or line['shot_id']!=old['shot']['id'] or line['character_id']!=old['character_id']:
                raise Problem(422,'VOICE_ASSIGNMENT','La voz debe pertenecer al personaje y plano seleccionados.')
            if offset < -.01 or offset+artifact['duration_ms']>duration_ms+.01 or line['end_ms']>start_ms+duration_ms+.01:
                raise Problem(422,'VOICE_FIT','La frase y sus pausas deben caber completas. Acorta el guion o amplía el plano; no se recorta ni acelera la voz.')
            path=VoiceStore(service.root).directory(str(ident))/'preview.wav'
            if not path.is_file() or sha256(path)!=artifact['sha256']:raise Problem(409,'VOICE_CHANGED','Cambió el audio de la frase.')
            voices.append(dict(id=str(ident),line_id=line['id'],input_hash=current_hash,sha256=artifact['sha256'],offset_ms=round(offset),duration_ms=artifact['duration_ms']))
            if selection.timing:
                import wave
                try:
                    with wave.open(str(path),'rb') as wav:
                        samples=round(wav.getnframes()*48000/wav.getframerate())
                except (wave.Error,EOFError,OSError) as error:
                    raise Problem(409,'TIMING_VOICE_FORMAT','La vista previa de voz no es un WAV válido; regenera esa frase.') from error
                start_samples=round(offset*48) if guided else round(offset)*48
                shift=selection.timing.delay_samples
                if start_samples+shift<0 or start_samples+shift+samples>frames*2000:
                    raise Problem(422,'TIMING_CLIP','El ajuste cortaría la frase o sus pausas; cambia los tiempos.')
                voices[-1].update(samples_48k=samples,offset_samples=start_samples)
        ordered=sorted(voices,key=lambda v:v['offset_ms'])
        if any(a['offset_ms']+a['duration_ms']>b['offset_ms'] for a,b in zip(ordered,ordered[1:])):
            raise Problem(422,'VOICE_OVERLAP','Las frases se solapan; ajusta sus tiempos antes de montar.')
        inputs=dict(version='parody-segment-2' if selection.timing else 'parody-segment-1',selection=selection_payload(selection),job_id=job['id'],video_sha256=job['direct_result_sha256'],
            voices=voices,width=width,height=height,frames=frames,fps=24)
        if guided:
            # A different line would invalidate the generated mouth, not just its mux.
            if selection.lipsync!='none' or (selection.audio=='replace' and (len(voices)!=1 or voices[0]['sha256']!=old['voice']['sha256'])):
                raise Problem(422,'SPEECH_AUDIO_CHANGED','Conserva la voz que guio este plano; cambiarla requiere otra generación H3.')
            inputs.update(version='parody-ref2va-segment-1' if native else 'parody-speech-segment-1',speech_render=dict(project_id=project['id'],plan_id=record['id']))
        if native and (selection.compact_dialogue is True or (selection.compact_dialogue is None and compact_default)):
            from .dialogue_edit import analyze
            from .speech import voice_path
            import subprocess
            guide=voice_path(service,old['voice']['id'],old['voice']['sha256'])
            try:
                version=selection.dialogue_edit_version or (2 if compact_default else 1)
                edit=analyze(speech_path(service,record),guide,selection.start_frame,selection.end_frame,old['voice']['line'],version)
            except (OSError,subprocess.SubprocessError) as error:
                raise Problem(422,'DIALOGUE_ANALYSIS','No se pudo medir el audio. Reintenta o desactiva el recorte de silencios.') from error
            inputs['selection']['compact_dialogue']=True
            if version==2:inputs['selection']['dialogue_edit_version']=2
            inputs['dialogue_edit']=edit
            inputs['frames']=frames=sum(b-a for a,b in edit['ranges'])
        if selection.timing and selection.timing.mode=='auto':
            from .timing_integration import runtime_binding
            scope=dict(video_sha256=inputs['video_sha256'],voice_sha256=voices[0]['sha256'],start_frame=selection.start_frame,end_frame=selection.end_frame,
                region=selection.timing.region.model_dump(),offset_samples=voices[0]['offset_samples'])
            previous=(accepted_timing or {}).get(len(segments))
            inputs['timing_runtime']=runtime_binding(service,scope,previous)
        if selection.lipsync=='latentsync':
            from .lipsync_runtime import runtime_binding
            inputs['lip_runtime']=runtime_binding()
        if rule:
            from .script import phrase_line
            candidates=[v['line_id'] for v in voices]
            if guided and selection.audio=='source':candidates.append(old['voice']['line']['id'])
            if any(l['id'] in candidates and phrase_line(rule,l) for l in project['script']['lines']):
                matching_segments.append(len(segments))
        segments.append(dict(inputs=inputs,input_hash=fingerprint(inputs),output_start_frame=output_frame))
        output_frame+=frames
    if rule and not matching_segments:
        raise Problem(422,'REQUIRED_PHRASE_MISSING','Incluye una toma del personaje que pronuncie la frase obligatoria.')
    if output_frame>24*120:raise Problem(422,'MONTAGE_DURATION','El montaje piloto admite hasta 120 segundos.')
    result=dict(version='parody-montage-1',segments=segments,frames=output_frame,fps=24,resolution=dimensions,
        warnings=['Revisión de cada toma y cada voz obligatoria antes de ejecutar.',
          'Reemplazar audio silencia toda la pista original del segmento, incluido ambiente y otros hablantes. No hay separación de fuentes.',
          'Lip sync es experimental y requiere una región con un solo rostro durante todo el segmento.',
          'Los cortes son directos: no se añaden transiciones, repeticiones ni cambios de velocidad.'])
    if any(isinstance(s,OriginalSegment) for s in body.segments):
        result['warnings'].append('Los planos originales conservan su mezcla de audio y se normalizan a 24 fps y la resolución elegida, con bandas si hacen falta. Se recodifican; no hay igualdad exacta de píxeles ni aumento de detalle nativo.')
    if any(isinstance(s,OriginalSegment) and s.reframe for s in body.segments):
        result['warnings'].append('Revisa todo el intervalo del reencuadre: es fijo, elimina zonas del original y puede reducir detalle. No realiza seguimiento automático.')
    if rule:
        result['required_phrase']={**rule,'segments':matching_segments}
        result['warnings'].append('Escucha la frase obligatoria tras los recortes y revisa la actuación antes de aprobar el vídeo.')
    return result
