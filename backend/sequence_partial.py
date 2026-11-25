"""Read-only preview of a contiguous sequence prefix, including unapproved renders."""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import UUID, uuid5, NAMESPACE_URL

from fastapi import HTTPException
from fastapi.responses import FileResponse
from . import video_sequences as seq
from .continuity import VERSION as CONTEXT
from .sequence_plan import FAITHFUL_VERSION, VIGGLE_VERSION, window_size

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/long_video_media.py'


def assemble(service,parent_id,index,attempt):
    parent=service.store.job(parent_id)
    if not parent or not parent.get('video_sequence_version'):
        raise HTTPException(404,'Secuencia no encontrada.')
    plan=seq.load(service,parent,False)
    if not plan or not 0<=index<len(plan['segments']):
        raise HTTPException(404,'Tramo no encontrado.')
    parts=[]
    originals={edit['index'] for edit in parent['parameters'].get('direct_shot_edits',[]) if edit.get('passthrough')}
    for segment in plan['segments'][:index+1]:
        n=segment['index']
        if segment['shot'] in originals:
            path=seq.file_checked(service,segment['path'],segment['sha256'])
            sha=segment['sha256']
        else:
            ident=attempt if n==index and attempt else seq.child_id(parent,n)
            child=service.store.job(ident)
            if not child or child['parameters'].get('sequence_parent_id')!=parent_id or child['parameters'].get('sequence_index')!=n:
                raise HTTPException(409,f'Falta el vídeo del tramo {n+1}.')
            if child.get('result_available') and child['status']=='completed':
                path=seq.local_path(service.root,service.root/'results'/(ident+'.mp4'))
                if not path.is_file():raise HTTPException(409,f'Falta el resultado del tramo {n+1}.')
            elif seq.rejected_preview_url(service,child):
                path=seq.verified_rejected_preview(service,child)
            else:
                raise HTTPException(409,f'El tramo {n+1} todavía no tiene vídeo.')
            sha=seq.digest(path)
            expected=child.get('direct_result_sha256') if child.get('result_available') else None
            if expected and expected!=sha:raise ValueError('Cambió un resultado aprobado.')
        parts.append(dict(path=str(path),sha256=sha,keep=segment['keep'],context_frames=segment['context_frames']))
    count=sum(p['keep'] for p in parts)
    version=plan['version']
    manifest=dict(mode='join',frames=count,width=plan['width'],height=plan['height'],segments=parts,
        chunk_frames=window_size(version),continuity_version=version if version in (FAITHFUL_VERSION,VIGGLE_VERSION) else CONTEXT,
        audio_source=str(seq.local_path(service.root,plan['normalized'])),audio_source_sha256=plan['normalized_sha256'])
    key=str(uuid5(NAMESPACE_URL,json.dumps(manifest,sort_keys=True)))
    folder=service.root/'jobs'/parent_id/'partial-previews'
    folder.mkdir(exist_ok=True)
    output=folder/(key+'.mp4')
    if not output.is_file():
        with tempfile.TemporaryDirectory(dir=folder) as temp:
            m=Path(temp)/'manifest.json'
            m.write_text(json.dumps({**manifest,'run_root':temp,'parent_pid':os.getpid()}),encoding='utf8')
            proc=subprocess.run([sys.executable,str(SCRIPT),str(m)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if proc.returncode:
                err=Path(temp)/'error.json'
                raise ValueError(json.loads(err.read_text(encoding='utf8'))['error'] if err.exists() else 'No se pudo montar la vista previa.')
            (Path(temp)/'result.mp4').replace(output)
    return {'url':f'/api/jobs/{parent_id}/partial-previews/{key}','frames':count,'segments':len(parts)}


def register(app):
    lock=asyncio.Lock()

    @app.post('/api/jobs/{job_id}/sequence/{index}/partial-preview')
    async def create(job_id:UUID,index:int,attempt_id:UUID|None=None):
        service=app.state.jobs.current
        async with lock:
            try:return await asyncio.to_thread(assemble,service,str(job_id),index,str(attempt_id) if attempt_id else None)
            except (OSError,ValueError) as e:raise HTTPException(409,str(e)) from e

    @app.get('/api/jobs/{job_id}/partial-previews/{key}')
    def video(job_id:UUID,key:UUID,download:bool=False):
        service=app.state.jobs.current
        path=service.root/'jobs'/str(job_id)/'partial-previews'/(str(key)+'.mp4')
        if not path.is_file() or not service.store.job(str(job_id)):raise HTTPException(404,'Montaje no encontrado.')
        return FileResponse(path,media_type='video/mp4',filename='Gen-Studio-tramos.mp4' if download else None)
