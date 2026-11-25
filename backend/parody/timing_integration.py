"""Versioned research-only timing adapter for the private montage queue."""
import asyncio
import json
import wave
from functools import lru_cache
from pathlib import Path
from .. import qwen_runtime as qr
from .analysis_media import sha256
from .audio_timing import Measurement, Policy, assess, verify_residual, validate_decision
from .repository import Problem

REGISTRY=Path(__file__).with_name('audio_timing_installation.json')


def fingerprint(value):
    import hashlib
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


@lru_cache(maxsize=64)
def checked_hash(path,size,mtime):
    return sha256(Path(path))


def installation():
    value=json.loads(REGISTRY.read_text(encoding='utf-8'))
    if value.get('version')!='parody-syncnet-runtime-2' or value.get('research_only') is not True:
        raise ValueError('El evaluador no tiene un registro de investigación válido.')
    for item in value['files']:
        p=Path(item['path']);s=p.stat()
        if checked_hash(str(p),s.st_size,s.st_mtime_ns)!=item['sha256']:
            raise ValueError('Cambió un componente del evaluador; requiere nueva validación.')
    return value,fingerprint(value)


def availability(settings):
    enabled=settings.get('parody_timing_enabled') is True
    automatic=enabled and settings.get('parody_timing_research_enabled') is True
    try:
        _,digest=installation();available=True;reason=''
    except (OSError,KeyError,ValueError):
        available=False;digest=None;reason='El evaluador local falta o cambió y necesita validación.'
    return dict(enabled=enabled,automatic=automatic and available,available=available,
                research_only=True,commercial_cleared=False,reason=reason,version='parody-audio-timing-2')


def runtime_binding(service,scope,accepted=None):
    if service.settings.get('parody_timing_enabled') is not True or service.settings.get('parody_timing_research_enabled') is not True:
        raise Problem(503,'TIMING_DISABLED','El ajuste automático experimental no está habilitado.')
    try:runtime,digest=installation()
    except (OSError,KeyError,ValueError) as error:raise Problem(503,'TIMING_UNAVAILABLE','El evaluador necesita validación local.') from error
    scope_hash=fingerprint(scope)
    if accepted is not None:
        if accepted['scope_sha256']!=scope_hash or accepted['evaluator_sha256']!=digest:
            raise Problem(409,'TIMING_CHANGED','Cambió la medición o su entorno; prepara otra versión.')
        qr.checked_binding(accepted[qr.FIELD]);Policy.model_validate(accepted['policy'])
        return accepted
    policy=Policy()
    # Calibration is server-owned and tied to exact media, selection and runtime.
    # No browser-provided threshold or generic enable-all flag is accepted.
    for item in service.settings.get('parody_timing_calibrations',[]):
        if item.get('scope_sha256')==scope_hash and item.get('evaluator_sha256')==digest:
            policy=Policy.model_validate(item['policy'])
            if policy.calibrated_evaluator_sha256!=digest:raise Problem(409,'CALIBRATION_CHANGED','La calibración no corresponde al evaluador.')
            break
    return dict(version='parody-audio-timing-2',scope_sha256=scope_hash,evaluator_sha256=digest,
                policy=policy.model_dump(),**{qr.FIELD:qr.selected_binding(service)})


class TimingReviewRequired(Exception):
    def __init__(self,report):
        super().__init__(report['message']);self.report=report


async def measure(service,inputs,video,audio,voice,folder,offset_samples,stopped):
    from .montage_runtime import command
    folder=Path(folder).resolve()
    if not folder.is_relative_to(service.root.resolve()):raise ValueError('Medición fuera del espacio privado.')
    runtime,digest=await asyncio.to_thread(installation)
    if digest!=inputs['timing_runtime']['evaluator_sha256']:raise ValueError('Cambió el entorno de la medición aceptada.')
    folder.mkdir(parents=True,exist_ok=False)
    request=dict(runtime=runtime,evaluator_sha256=digest,video=str(video),audio=str(audio),voice=str(voice),
        output=str(folder/'measurement.json'),video_sha256=sha256(video),voice_sha256=sha256(voice),
        frames=inputs['frames'],region=inputs['selection']['timing']['region'],target_confirmed=True,
        voice_samples=inputs['voices'][0]['samples_48k'],current_offset_samples=offset_samples)
    (folder/'request.json').write_text(json.dumps(request),encoding='utf-8')
    await command([runtime['python'],'-B',runtime['worker'],folder/'request.json'],stopped,timeout=180,log=folder/'worker.log')
    stopped();result=json.loads((folder/'measurement.json').read_text())
    if result.get('status')=='review':raise TimingReviewRequired(dict(message=result['reason'],reasons=['face_review'],candidate_delay_ms=None))
    measurement=Measurement.model_validate(result['measurement'])
    if (measurement.evaluator_sha256!=digest or measurement.video_sha256!=request['video_sha256']
        or measurement.voice_sha256!=inputs['voices'][0]['sha256'] or measurement.current_offset_samples!=offset_samples
        or measurement.frames!=inputs['frames'] or measurement.voice_samples!=inputs['voices'][0]['samples_48k']):
        raise ValueError('El evaluador respondió sobre otras entradas.')
    return measurement


