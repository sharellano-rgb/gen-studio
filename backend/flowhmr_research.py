"""Private, fixed-input FlowHMR research trial under Studio's shared GPU lease.

The runner is pinned at registration, receives no caller-selected paths/commands,
and may load CUDA only after a Windows kill-on-close JobObject owns its process.
"""
import asyncio
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from urllib.parse import quote
from uuid import UUID

from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

VERSION='flowhmr-research-1'
RUNTIME=Path(r'D:\AI\experiments\FlowHMR')
SOURCE_ID='64790c34-81ce-4fb0-b8d1-bb9e2b73a1fd'
REFERENCE_ID='7c6494b9-f5ba-4b41-91b6-1ea7d22888f3'
TERMINAL={'completed','failed','cancelled','interrupted'}

class Request(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_key:UUID

def now():
    from .store import now as stamp
    return stamp()

def digest(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(4*1024*1024),b''):result.update(block)
    return result.hexdigest()

def contained(root,path):
    root=Path(root).resolve();path=Path(path).resolve()
    if not path.is_relative_to(root):raise ValueError('Un archivo del ensayo estÃ¡ fuera de su carpeta autorizada.')
    return path

def read_json(path,limit=2*1024*1024):
    if path.stat().st_size>limit:raise ValueError('El registro del ensayo supera el tamaÃ±o permitido.')
    value=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value,dict):raise ValueError('El registro del ensayo no es vÃ¡lido.')
    return value

def record_path(service,ident):
    ident=str(UUID(str(ident)))
    return contained(service.root,service.root/'research'/'flowhmr'/(ident+'.json'))

def save(service,record):
    path=record_path(service,record['id']);path.parent.mkdir(parents=True,exist_ok=True)
    record['updated_at']=now();temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)

def load(service,ident):
    path=record_path(service,ident)
    if not path.is_file():raise HTTPException(404,'Ensayo no encontrado en este espacio.')
    record=read_json(path)
    if record.get('id')!=str(ident) or record.get('version')!=VERSION:raise HTTPException(409,'El ensayo guardado no es compatible.')
    return record

def output_path(ident):
    ident=str(UUID(str(ident)))
    return contained(RUNTIME,RUNTIME/'research-runs'/ident)

def owner(app):
    service=app.state.jobs.current
    if service.settings.get('workspace_id')!='owner':raise HTTPException(404,'Ensayo no disponible en este espacio.')
    return service

def preflight(service,runner_sha256):
    if not re.fullmatch(r'[0-9a-f]{64}',runner_sha256 or ''):raise ValueError('El ejecutor FlowHMR no tiene una revisiÃ³n fijada.')
    python=contained(RUNTIME,RUNTIME/'.venv/Scripts/python.exe')
    runner=contained(RUNTIME,RUNTIME/'research-tools/run_trial.py')
    receipt_path=contained(RUNTIME,RUNTIME/'trial-inputs/selection.json')
    if not python.is_file() or not runner.is_file() or digest(runner)!=runner_sha256:
        raise ValueError('CambiÃ³ o falta el ejecutor verificado de FlowHMR.')
    receipt=read_json(receipt_path);selection=receipt['selection']
    if (selection.get('workspace')!='owner' or selection.get('source_asset_id')!=SOURCE_ID
            or selection.get('reference_asset_id')!=REFERENCE_ID
            or selection.get('selected_start_seconds_relative_to_first_video_frame')!=0
            or selection.get('selected_duration_seconds')!=14
            or selection.get('flowhmr_target_frames_at_30fps')!=420
            or selection.get('gen_studio_target_frames_at_24fps')!=336):
        raise ValueError('El ensayo no corresponde al intervalo privado de 14 segundos seleccionado.')
    files=[]
    for ident,kind,prefix in ((SOURCE_ID,'video','source'),(REFERENCE_ID,'image','reference')):
        asset=service.store.asset(ident)
        if not asset or asset['kind']!=kind:raise ValueError('Una entrada del ensayo no pertenece al propietario.')
        path=contained(service.root/'inputs',service.root/'inputs'/asset['filename'])
        if path.parent!=(service.root/'inputs').resolve() or Path(selection[prefix+'_path']).resolve()!=path:
            raise ValueError('La entrada del ensayo no coincide con la biblioteca privada.')
        sha=digest(path)
        if sha!=selection[prefix+'_sha256'] or (asset.get('sha256') and sha!=asset['sha256']):
            raise ValueError('CambiÃ³ una entrada del ensayo.')
        files.append({'id':ident,'sha256':sha,'kind':kind})
    clip=contained(RUNTIME,RUNTIME/'trial-inputs/dance-64790c34-14s-30fps.mp4')
    if Path(receipt['clip_path']).resolve()!=clip or digest(clip)!=receipt['clip_sha256']:
        raise ValueError('CambiÃ³ el vÃ­deo normalizado del ensayo.')
    probe=receipt['probe']
    if int(probe['nb_read_frames'])!=420 or probe['avg_frame_rate']!='30/1' or float(probe['duration'])!=14:
        raise ValueError('El ensayo debe contener 420 fotogramas a 30 fps.')
    return {'version':VERSION,'runner_sha256':runner_sha256,'receipt_sha256':digest(receipt_path),
        'clip_sha256':receipt['clip_sha256'],'files':files,'frames':420,'fps':30,
        'python':str(python),'runner':str(runner),'receipt':str(receipt_path)}

