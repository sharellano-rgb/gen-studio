"""Workspace-scoped acoustic observations, reusing the installed CPU worker."""
import asyncio
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException
from .repository import Repository


def register(router,service,run,source_reference):
    lock = asyncio.Lock()

    @router.post('/projects/{project_id}/acting-audio/{reference_id}')
    async def analyze(project_id: UUID, reference_id: UUID):
        current = service()
        project = run(lambda: Repository(current.root).read(str(project_id)))
        ref = next((r for c in project['cast']['characters'] for r in c['references']
                    if r['id']==str(reference_id) and r['role']=='acting'), None)
        if not ref:
            raise HTTPException(404,'Clip de actuación no encontrado.')
        source = await asyncio.to_thread(run,lambda: source_reference(current,ref['asset_id']))
        if ref['sha256']!=source['sha256']:
            raise HTTPException(409,'La referencia cambió. Revisa la ficha.')
        folder = current.root/'parody'/'acting-audio'
        cache = folder/(source['sha256']+'-v2.json')
        if cache.exists():
            return json.loads(cache.read_text('utf8'))
        if project['archived'] or lock.locked():
            raise HTTPException(409,'Abre un proyecto activo y espera a que termine el análisis actual.')
        async with lock:
            python = Path(os.environ.get('GEN_STUDIO_ACTING_ASR_PYTHON','D:/AI/apps/ComfyUI-ComfyField/.venv/Scripts/python.exe'))
            if not python.is_file():
                raise HTTPException(503,'El entorno local de análisis de audio no está disponible.')
            filename=current.store.asset(ref['asset_id'])['filename']
            proc=await asyncio.create_subprocess_exec(str(python),'-B','-X','utf8',str(Path(__file__).with_name('acting_audio_worker.py')),
                str(current.root/'inputs'/filename),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
                env=dict(os.environ,CUDA_VISIBLE_DEVICES='-1',HF_HUB_OFFLINE='1'))
            try:
                stdout,stderr=await asyncio.wait_for(proc.communicate(),timeout=300)
            except asyncio.TimeoutError:
                raise HTTPException(504,'El análisis superó cinco minutos.')
            finally:
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()
            if proc.returncode:
                message=stderr.decode('utf8',errors='replace').strip().splitlines()
                raise HTTPException(422,message[-1] if proc.returncode==2 and message else 'Falló el análisis local del audio.')
            result={'sha256':source['sha256'],'segments':json.loads(stdout)}
            folder.mkdir(parents=True,exist_ok=True)
            tmp=folder/(uuid4().hex+'.tmp')
            tmp.write_text(json.dumps(result),'utf8')
            tmp.replace(cache)
            return result
