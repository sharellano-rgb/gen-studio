"""Versioned, private H3 speech plans. Native H3 code/workflows stay unchanged."""
import copy
import hashlib
import wave
from .repository import Problem, encode
from .voice_store import VoiceStore
from .script import line_binding
from .analysis_media import sha256
from .. import qwen_runtime as qr

VERSION='parody-h3-speech-1'
def is_speech(plan):return plan.get('version') in (VERSION,'parody-pika-ref2va-1')
def fingerprint(value):return hashlib.sha256(encode(value).encode()).hexdigest()


def voice_path(service,ident,expected):
    path=VoiceStore(service.root).directory(ident)/'preview.wav'
    if not path.is_file() or sha256(path)!=expected:raise Problem(409,'VOICE_CHANGED','Cambió la voz preparada.')
    return path


def compile_speech(service,project,body,base,accepted=None):
    if service.settings.get('parody_speech_enabled') is not True:
        raise Problem(503,'SPEECH_DISABLED','H3 guiado por voz no está habilitado.')
    if body.performance!='new' or not body.voice_reviewed:
        raise Problem(422,'VOICE_REVIEW','Escucha la voz y confirma la actuación nueva para este plano.')
    if body.preset!='hands' or len(body.reference_ids)!=1 or base['timing']['frames']>96:
        raise Problem(422,'SPEECH_PILOT_LIMIT','Este flujo validado admite Realismo Manos, una referencia visual y hasta cuatro segundos por plano.')
    voice=VoiceStore(service.root).read(project['id'],str(body.voice_preview_id))
    binding,digest=line_binding(project,voice['line_id']);line=binding['line']
    if voice['status']!='review_pending' or not voice.get('artifact') or digest!=voice['input_hash']:
        raise Problem(409,'VOICE_STALE','Regenera la frase modificada antes de guiar H3.')
    if line['kind']!='speech' or line['shot_id']!=base['shot']['id'] or line['character_id']!=base['character_id']:
        raise Problem(422,'VOICE_ASSIGNMENT','La voz debe pertenecer al personaje y plano seleccionados.')
    path=voice_path(service,voice['id'],voice['artifact']['sha256'])
    with wave.open(str(path),'rb') as wav:
        if wav.getframerate()!=24000 or wav.getnchannels()!=1 or wav.getsampwidth()!=2:raise ValueError('Formato de voz no compatible.')
        samples=wav.getnframes()*2
    offset=round(line['start_ms']*48-base['timing']['start_frame']*2000)
    if samples<=0 or offset<0 or offset+samples>base['timing']['frames']*2000 or line['end_ms']>base['timing']['end_ms']+.01:
        raise Problem(422,'VOICE_FIT','La voz y sus pausas deben caber completas; no se recorta ni acelera.')
    model=qr.checked_binding(accepted[qr.FIELD]) if accepted else qr.selected_binding(service)
    from ..direct_character import graph
    payload=copy.deepcopy(base['native_payload']);payload['resolution']=base['resolution'];payload['prompt']='PARODY_SPEECH_PENDING'
    recipe=graph(payload,'parody-source.mp4',['parody-reference.png'],'parody-speech',audio=False)
    conditioning=[n for n in recipe.values() if n['class_type']=='MiniMaxH3ReferenceToVideo']
    if len(conditioning)!=1:raise ValueError('Cambió la receta H3; requiere validación.')
    # Same explicit internal guide padding as the accepted 2 s / 4 s experiments.
    internal=base['timing']['frames']+8;conditioning[0]['inputs']['length']=internal
    result=copy.deepcopy(base)
    from .render import request_payload
    result.update(version=VERSION,request=request_payload(body),audio='prepared_voice',performance='new_audio_guided',
        voice=dict(id=voice['id'],input_hash=digest,sha256=voice['artifact']['sha256'],samples_48k=samples,
                   offset_samples=offset,line=copy.deepcopy(line)),
        speech=dict(internal_frames=internal,recipe=recipe,recipe_sha256=fingerprint(recipe),**{qr.FIELD:model}),
        warnings=['H3 recibe la voz preparada como guía; puede crear una actuación nueva. Requiere revisión de boca, identidad y escena.',
                  'Una voz y una referencia por plano; no se conserva el ambiente original ni se clona otra voz.',
                  'El vídeo queda pendiente de aprobación humana antes de incluirlo en un montaje.',
                  'No se aplica un desfase universal. El montaje conserva la voz o permite revisar su ajuste temporal.'])
    # Historical plans retain absence of this policy and their original direction contract.
    if accepted is None:result['speech']['director_policy']='visible-character-speech-tail-3'
    elif 'director_policy' in accepted:result['speech']['director_policy']=accepted['director_policy']
    return result


def result_path(service,record):
    from .render import RenderStore
    meta=record.get('artifact')
    if not meta:raise Problem(409,'RENDER_PENDING','Todavía no hay un resultado verificado.')
    root=RenderStore(service.root).directory(record['id']);path=(root/meta['file']).resolve()
    if not path.is_relative_to(root) or not path.is_file() or sha256(path)!=meta['sha256']:
        raise Problem(409,'RENDER_CHANGED','Cambió el resultado del plano guiado por voz.')
    return path


def status(service,record):
    value=dict(record);task=getattr(service,'parody_speech_tasks',{}).get(record['id'])
    if value['status'] in ('running','cancelling') and (task is None or task.done()):
        value.update(status='interrupted',stage='Trabajo interrumpido; recupera este mismo envío, sin generar otra copia')
    value['job']=dict(error=value.get('error'),result_available=bool(value.get('artifact')),progress=None)
    return value