def public(service,record):
    result={k:record.get(k) for k in ('id','version','status','stage','progress','error','created_at','updated_at','cleanup_confirmed')}
    task=getattr(service,'flowhmr_research_task',None)
    if record['status'] not in TERMINAL:
        if getattr(service,'flowhmr_research_task_id',None)!=record['id'] or task is None or task.done():
            result.update(status='interrupted',stage='El ensayo se interrumpiÃ³; no se repetirÃ¡ automÃ¡ticamente.')
        else:
            path=output_path(record['id'])/'progress.json'
            try:
                progress=read_json(path,65536)
                if isinstance(progress.get('stage'),str):result['stage']=progress['stage'][:400]
                value=progress.get('progress')
                if type(value) in (int,float) and math.isfinite(value) and 0<=value<=1:result['progress']=value
            except (OSError,ValueError,KeyError):pass
    result['research_only']=True
    if record['status']=='completed' and record.get('cleanup_confirmed'):
        result['artifacts']=[{'path':item['path'],'sha256':item['sha256'],
            'url':f'/api/research/flowhmr/{record["id"]}/artifacts/'+quote(item['path'],safe='/')} for item in record.get('artifacts',[])]
    return result

REVIEW_VERSION='flowhmr-alignment-review-1'


def review_output_path(ident):
    ident=str(UUID(str(ident)))
    return contained(RUNTIME,RUNTIME/'research-reviews'/ident)


def load_review(service,parent_id,review_id):
    """A derived CPU review is separate from the immutable GPU trial."""
    parent=load(service,parent_id)
    if parent['status']!='completed' or not parent.get('cleanup_confirmed'):
        raise HTTPException(409,'El ensayo original no ha terminado.')
    review_id=str(UUID(str(review_id)))
    path=contained(service.root,service.root/'research'/'flowhmr-reviews'/parent_id/(review_id+'.json'))
    if not path.is_file():raise HTTPException(404,'Revision no encontrada en este espacio.')
    review=read_json(path)
    if (review.get('version')!=REVIEW_VERSION or review.get('id')!=review_id
            or review.get('parent_id')!=parent_id or review.get('status')!='completed'
            or review.get('parent_result_sha256')!=parent.get('result_sha256')
            or review.get('clip_sha256')!=parent['binding']['clip_sha256']
            or review.get('frames')!=parent['binding']['frames'] or review.get('fps')!=parent['binding']['fps']
            or review.get('research_only') is not True or review.get('safe_to_use_as_pixel_aligned_guide') is not False):
        raise ValueError('La revision no corresponde al ensayo original verificado.')
    original=contained(output_path(parent_id),output_path(parent_id)/'result.json')
    if digest(original)!=parent['result_sha256']:raise ValueError('Cambio el resultado del ensayo original.')
    artifacts=review.get('artifacts')
    if not isinstance(artifacts,list) or not 1<=len(artifacts)<=32:raise ValueError('La revision no tiene archivos validos.')
    seen=set()
    for item in artifacts:
        if not isinstance(item,dict) or not isinstance(item.get('path'),str) or item['path'] in seen:
            raise ValueError('La revision repite un archivo o tiene un registro invalido.')
        checked_artifact(review_output_path(review_id),item)
        seen.add(item['path'])
    return review


