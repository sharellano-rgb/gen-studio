"""CPU-only evidence with original PTS and full-frame sampling. Never edits source."""
import asyncio
import hashlib
import json
import math
import shutil
import subprocess
from bisect import bisect_left
from ..scene_detection import parse_metrics, detect_cuts


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()


async def command(args, timeout=180):
    process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        out, err = await asyncio.wait_for(process.communicate(), timeout)
    except BaseException:
        if process.returncode is None: process.kill()
        await process.communicate()
        raise
    if process.returncode: raise ValueError('No se pudo decodificar la evidencia de vídeo: '+err.decode(errors='replace')[-350:])
    return out, err


def windows(duration_ms, cuts_ms, timestamps):
    bounds = [0, *sorted(set(c for c in cuts_ms if 0 < c < duration_ms)), duration_ms]
    result = []
    for shot, (begin, end) in enumerate(zip(bounds, bounds[1:])):
        for start in range(begin, end, 5000):
            stop = min(start+5000, end)
            indices = [i for i, t in enumerate(timestamps) if start <= t < stop]
            if not indices: raise ValueError('Un intervalo no tiene evidencia decodificable.')
            selected = sorted(set((indices[0], indices[len(indices)//2], indices[-1])))
            result.append(dict(index=len(result), shot=shot, start_ms=start, end_ms=stop,
                               samples=[{'frame_index':i, 'time_ms':timestamps[i]} for i in selected]))
    if len(result) > 96: raise ValueError('Demasiados planos para este análisis. Usa una escena más corta; no se omite evidencia silenciosamente.')
    return result


async def prepare(source, folder, expected, stopped):
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe: raise ValueError('Faltan FFmpeg o FFprobe.')
    if await asyncio.to_thread(sha256, source) != expected['sha256']: raise ValueError('Cambió el vídeo fuente.')
    raw, _ = await command([ffprobe, '-v','error','-show_streams','-show_format','-of','json',str(source)])
    info = json.loads(raw); video = next(s for s in info['streams'] if s['codec_type']=='video')
    duration = float(info['format']['duration'])
    if not math.isfinite(duration) or not 0 < duration <= 120: raise ValueError('La escena debe durar entre 0 y 120 segundos.')
    duration_ms = round(duration*1000)
    if abs(duration_ms-expected['duration_ms']) > 100: raise ValueError('La duración no coincide con la fuente guardada.')
    raw, _ = await command([ffprobe,'-v','error','-select_streams','v:0','-show_frames',
        '-show_entries','frame=best_effort_timestamp_time','-of','json',str(source)])
    pts = [float(f['best_effort_timestamp_time']) for f in json.loads(raw)['frames']]
    if not pts or len(pts)>30000 or any(not math.isfinite(t) for t in pts) or any(b<a for a,b in zip(pts,pts[1:])):
        raise ValueError('La línea de tiempo de la fuente no es compatible.')
    timestamps = [round((t-pts[0])*1000) for t in pts]
    stopped()
    _, log = await command([ffmpeg,'-hide_banner','-nostdin','-i',str(source),'-an','-vf',
        'scale=320:-2,scdet=t=0,metadata=print','-f','null','-'])
    detection = detect_cuts(parse_metrics(log), len(pts))
    # Native decoded frame indices map to the same original PTS used by samples.
    # Do not mix a resampled 24 fps detector with the original frame timeline.
    cuts = [timestamps[c] for c in detection['cuts']]
    plan = dict(duration_ms=duration_ms, pts_origin_seconds=pts[0], time_base=video.get('time_base'),
                avg_frame_rate=video.get('avg_frame_rate'), r_frame_rate=video.get('r_frame_rate'),
                frame_count=len(pts), audio_present=any(s['codec_type']=='audio' for s in info['streams']),
                cut_timeline='original-decoded-frame-pts', cuts_ms=cuts, detector=detection,
                windows=windows(duration_ms,cuts,timestamps), full_frame=True,
                limitations=['Cortes candidatos en fotogramas originales; confirmar cortes y fundidos.',
                            'Muestreo visual: no es seguimiento por fotograma ni transcripción de audio.'])
    folder.mkdir(parents=True,exist_ok=True)
    for window in plan['windows']:
        for sample in window['samples']:
            stopped()
            path = folder / f"frame-{sample['frame_index']:06d}.jpg"
            await command([ffmpeg,'-v','error','-nostdin','-y','-i',str(source),'-an','-vf',
                f"select=eq(n\\,{sample['frame_index']}),scale=768:768:force_original_aspect_ratio=decrease:force_divisible_by=2",
                '-frames:v','1','-q:v','3',str(path)],timeout=60)
            if not path.is_file(): raise ValueError('Falta un fotograma de evidencia.')
            sample.update(filename=path.name, sha256=await asyncio.to_thread(sha256,path))
    if await asyncio.to_thread(sha256,source) != expected['sha256']: raise ValueError('La fuente cambió durante el análisis.')
    return plan
