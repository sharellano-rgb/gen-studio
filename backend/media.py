import asyncio
import hashlib
import json
import shutil
import subprocess
import uuid
from pathlib import Path


def probe(path):
    executable = shutil.which('ffprobe')
    if not executable:
        raise ValueError('ffprobe no está disponible. No se puede validar la carga.')
    response = subprocess.run([executable, '-v', 'error', '-show_entries',
                               'format=duration:stream=codec_type,width,height,codec_name:stream_tags=rotate:stream_side_data=rotation', '-of', 'json', str(path)],
                              capture_output=True, text=True, timeout=30,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if response.returncode:
        raise ValueError('Archivo de vídeo/imagen inválido o no compatible.')
    data = json.loads(response.stdout)
    video = next((s for s in data.get('streams', []) if s.get('codec_type') == 'video'), None)
    if not video or not 0 < video.get('width', 0) <= 8192 or not 0 < video.get('height', 0) <= 8192:
        raise ValueError('No hay un vídeo/imagen válido de hasta 8192 píxeles.')
    rotation=float(next((s['rotation'] for s in video.get('side_data_list',[]) if 'rotation' in s),video.get('tags',{}).get('rotate',0)))
    width,height=video['width'],video['height']
    if round(rotation)%180==90:width,height=height,width
    return {'width': width, 'height': height, 'display_width':width,'display_height':height,
            'encoded_width':video['width'],'encoded_height':video['height'],'rotation':rotation,
            'duration': float(data.get('format', {}).get('duration', 0)),
            'audio': any(s.get('codec_type') == 'audio' for s in data.get('streams', [])),
            'codec': video.get('codec_name')}


def source_resolution(video, resolution, match_aspect=False):
    width=video.get('display_width',video.get('width',0));height=video.get('display_height',video.get('height',0))
    if not width or not height:return resolution
    w,h=map(int,resolution.split('x'))
    if match_aspect and (4/7)**0.5 <= width/height <= (7/4)**0.5:
        return '1024x1024' if max(w,h)>=1000 else '512x512'
    if width==height:return '512x512'
    if resolution in ('640x1152','1152x640'):return '640x1152' if height>width else '1152x640'
    if height>width:return resolution if h>w else '768x1344' if max(w,h)>=1000 else '480x864'
    return resolution if w>h else '1344x768' if max(w,h)>=1000 else '864x480'


def fit_filter(width,height):
    return f'scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1'


AUDIO_FORMATS = {'.wav': 'wav', '.mp3': 'mp3', '.m4a': 'mov', '.flac': 'flac',
                 '.ogg': 'ogg', '.opus': 'ogg', '.aac': 'aac'}


def probe_voice_audio(path):
    executable = shutil.which('ffprobe')
    if not executable:
        raise ValueError('FFprobe no está disponible para verificar el audio.')
    response = subprocess.run([executable, '-v', 'error', '-protocol_whitelist', 'file',
                               '-format_whitelist', AUDIO_FORMATS[path.suffix],
                               '-show_entries', 'format=duration:stream=codec_type,codec_name:stream_disposition=attached_pic',
                               '-of', 'json', str(path)], capture_output=True, text=True, timeout=30)
    if response.returncode:
        raise ValueError('El contenido no coincide con el formato de audio seleccionado.')
    data = json.loads(response.stdout)
    streams = data.get('streams', [])
    audio = next((s for s in streams if s.get('codec_type') == 'audio'), None)
    if not audio or any(s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic') for s in streams):
        raise ValueError('Selecciona un archivo de audio o sube el vídeo con su extensión original.')
    duration = float(data.get('format', {}).get('duration', 0))
    if not 3 <= duration < float('inf'):
        raise ValueError('La referencia de voz debe contener al menos 3 segundos de audio.')
    return {'duration': duration, 'codec': audio['codec_name'], 'audio': True}


async def save_upload(upload, root, store, *, voice_audio=False):
    extension = Path(upload.filename or '').suffix.lower()
    kind = 'video' if extension in {'.mp4', '.mov', '.webm'} else 'image' if extension in {'.png', '.jpg', '.jpeg', '.webp'} else None
    if voice_audio:
        if extension not in AUDIO_FORMATS:
            raise ValueError('Usa WAV, MP3, M4A, FLAC, OGG, Opus o AAC.')
        kind = 'audio'
    if not kind:
        raise ValueError('Usa MP4, MOV, WebM, PNG, JPG o WebP.')
    asset_id = str(uuid.uuid4())
    folder = root / 'inputs'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (asset_id + extension)
    limit = (200 if kind == 'video' else 25) * 1024 ** 2
    size = 0
    digest = hashlib.sha256()
    try:
        with target.open('xb') as stream:
            while chunk := await upload.read(1024 ** 2):
                size += len(chunk)
                if size > limit:
                    raise ValueError(f'La carga supera {limit // 1024 ** 2} MB.')
                stream.write(chunk)
                digest.update(chunk)
        metadata = await asyncio.to_thread(probe_voice_audio if voice_audio else probe, target)
        if kind == 'video' and metadata['duration'] <= 0:
            raise ValueError('No se puede determinar la duración del vídeo.')
        return store.add_asset({'id': asset_id, 'kind': kind, 'name': Path(upload.filename).name,
                                'filename': target.name, 'bytes': size, 'sha256': digest.hexdigest(), **metadata})
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()