def public_review(review):
    result={key:review.get(key) for key in ('id','parent_id','version','status','frames','fps','research_only','safe_to_use_as_pixel_aligned_guide','summary')}
    result['artifacts']=[{'path':item['path'],'sha256':item['sha256'],
        'url':f'/api/research/flowhmr/{review["parent_id"]}/reviews/{review["id"]}/artifacts/'+quote(item['path'],safe='/')}
        for item in review['artifacts']]
    return result


async def free_gpu(service):
    """Release only the same allowed local engines used by Studio generation."""
    if not await service.idle():raise ValueError('ComfyUI tiene trabajo pendiente; no se iniciÃ³ FlowHMR.')
    base=service.settings['ollama_url'];loaded=(await service.get(base,'/api/ps')).get('models',[])
    names={m.get('name') or m.get('model') for m in loaded}
    aliases={'simpligen-agent:latest','simpligen-qwen36:latest'}
    if any(not isinstance(n,str) or not(n.startswith('qwen3') or n in aliases) for n in names):
        raise ValueError('Ollama tiene un modelo ajeno a la alternancia autorizada.')
    for name in names:await service.post(base,'/api/generate',json={'model':name,'keep_alive':0,'stream':False},timeout=90)
    for _ in range(30):
        if not (await service.get(base,'/api/ps')).get('models'):break
        await asyncio.sleep(1)
    else:raise ValueError('Ollama no confirmÃ³ la liberaciÃ³n de memoria.')
    if not await service.idle():raise ValueError('ComfyUI recibiÃ³ otro trabajo; no se iniciÃ³ FlowHMR.')
    await service.post(service.settings['comfy_url'],'/free',json={'unload_models':True,'free_memory':True})
    for _ in range(30):
        devices=(await service.get(service.settings['comfy_url'],'/system_stats')).get('devices',[])
        if devices and all(d.get('torch_vram_total',float('inf'))<512*1024**2 for d in devices):break
        await asyncio.sleep(1)
    else:raise ValueError('ComfyUI no confirmÃ³ memoria libre.')
    if not await service.idle():raise ValueError('ComfyUI recibiÃ³ otro trabajo; no se iniciÃ³ FlowHMR.')

class WindowsJob:
    """Kernel kills the research worker if Studio exits unexpectedly."""
    def __init__(self,name):
        if os.name!='nt':raise ValueError('Este ensayo local requiere el supervisor de procesos de Windows.')
        import ctypes
        from ctypes import wintypes as wt
        class BASIC(ctypes.Structure):
            _fields_=[('PerProcessUserTimeLimit',ctypes.c_int64),('PerJobUserTimeLimit',ctypes.c_int64),
                ('LimitFlags',wt.DWORD),('MinimumWorkingSetSize',ctypes.c_size_t),('MaximumWorkingSetSize',ctypes.c_size_t),
                ('ActiveProcessLimit',wt.DWORD),('Affinity',ctypes.c_size_t),('PriorityClass',wt.DWORD),('SchedulingClass',wt.DWORD)]
        class IO(ctypes.Structure):_fields_=[(name,ctypes.c_uint64) for name in ('ReadOperationCount','WriteOperationCount','OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount')]
        class EXTENDED(ctypes.Structure):
            _fields_=[('BasicLimitInformation',BASIC),('IoInfo',IO),('ProcessMemoryLimit',ctypes.c_size_t),('JobMemoryLimit',ctypes.c_size_t),('PeakProcessMemoryUsed',ctypes.c_size_t),('PeakJobMemoryUsed',ctypes.c_size_t)]
        api=ctypes.WinDLL('kernel32',use_last_error=True)
        api.CreateJobObjectW.argtypes=[ctypes.c_void_p,wt.LPCWSTR];api.CreateJobObjectW.restype=wt.HANDLE
        api.SetInformationJobObject.argtypes=[wt.HANDLE,ctypes.c_int,ctypes.c_void_p,wt.DWORD];api.SetInformationJobObject.restype=wt.BOOL
        api.OpenProcess.argtypes=[wt.DWORD,wt.BOOL,wt.DWORD];api.OpenProcess.restype=wt.HANDLE
        api.AssignProcessToJobObject.argtypes=[wt.HANDLE,wt.HANDLE];api.AssignProcessToJobObject.restype=wt.BOOL
        api.CloseHandle.argtypes=[wt.HANDLE];api.CloseHandle.restype=wt.BOOL
        self.api=api;self.ctypes=ctypes;self.name=name
        ctypes.set_last_error(0);self.handle=api.CreateJobObjectW(None,name)
        if not self.handle:raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error()==183:
            self.close();raise ValueError('Ya existe un supervisor con la clave de este ensayo.')
        info=EXTENDED();info.BasicLimitInformation.LimitFlags=0x00002000
        if not api.SetInformationJobObject(self.handle,9,ctypes.byref(info),ctypes.sizeof(info)):
            error=ctypes.WinError(ctypes.get_last_error());self.close();raise error
    def assign(self,pid):
        process=self.api.OpenProcess(0x0100|0x0001,False,pid)
        if not process:raise self.ctypes.WinError(self.ctypes.get_last_error())
        try:
            if not self.api.AssignProcessToJobObject(self.handle,process):raise self.ctypes.WinError(self.ctypes.get_last_error())
        finally:self.api.CloseHandle(process)
    def close(self):
        if self.handle:
            if not self.api.CloseHandle(self.handle):raise self.ctypes.WinError(self.ctypes.get_last_error())
            self.handle=None

