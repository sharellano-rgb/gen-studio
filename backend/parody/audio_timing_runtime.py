"""Isolated primitives for the timing pilot; not registered as production routes.

The caller must own the shared analysis lease and supply hash-bound private files.
No models are installed, no visual frames are regenerated, no automatic fallback.
"""
import asyncio
import json
from pathlib import Path
from .. import qwen_runtime as qr
from ..prompt_enhancer import unload
from .analysis import release_for_qwen
from .analysis_media import sha256
from .audio_timing import qwen_payload, validate_decision
from .montage_runtime import command, inspect, CleanupFailed


@qr.bound
async def consult_qwen(service, record, measurement, policy, folder, stopped):
    # Outer job runner retains/relinquishes the lease, including on cleanup failure.
    if not service.enhancer_active:
        raise ValueError('Falta el turno de análisis reservado.')
    folder = Path(folder).resolve()
    if not folder.is_relative_to(service.root.resolve()):
        raise ValueError('La evidencia debe permanecer en el espacio privado.')
    folder.mkdir(parents=True, exist_ok=False)
    requested = False
    try:
        stopped()
        await release_for_qwen(service)
        stopped()
        payload = qwen_payload(measurement, policy)
        (folder/'request.json').write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        requested = True
        response = await qr.chat(service, json=payload, timeout=180)
        response.raise_for_status()
        data = response.json()
        (folder/'response.json').write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        stopped()
        if data.get('done_reason') == 'length':
            raise ValueError('Decisión Qwen truncada; revisión necesaria.')
        return validate_decision(data.get('message', {}).get('content', ''), measurement, policy)
    finally:
        if requested:
            try:
                await asyncio.shield(unload(service))
            except BaseException as error:
                raise CleanupFailed('No se confirmó la descarga de Qwen; retener el turno.') from error


async def video_hash(path, stopped):
    raw = await command(['ffmpeg', '-v', 'error', '-nostdin', '-i', path, '-map', '0:v:0',
                         '-c:v', 'copy', '-f', 'hash', '-hash', 'sha256', '-'], stopped)
    return raw.decode().strip()


async def shift_voice(video, voice, folder, *, video_sha256, voice_sha256, frames,
                      offset_samples, stopped):
    """Apply an absolute voice start in 48 kHz samples, never clip the input WAV.

Works for an already prepared single shot at 24 fps. The caller decides whether
the offset is human-approved or passes automatic measurement/decision gates.
This function alone NEVER certifies lip-sync quality.
"""
    video, voice, folder = Path(video), Path(voice), Path(folder)
    if type(offset_samples) is not int or offset_samples < 0:
        raise ValueError('El inicio de la voz debe ser una muestra no negativa.')
    if type(frames) is not int or not 1 <= frames <= 2880:
        raise ValueError('Duración fuera del piloto.')
    if sha256(video) != video_sha256 or sha256(voice) != voice_sha256:
        raise ValueError('Cambió una entrada del ajuste.')
    source = json.loads(await command(['ffprobe', '-v', 'error', '-count_frames', '-show_streams', '-of', 'json', video], stopped))
    streams = [s for s in source['streams'] if s['codec_type'] == 'video']
    if len(streams) != 1:
        raise ValueError('Selecciona un vídeo con una sola pista visual.')
    stream = streams[0]
    if (stream['r_frame_rate'], stream['avg_frame_rate'], int(stream['nb_read_frames'])) != ('24/1', '24/1', frames):
        raise ValueError('El plano debe estar preparado a 24 fps constantes.')
    if stream.get('codec_name') != 'h264':
        raise ValueError('Este piloto conserva únicamente el bitstream H.264 existente.')
    folder.mkdir(parents=True, exist_ok=False)
    pcm = folder/'voice.wav'
    await command(['ffmpeg', '-v', 'error', '-nostdin', '-n', '-i', voice, '-vn', '-ar', '48000',
                   '-ac', '2', '-c:a', 'pcm_s16le', pcm], stopped)
    import wave
    with wave.open(str(pcm), 'rb') as wav:
        samples = wav.getnframes()
    if offset_samples+samples > frames*2000:
        raise ValueError('El ajuste cortaría la frase o sus pausas; revisión necesaria.')
    stopped()
    # Inputs may change during extraction; do not authorize a changed input.
    if sha256(video) != video_sha256 or sha256(voice) != voice_sha256:
        raise ValueError('Cambió una entrada durante el ajuste.')
    original_video_hash = await video_hash(video, stopped)
    output = folder/'adjusted.mp4'
    await command(['ffmpeg', '-v', 'error', '-nostdin', '-n', '-i', video, '-i', pcm,
                   '-filter_complex', f'[1:a]adelay={offset_samples}S:all=1,apad,atrim=end_sample={frames*2000}[a]',
                   '-map', '0:v:0', '-map', '[a]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
                   '-t', frames/24, '-movflags', '+faststart', '-metadata',
                   'comment=GEN STUDIO - PARODIA SINTETICA - TIMING REVIEW', output], stopped)
    meta = await inspect(output, frames, stream['width'], stream['height'], stopped)
    result_video_hash = await video_hash(output, stopped)
    if original_video_hash != result_video_hash:
        raise ValueError('El ajuste alteró el bitstream visual.')
    stopped()
    meta.update(file=output.name, video_sha256=video_sha256, voice_sha256=voice_sha256,
                voice_samples=samples, offset_samples=offset_samples, sample_rate=48000,
                video_bitstream_hash=result_video_hash, video_bitstream_unchanged=True,
                review_required=True, lip_sync_validated=False)
    (folder/'receipt.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
    return meta
