"""Optional second pass. Every run and output belongs to a new child job."""
import asyncio
import hashlib
import json
import os
import subprocess
from pathlib import Path
from uuid import UUID, uuid4, uuid5

from .face import ROOT, available
from .media import probe


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


async def cpu(service, job, mode, extra):
    if not available(service.settings):
        raise ValueError('El procesamiento local de cabeza no está disponible.')
    folder=service.root/'jobs'/job['id']
    folder.mkdir(parents=True,exist_ok=True)
    run=folder/('head-'+mode+'-'+str(uuid4()))
    run.mkdir()
    manifest={'mode':mode,'model_root':service.settings['face_models'],'parent_pid':os.getpid(),
              'run_root':str(run),**extra}
    path=run/'manifest.json'
    path.write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    process=None
    with (run/'stderr.log').open('wb') as errors:
        try:
            process=await asyncio.create_subprocess_exec(service.settings['face_python'],'-u',
                str(ROOT/'scripts/refine_head.py'),str(path),stdout=asyncio.subprocess.PIPE,stderr=errors,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),
                env={**os.environ,'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','PYTHONIOENCODING':'utf-8'})
            while line:=await process.stdout.readline():
                try:
                    event=json.loads(line.decode('utf-8'))
                    if event.get('type')=='progress':
                        service.store.update(job['id'],stage='Siguiendo la cabeza' if mode=='prepare' else 'Componiendo la cabeza en la escena',
                            progress={'value':event['frame'],'max':extra['frames']})
                except (ValueError,UnicodeDecodeError):
                    pass
            if await process.wait():
                error=run/'error.json'
                raise ValueError(json.loads(error.read_text(encoding='utf-8'))['error'] if error.is_file()
                                 else 'Falló el afinado de cabeza. Consulta el registro del trabajo.')
        finally:
            if process and process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(),5)
                except TimeoutError:
                    process.kill()
                    await process.wait()
    return json.loads((run/'report.json').read_text(encoding='utf-8'))


def parent_inputs(service,job):
    parent_id=job['parameters']['head_parent_id']
    parent=service.store.job(parent_id)
    source=service.store.asset(parent['parameters']['video_id'])
    base=service.root/'results'/(parent_id+('.face.mp4' if parent.get('face_result_available') else '.mp4'))
    mask=service.root/'jobs'/parent_id/'subject-mask.mp4'
    return parent,source,base,mask


async def prepare(service,job):
    parent,source,base,mask=parent_inputs(service,job)
    folder=service.root/'jobs'/job['id']
    folder.mkdir(parents=True,exist_ok=True)
    checkpoint=folder/'head-plan.json'
    if checkpoint.is_file():
        plan=json.loads(checkpoint.read_text(encoding='utf-8'))
        if plan['base_sha256']!=await asyncio.to_thread(digest,base) or plan['source_sha256']!=await asyncio.to_thread(digest,service.root/'inputs'/source['filename']) or plan['mask_sha256']!=await asyncio.to_thread(digest,mask):
            raise ValueError('Las entradas de la segunda pasada cambiaron; crea una nueva copia.')
        asset=service.store.asset(plan['asset']['id'])
        if not asset:
            asset=service.store.add_asset(plan['asset'])
        target=service.root/'inputs'/asset['filename']
        if not target.is_file() or await asyncio.to_thread(digest,target)!=asset['sha256']:
            raise ValueError('El recorte guardado no coincide con el registro del trabajo.')
    else:
        asset_id=str(uuid5(UUID(job['id']),'head-source'))
        target=service.root/'inputs'/(asset_id+'.mp4')
        if target.exists():
            raise ValueError('Existe un recorte sin registro completo. Se conserva para diagnóstico; crea otra copia.')
        face_report=service.root/'jobs'/parent['id']/'face-report.json'
        forced=json.loads(face_report.read_text(encoding='utf-8')).get('scene_cuts',[]) if face_report.is_file() else []
        plan=await cpu(service,job,'prepare',{'source':str(service.root/'inputs'/source['filename']),
            'base':str(base),'mask':str(mask),'frames':parent['parameters']['frames'],
            'forced_cut_frames':forced,'target':str(target)})
        metadata=await asyncio.to_thread(probe,target)
        asset={'id':asset_id,'kind':'video','name':'Recorte automático de cabeza',
            'filename':target.name,'bytes':target.stat().st_size,'sha256':await asyncio.to_thread(digest,target),**metadata}
        plan['asset']=asset
        checkpoint.write_text(json.dumps(plan,indent=2),encoding='utf-8')
        service.store.add_asset(asset)
    payload={**job['parameters'],'video_id':asset['id'], 'mask_grow_spatial':min(64,round(16*512/plan['roi'][2]))}
    return service.store.update(job['id'],parameters=payload,head_plan=plan,scene_resolution=f"{plan['width']}x{plan['height']}",
                                stage='Recorte automático preparado',progress=None)


async def compose(service,job):
    parent,source,base,mask=parent_inputs(service,job)
    plan=job['head_plan']
    if await asyncio.to_thread(digest,base)!=plan['base_sha256'] or await asyncio.to_thread(digest,mask)!=plan['mask_sha256']:
        raise ValueError('La toma original cambió durante el afinado. Se conservan las copias.')
    report=await cpu(service,job,'compose',{'base':str(base),'mask':str(mask),'frames':job['parameters']['frames'],
        'local':str(service.root/'results'/(job['id']+'.face.mp4')),
        'target':str(service.root/'results'/(job['id']+'.head.mp4')),'plan':plan})
    (service.root/'jobs'/job['id']/'head-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report