async def finish_task(task):
    """Do not let repeated cancellation abort resource cleanup."""
    cancelled=False
    while not task.done():
        try:await asyncio.shield(task)
        except asyncio.CancelledError:cancelled=True
    return task.result(),cancelled

async def cleanup_process(spawn,process,job):
    if process is None and spawn is not None:
        try:process=await spawn
        except Exception:
            # A failed create_subprocess_exec has no live process handle. Its
            # original failure is already recorded by run(). Still close job.
            process=None
    # Closing the kernel job also terminates CPU descendants such as ffmpeg.
    close_error=None
    if job is not None:
        try:job.close()
        except Exception as error:close_error=error
    if process is not None:
        if process.returncode is None:
            try:process.kill()
            except ProcessLookupError:pass
        await asyncio.wait_for(process.wait(),30)
    if close_error is not None:raise close_error

def checked_artifact(folder,item):
    name=item.get('path')
    if (not isinstance(name,str) or not name or len(name)>300 or '\\' in name
            or PurePosixPath(name).is_absolute() or any(p in ('..','.') for p in name.split('/'))
            or ':' in name or not re.fullmatch(r'[0-9a-f]{64}',item.get('sha256',''))):
        raise ValueError('Un archivo del resultado no es vÃ¡lido.')
    path=contained(folder,folder/name)
    if not path.is_file() or digest(path)!=item['sha256']:raise ValueError('CambiÃ³ un archivo del resultado de FlowHMR.')
    return path

def validate_result(folder,binding):
    path=contained(folder,folder/'result.json');result=read_json(path)
    if (result.get('status')!='completed' or result.get('clip_sha256')!=binding['clip_sha256']
            or type(result.get('frames')) is not int or result['frames']!=420 or result.get('fps')!=30):
        raise ValueError('FlowHMR no devolviÃ³ un resultado completo del intervalo seleccionado.')
    artifacts=result.get('artifacts')
    if not isinstance(artifacts,list) or not 1<=len(artifacts)<=128:raise ValueError('Faltan los archivos verificables de FlowHMR.')
    seen=set();clean=[]
    for item in artifacts:
        if not isinstance(item,dict):raise ValueError('Archivo de resultado no vÃ¡lido.')
        checked_artifact(folder,item)
        if item['path'] in seen:raise ValueError('El resultado repite un archivo.')
        seen.add(item['path']);clean.append({'path':item['path'],'sha256':item['sha256']})
    return {'result_sha256':digest(path),'artifacts':clean}

