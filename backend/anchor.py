"""Queue adapter for the bounded CPU identity pass."""
import asyncio, json, os, shutil, subprocess
from pathlib import Path
from uuid import uuid4
from .face import ROOT, available
from .head import digest

async def process(service,job):
    if job['parameters'].get('anchor_method')=='guided_composition':
        from .guided_composition import process as guided_process
        return await guided_process(service,job)
    if not available(service.settings): raise ValueError('La corrección facial local no está disponible.')
    payload=job['parameters']; folder=service.root/'jobs'/job['id']; folder.mkdir(parents=True,exist_ok=True)
    source=Path(payload['anchor_source'])
    reference=service.store.asset(payload['face_reference_id'])
    if not reference: raise ValueError('La foto real ya no está disponible.')
    checkpoint=folder/'anchor-report.json'
    results=service.root/'results'; results.mkdir(exist_ok=True)
    target=results/(job['id']+'.face.mp4'); detail=results/(job['id']+'.detail.mp4')
    if checkpoint.exists():
        report=json.loads(checkpoint.read_text(encoding='utf-8'))
        if target.is_file() and detail.is_file() and digest(target)==report['result_sha256'] and digest(detail)==report['detail_sha256']:
            return report
        raise ValueError('La copia registrada cambió. Crea una nueva corrección.')
    run=folder/('anchor-'+str(uuid4())); run.mkdir()
    manifest={'generated':str(source),'source_sha256':payload['anchor_sha256'],
        'reference':str(service.root/'inputs'/reference['filename']),'region':payload['anchor_region'],
        'model_root':service.settings['face_models'],'parent_pid':os.getpid(),'run_root':str(run)}
    path=run/'manifest.json'; path.write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    process=None
    with (run/'stderr.log').open('wb') as errors:
        try:
            process=await asyncio.create_subprocess_exec(service.settings['face_python'],'-u',str(ROOT/'scripts/anchor_face.py'),str(path),
                stdout=asyncio.subprocess.PIPE,stderr=errors,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),
                env={**os.environ,'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','PYTHONIOENCODING':'utf-8'})
            while line:=await process.stdout.readline():
                try:
                    event=json.loads(line.decode('utf-8'))
                    if event.get('type')=='progress':
                        service.store.update(job['id'],stage=event['stage'],progress={'value':event['frame'],'max':event['max']})
                except (ValueError,UnicodeDecodeError): pass
            if await process.wait():
                error=run/'error.json'
                raise ValueError(json.loads(error.read_text(encoding='utf-8'))['error'] if error.exists() else 'Falló la corrección; consulta el registro del trabajo.')
        finally:
            if process and process.returncode is None:
                process.terminate()
                try: await asyncio.wait_for(process.wait(),5)
                except TimeoutError: process.kill(); await process.wait()
    report=json.loads((run/'report.json').read_text(encoding='utf-8'))
    # A retry can finish a previous interrupted publication only if bytes match.
    for src,dst,key in [(run/'corrected.mp4',target,'result_sha256'),(run/'detail.mp4',detail,'detail_sha256')]:
        if dst.exists():
            if digest(dst)!=report[key]: raise ValueError('Ya existe una copia diferente; se conserva.')
        else: await asyncio.to_thread(shutil.copy2,src,dst)
    checkpoint.write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report
