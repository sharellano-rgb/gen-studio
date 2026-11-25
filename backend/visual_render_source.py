"""CPU-only, immutable render-input preparation for the faithful spatial method."""
import asyncio,json,os,subprocess
from pathlib import Path
from uuid import uuid4
from .head import digest
from .guided_composition import local_path
from .job_cancel import checkpoint,thread_checkpoint
from .process_cleanup import stop_process_tree

VERSION='visual-faithful-source-1'
ROOT=Path(__file__).resolve().parents[1]

async def _settle(task):
    """Finish owned process work even when cancellation is requested again."""
    interrupted=False
    while True:
        try:return await asyncio.shield(task),interrupted
        except asyncio.CancelledError:
            if task.cancelled():raise
            interrupted=True

async def _cleanup(process,waiter):
    try:
        if process.returncode is None:await stop_process_tree(process)
    finally:
        if waiter is not None:
            if not waiter.done():waiter.cancel()
            await asyncio.gather(waiter,return_exceptions=True)

async def _validate(service,ident,folder,source,key,result):
    if not isinstance(result,dict) or any(result.get(k)!=key[k] for k in ('version','source_sha256','frames')):
        raise ValueError('El registro de la fuente espacial no coincide con el tramo preparado.')
    w,h=result.get('width'),result.get('height')
    if any(type(n) is not int or n<2 or n%2 for n in (w,h)) or w*h>key['pixel_budget']:
        raise ValueError('El tamaño de la fuente espacial excede su presupuesto.')
    target=local_path(service.root,result['path'])
    if (target==source or target.name!='fiel-source.mp4' or target.parent.parent!=folder.resolve()
            or not target.parent.name.startswith('fiel-render-')):
        raise ValueError('La fuente espacial no pertenece a esta toma.')
    if await thread_checkpoint(service,ident,digest,target)!=result.get('sha256'):
        raise ValueError('Cambió la fuente de render espacial.')
    return target

async def prepare(service,job,original,*,accepted=False):
    ident=job['id'];p=job['parameters'];folder=service.root/'jobs'/ident
    checkpoint(service,ident)
    source=local_path(service.root,original['video']);sha=original['files'][str(source)]
    if await thread_checkpoint(service,ident,digest,source)!=sha:raise ValueError('Cambió la fuente preparada de Fiel.')
    key={'version':VERSION,'source_sha256':sha,'frames':p['render_frames'],
         'pixel_budget':max(p['width']*p['height'],round(p['guide_megapixels']*1024*1024))}
    receipt=folder/'fiel-render-source.json'
    if receipt.exists():
        saved=json.loads(receipt.read_text(encoding='utf-8'))
        if saved.get('binding')!=key:raise ValueError('Cambió la fuente de render guardada; crea otra toma.')
        result=saved['result']
        target=await _validate(service,ident,folder,source,key,result)
    else:
        if accepted:raise ValueError('Falta el registro de la fuente de un workflow Fiel aceptado; no se reconstruye.')
        run=folder/('fiel-render-'+str(uuid4()));run.mkdir(parents=True)
        manifest=run/'manifest.json'
        manifest.write_text(json.dumps({**key,'source':str(source),'run_root':str(run),'parent_pid':os.getpid()}),encoding='utf-8')
        service.store.update(ident,stage='Preparando la guía espacial de Fiel',progress=None)
        process=waiter=None
        try:
            with (run/'stderr.log').open('wb') as errors:
                try:
                    spawn=asyncio.create_task(asyncio.create_subprocess_exec(str(ROOT/'.venv/Scripts/python.exe'),
                        str(ROOT/'scripts/fiel_render_media.py'),str(manifest),stdout=asyncio.subprocess.DEVNULL,stderr=errors,
                        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)))
                    try:process=await asyncio.shield(spawn)
                    except asyncio.CancelledError:
                        process,_=await _settle(spawn)
                        raise
                    waiter=asyncio.create_task(process.wait())
                    while True:
                        try:code=await asyncio.wait_for(asyncio.shield(waiter),.5);break
                        except TimeoutError:
                            if waiter.done():raise
                            checkpoint(service,ident)
                    if code:
                        error=run/'error.json'
                        raise ValueError(json.loads(error.read_text(encoding='utf-8'))['error'] if error.exists() else 'Falló la preparación espacial de Fiel.')
                finally:
                    if process is not None:
                        _,interrupted=await _settle(asyncio.create_task(_cleanup(process,waiter)))
                        if interrupted:raise asyncio.CancelledError
            checkpoint(service,ident)
            result=json.loads((run/'report.json').read_text(encoding='utf-8'))
            target=await _validate(service,ident,folder,source,key,result)
            checkpoint(service,ident)
            temp=receipt.with_suffix('.tmp');temp.write_text(json.dumps({'binding':key,'result':result},indent=2),encoding='utf-8');temp.replace(receipt)
        except BaseException:
            for artifact in (run/'fiel-source.mp4',run/'report.json',receipt.with_suffix('.tmp')):
                artifact.unlink(missing_ok=True)
            raise
    return {**original,'video':str(target),'files':{**original['files'],str(target):result['sha256']}},result['sha256']