async def run(service,record):
    record['worker_entered']=True
    binding=record['binding'];folder=output_path(record['id']);process=spawn=job=None;cancelled=False;log=None
    cleanup_confirmed=False
    try:
        save(service,record)
        await free_gpu(service)
        # Re-check accepted file hashes immediately before the executable starts.
        fresh=await asyncio.to_thread(preflight,service,binding['runner_sha256'])
        if fresh!=binding:raise ValueError('Las entradas cambiaron despuÃ©s de aceptar el ensayo.')
        folder.mkdir(parents=True,exist_ok=False)
        job=WindowsJob('Local\\GenStudio-FlowHMR-'+record['id'])
        env=os.environ.copy();env.update(HF_HOME=r'D:\AI\models\HuggingFace',TORCH_HOME=r'D:\AI\cache\torch',
            TEMP=r'D:\AI\cache\tmp\flowhmr',TMP=r'D:\AI\cache\tmp\flowhmr',HF_HUB_OFFLINE='1',
            TRANSFORMERS_OFFLINE='1',PYTHONDONTWRITEBYTECODE='1',PYTHONUNBUFFERED='1',PYTHONIOENCODING='utf-8',PYTHONUTF8='1',GEN_STUDIO_FLOWHMR_LEASE=record['id'])
        log=(folder/'runner.log').open('xb')
        args=[binding['python'],'-B',binding['runner'],'--receipt',binding['receipt'],'--output',str(folder),
            '--lease-gate',str(folder/'lease-ready.json')]
        spawn=asyncio.create_task(asyncio.create_subprocess_exec(*args,cwd=str(RUNTIME),env=env,stdin=subprocess.DEVNULL,
            stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)))
        process=await asyncio.shield(spawn)
        job.assign(process.pid)
        # No await between assignment and persisting/opening the gate.
        record.update(pid=process.pid,job_object_assigned=True,stage='FlowHMR Â· Reconstruyendo movimiento humano')
        save(service,record)
        # A Windows venv executable can spawn a redirector child before its PID
        # is assigned. The pinned runner must join this named job itself, close
        # its temporary job handle, and only then import torch/load any model.
        gate={'run_id':record['id'],'runner_sha256':binding['runner_sha256'],'job_object_name':job.name}
        temp=folder/'lease-ready.tmp';temp.write_text(json.dumps(gate),encoding='utf-8');temp.replace(folder/'lease-ready.json')
        wait=asyncio.create_task(process.wait())
        code=await asyncio.wait_for(asyncio.shield(wait),3600)
        if code!=0:raise ValueError(f'FlowHMR terminÃ³ con cÃ³digo {code}; revisa el registro local del ensayo.')
        checked=await asyncio.to_thread(validate_result,folder,binding)
        record.update(status='completed',stage='ReconstrucciÃ³n lista para revisar',progress=1.,**checked)
    except asyncio.CancelledError:
        cancelled=True;record.update(status='cancelled',stage='Cancelando el ensayo y liberando la GPU',progress=None)
    except Exception as error:
        record.update(status='failed',stage='El ensayo se detuvo',error=str(error)[:1400],progress=None)
    finally:
        cleanup=asyncio.create_task(cleanup_process(spawn,process,job))
        try:
            _,extra_cancel=await finish_task(cleanup);cancelled=cancelled or extra_cancel;cleanup_confirmed=True
        except Exception as error:
            record.update(status='failed',stage='No se confirmÃ³ la detenciÃ³n del ejecutor',error=str(error)[:1400])
        if log is not None:log.close()
        record['cleanup_confirmed']=cleanup_confirmed
        if cancelled and cleanup_confirmed:record.update(status='cancelled',stage='Ensayo cancelado Â· Ejecutable detenido',progress=None)
        try:save(service,record)
        finally:
            if cleanup_confirmed:
                service.enhancer_active=False;service.release_analysis();service.wake.set()
    if cancelled:raise asyncio.CancelledError()

def before_start_cancelled(service,record,task):
    """A task cancelled before its first instruction cannot run a finally block."""
    if (not task.cancelled() or record.get('worker_entered')
            or getattr(service,'flowhmr_research_task',None) is not task
            or getattr(service,'flowhmr_research_task_id',None)!=record['id']):return
    record.update(status='cancelled',stage='Ensayo cancelado antes de iniciar',progress=None,cleanup_confirmed=True)
    try:save(service,record)
    finally:
        service.enhancer_active=False;service.release_analysis();service.wake.set()

