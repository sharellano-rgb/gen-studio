"""Read-only, on-demand CPU analysis of the exact H3 segment open in the review sheet."""
import asyncio
from collections import OrderedDict
import json
import os
from pathlib import Path
import subprocess
from uuid import UUID

from fastapi import HTTPException, Query

from .guided_composition import local_path
from .head import digest
from .process_cleanup import stop_process_tree


WORKER = Path(__file__).resolve().parents[1] / 'scripts' / 'segment_background_review.py'


def eligible(job):
    p = job.get('parameters') or {}
    return (p.get('engine') == 'h3_direct' and p.get('sequence_parent_id')
            and not p.get('operation') and (p.get('direct_edit_policy') or {}).get('background') == 'source')


def inputs(service, job_id, expected):
    from .video_sequences import verified_rejected_preview

    job = service.store.job(job_id)
    if not job or not eligible(job):
        raise HTTPException(404, 'Este tramo no tiene revisión de fondo original.')
    folder = local_path(service.root, service.root / 'jobs' / job_id)
    if job.get('status') == 'completed' and job.get('result_available'):
        clip = local_path(service.root / 'results', service.root / 'results' / (job_id + '.mp4'))
        if expected != job.get('direct_result_sha256') or not clip.is_file() or digest(clip) != expected:
            raise HTTPException(409, 'El vídeo cambió. Vuelve a abrir el tramo.')
    else:
        clip = verified_rejected_preview(service,job,expected)
    try:
        prepared = json.loads(local_path(folder,folder/'direct-prepare.json').read_text(encoding='utf-8'))
        name = prepared.get('h3_video') or prepared['video']
        source = local_path(folder,name)
        source_hash = prepared['files'][name]
        p = job['parameters']
        start, keep = p['sequence_context'], p['sequence_keep']
        size = [int(n) for n in p['resolution'].split('x')]
        if (type(start) is not int or type(keep) is not int or not 0 <= start < start+keep <= 256
                or len(size) != 2 or min(size) <= 0 or not source.is_file() or digest(source) != source_hash):
            raise ValueError('Invalid recorded input')
    except (OSError, ValueError, TypeError, KeyError) as err:
        raise HTTPException(409, 'No se pudo verificar la fuente y el intervalo de este tramo.') from err
    key = (str(service.root), job_id, expected, source_hash, start, keep, *size)
    return key, {'source':str(source), 'candidate':str(clip), 'interval':[start,start+keep], 'size':size}


async def analyze(request):
    python = os.environ.get('GEN_STUDIO_MATTE_PYTHON','D:/AI/apps/ComfyUI-portable/python_embeded/python.exe')
    try:
        process = await asyncio.create_subprocess_exec(python, '-B', str(WORKER), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            data,_ = await asyncio.wait_for(process.communicate(json.dumps(request).encode()),75)
        finally:
            if process.returncode is None:
                await stop_process_tree(process)
        if process.returncode:
            raise ValueError('Worker unavailable')
        return json.loads(data)
    except (OSError, ValueError, TimeoutError) as err:
        raise HTTPException(503, 'No se pudo medir el fondo de este vídeo.') from err


def register(app):
    lock = asyncio.Lock()
    cache = OrderedDict()

    @app.post('/api/jobs/{job_id}/segment-background-review')
    async def background_review(job_id:UUID, sha256:str=Query(pattern=r'^[0-9a-f]{64}$')):
        service = app.state.jobs.current
        # The lock belongs only to this advisory; approval and generation use their own paths.
        try:
            await asyncio.wait_for(lock.acquire(),timeout=15)
        except TimeoutError as err:
            raise HTTPException(503,'Hay otra revisión de fondo en curso. Inténtalo de nuevo.') from err
        try:
            key,request = await asyncio.to_thread(inputs,service,str(job_id),sha256)
            if key in cache:
                cache.move_to_end(key)
                return cache[key]
            report = await analyze(request)
            final_key,_ = await asyncio.to_thread(inputs,service,str(job_id),sha256)
            if final_key != key:
                raise HTTPException(409,'El tramo cambió durante el análisis.')
            result = {**report,'sha256':sha256}
            cache[key] = result
            if len(cache)>128:
                cache.popitem(last=False)
            return result
        finally:
            lock.release()