async def apply_timing(service,inputs,folder,video,audio,stopped,progress):
    from .montage_runtime import command
    from .audio_timing_runtime import consult_qwen
    from .voice_store import VoiceStore
    selection=inputs['selection']['timing'];voice=inputs['voices'][0]
    path=VoiceStore(service.root).directory(voice['id'])/'preview.wav'
    offset=voice['offset_samples'];report=dict(mode=selection['mode'],delay_samples=0,review_required=True)
    policy=None;before=None;decision=None
    if selection['mode']=='manual':
        offset+=selection['delay_samples'];report.update(delay_samples=selection['delay_samples'],method='user_reviewed')
    else:
        progress('Midiendo desfase del plano en CPU')
        before=await measure(service,inputs,video,audio,path,folder/'timing-before',offset,stopped)
        policy=Policy.model_validate(inputs['timing_runtime']['policy']);gates=assess(before,policy)
        progress('Qwen revisa la medición del plano')
        try:
            decision=await consult_qwen(service,inputs['timing_runtime'],before,policy,folder/'timing-qwen',stopped)
            decision=validate_decision(decision.model_dump_json(),before,policy)
        except ValueError as error:
            raise TimingReviewRequired(dict(message='Qwen no entregó una decisión válida. Revisa el ajuste del plano.',reasons=['invalid_qwen_decision'],candidate_delay_ms=None)) from error
        report.update(gates=gates,decision=decision.model_dump(),measurement=before.model_dump())
        (folder/'timing-decision.json').write_text(json.dumps(report,ensure_ascii=False),encoding='utf-8')
        if decision.action=='review':
            best=min(before.candidates,key=lambda c:c.distance) if before.candidates else None
            raise TimingReviewRequired(dict(message='Este plano necesita revisión antes de ajustar la voz.',reasons=gates['reasons'] or ['qwen_review'],
                explanation=decision.explanation,candidate_delay_ms=best.delay_samples/48 if best else None))
        candidate=next(c for c in before.candidates if c.id==decision.candidate_id)
        offset+=candidate.delay_samples;report.update(delay_samples=candidate.delay_samples,method='measured_qwen')
    if offset<0 or offset+voice['samples_48k']>inputs['frames']*2000:
        raise ValueError('El ajuste cortaría la voz; revisa sus tiempos.')
    if sha256(path)!=voice['sha256']:raise ValueError('Cambió la voz antes de ajustarla.')
    adjusted=folder/'timed-audio.wav'
    await command(['ffmpeg','-v','error','-nostdin','-n','-i',path,'-vn','-af',
        f'aresample=48000,adelay={offset}S:all=1,apad,atrim=end_sample={inputs["frames"]*2000}',
        '-ar','48000','-ac','2','-c:a','pcm_s16le',adjusted],stopped)
    with wave.open(str(adjusted),'rb') as wav:
        if wav.getnframes()!=inputs['frames']*2000:raise ValueError('Duración PCM incorrecta.')
    # Caller retains original PCM as evidence and adopts this only after validation.
    return adjusted,report,(before,policy,decision,offset)


async def postcheck(service,inputs,folder,video,report,context,stopped,progress):
    before,policy,decision,offset=context
    if before is None:return report
    from .voice_store import VoiceStore
    voice=VoiceStore(service.root).directory(inputs['voices'][0]['id'])/'preview.wav'
    progress('Comprobando sincronización del archivo entregable')
    after=await measure(service,inputs,video,video,voice,folder/'timing-after',offset,stopped)
    valid=verify_residual(before,after,policy,output_video_sha256=sha256(video),decision=decision)
    report.update(post_measurement=after.model_dump(),residual_verified=valid)
    (folder/'timing-result.json').write_text(json.dumps(report,ensure_ascii=False),encoding='utf-8')
    if not valid:raise TimingReviewRequired(dict(message='La comprobación posterior no confirma un desfase estable. Revisa este plano.',reasons=['residual_failed'],candidate_delay_ms=None))
    best=min(after.candidates,key=lambda c:c.distance)
    report['residual_ms']=best.delay_samples/48
    return report