def register(app,settings,*,runner_sha256):
    @app.post('/api/research/flowhmr',status_code=202)
    async def start(body:Request):
        service=owner(app)
        if not settings.get('generation_enabled'):raise HTTPException(503,'La generaciÃ³n local estÃ¡ desactivada.')
        ident=str(body.request_key)
        async with service.lock:
            if record_path(service,ident).exists():return public(service,load(service,ident))
            if service.enhancer_active or service.store.unfinished_jobs():raise HTTPException(409,'Espera a que termine el trabajo actual.')
            try:binding=await asyncio.to_thread(preflight,service,runner_sha256)
            except (OSError,ValueError,KeyError,TypeError) as error:raise HTTPException(409,str(error)) from error
            if output_path(ident).exists():raise HTTPException(409,'Ya existe un resultado con esta clave; no se repetirÃ¡.')
            if service.enhancer_active or service.store.unfinished_jobs():raise HTTPException(409,'LlegÃ³ otro trabajo; espera a que termine.')
            if not await service.idle():raise HTTPException(409,'ComfyUI tiene trabajo pendiente.')
            if not service.reserve_analysis():raise HTTPException(409,'El motor compartido estÃ¡ ocupado.')
            record={'id':ident,'version':VERSION,'status':'running','stage':'Preparando el ensayo privado FlowHMR',
                'progress':None,'error':None,'created_at':now(),'binding':binding,'cleanup_confirmed':False}
            try:
                save(service,record);service.enhancer_active=True
                task=asyncio.create_task(run(service,record));service.enhancer_task=task
                service.flowhmr_research_task=task;service.flowhmr_research_task_id=ident
                task.add_done_callback(lambda finished:before_start_cancelled(service,record,finished))
            except BaseException:
                service.enhancer_active=False;service.release_analysis();raise
            return public(service,record)

    @app.get('/api/research/flowhmr/{ident}')
    async def status(ident:UUID):
        service=owner(app);return public(service,load(service,str(ident)))

    @app.post('/api/research/flowhmr/{ident}/cancel',status_code=202)
    async def cancel(ident:UUID):
        service=owner(app)
        async with service.lock:
            record=load(service,str(ident));task=getattr(service,'flowhmr_research_task',None)
            if record['status'] in TERMINAL:return public(service,record)
            if getattr(service,'flowhmr_research_task_id',None)==str(ident) and task is not None and not task.done():task.cancel()
            return public(service,record)

    @app.get('/api/research/flowhmr/{ident}/artifacts/{name:path}')
    async def artifact(ident:UUID,name:str):
        service=owner(app);record=load(service,str(ident))
        if record['status']!='completed' or not record.get('cleanup_confirmed'):raise HTTPException(409,'Espera a que termine el ensayo.')
        selected=next((item for item in record.get('artifacts',[]) if item['path']==name),None)
        if not selected:raise HTTPException(404,'Archivo no encontrado.')
        try:path=await asyncio.to_thread(checked_artifact,output_path(str(ident)),selected)
        except (OSError,ValueError) as error:raise HTTPException(409,str(error)) from error
        return FileResponse(path,headers={'Cache-Control':'private, no-store'},filename=path.name,
            content_disposition_type='inline' if path.suffix.lower() in {'.mp4','.jpg','.jpeg','.png'} else 'attachment')

    @app.get('/api/research/flowhmr/{ident}/reviews/{review_id}')
    async def review_status(ident:UUID,review_id:UUID):
        service=owner(app)
        try:review=await asyncio.to_thread(load_review,service,str(ident),str(review_id))
        except (OSError,ValueError,KeyError,TypeError) as error:raise HTTPException(409,str(error)) from error
        return public_review(review)

    @app.get('/api/research/flowhmr/{ident}/reviews/{review_id}/artifacts/{name:path}')
    async def review_artifact(ident:UUID,review_id:UUID,name:str):
        service=owner(app)
        try:review=await asyncio.to_thread(load_review,service,str(ident),str(review_id))
        except (OSError,ValueError,KeyError,TypeError) as error:raise HTTPException(409,str(error)) from error
        item=next((item for item in review['artifacts'] if item['path']==name),None)
        if item is None:raise HTTPException(404,'Archivo no encontrado.')
        try:path=await asyncio.to_thread(checked_artifact,review_output_path(str(review_id)),item)
        except (OSError,ValueError) as error:raise HTTPException(409,str(error)) from error
        return FileResponse(path,headers={'Cache-Control':'private, no-store'},filename=path.name,
            content_disposition_type='inline' if path.suffix.lower() in {'.mp4','.jpg','.jpeg','.png'} else 'attachment')
