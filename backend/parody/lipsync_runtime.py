"""Read-only reuse of the installed LatentSync runtime in an owned subprocess."""
import asyncio
import json
import os
import hashlib
from pathlib import Path
from .analysis import release_for_qwen
from .montage_runtime import command
from .voice_runtime import atomic_json

ROOT=Path(Path.home()/'.gen-studio'/'media-engines')
PYTHON=Path('D:/AI/apps/ComfyUI-ComfyField/.venv/Scripts/python.exe')
WORKER=Path(__file__).with_name('lipsync_worker.py')

def runtime_binding():
    if not availability()['available']:raise ValueError('El entorno local de lip sync no está completo.')
    code=[WORKER,*sorted((ROOT/'lipsync/latentsync').rglob('*.py')),*sorted((ROOT/'lipsync/configs').rglob('*.yaml')),ROOT/'lipsync/latentsync/utils/mask.png']
    weights=[ROOT/'models/latentsync/latentsync_unet.pt',ROOT/'models/latentsync/whisper/tiny.pt',ROOT/'models/sd-vae/diffusion_pytorch_model.safetensors',ROOT/'lipsync/latentsync/weights/face_landmarker.task']
    value=dict(code=[dict(name=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in code],
        weights=[dict(name=str(p),size=p.stat().st_size,mtime_ns=p.stat().st_mtime_ns) for p in weights])
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def availability():
    paths=[PYTHON,ROOT/'lipsync/latentsync/pipelines/lipsync_pipeline.py',ROOT/'models/latentsync/latentsync_unet.pt',
           ROOT/'models/latentsync/whisper/tiny.pt',ROOT/'lipsync/latentsync/weights/face_landmarker.task',
           ROOT/'models/sd-vae/diffusion_pytorch_model.safetensors']
    return dict(available=all(p.is_file() for p in paths),model='LatentSync 1.6 · experimental',
        single_face_region=True,commercial_license_review_required=True,downloads=False)


async def synchronize(service,inputs,folder,video,audio,stopped):
    if not availability()['available']:raise ValueError('Faltan componentes del lip sync local; no se descargan automáticamente.')
    if inputs.get('lip_runtime') and inputs['lip_runtime']!=runtime_binding():raise ValueError('Cambió el runtime de lip sync; prepara otro plan.')
    # The same internal lease used by Parody is held by the caller. Never unload Ollama sessions belonging to others.
    await release_for_qwen(service);stopped()
    raw=await command(['nvidia-smi','--query-gpu=memory.free','--format=csv,noheader,nounits'],stopped,timeout=15)
    if int(raw.decode().strip().splitlines()[0])<21000:raise ValueError('Lip sync necesita 21000 MiB libres para esta prueba conservadora.')
    request=dict(video=str(video.resolve()),audio=str(audio.resolve()),frames=inputs['frames'],width=inputs['width'],height=inputs['height'],
                 fps=24,region=inputs['selection']['region'],seed=42)
    atomic_json(folder/'lip-request.json',request)
    lease=folder/'lip-lease';lease.write_text('owned',encoding='utf-8')
    await command([PYTHON,'-B',WORKER,folder.resolve()],stopped,timeout=600,log=folder/'lip-worker.log',lease=lease)
    proof=json.loads((folder/'lip-proof.json').read_text())
    if proof['outside_region_max_difference']!=0 or proof['frames']!=inputs['frames']:raise ValueError('Lip sync no conservó la región protegida o los fotogramas.')
    return folder/'lip.mp4'
