"""Optional, bounded CPU head-proportion copies of completed direct scenes."""
import asyncio,json,os,shutil,subprocess
from pathlib import Path
from uuid import UUID,uuid4
from fastapi import HTTPException
from pydantic import BaseModel,Field
from .head import digest
from .workflows import ROOT

class GeometryRequest(BaseModel):
    request_key:UUID
    region:list[int]=Field(min_length=4,max_length=4)
    start:float=Field(ge=0,le=6,allow_inf_nan=False)
    end:float=Field(gt=0,le=6,allow_inf_nan=False)
    fade:float=Field(default=.5,ge=.2,le=1,allow_inf_nan=False)
    scale:float=Field(default=.9,ge=.85,le=.98,allow_inf_nan=False)
class Review(BaseModel):
    approved:bool
    feedback:str=Field(default='',max_length=1000)

def enabled(settings):
    return bool(settings.get('generation_enabled') and Path(settings.get('face_python','')).is_file() and (Path(settings.get('face_models',''))/'models/buffalo_l/det_10g.onnx').is_file())
def source(service,parent):
    if not parent or parent['status']!='completed' or not parent.get('result_available') or parent['parameters'].get('engine')!='h3_direct' or parent['parameters'].get('operation'):
        raise ValueError('Selecciona una escena H3 directa terminada, sin correcciones anteriores.')
    frames=parent['parameters']['frames'];width,height=map(int,parent['parameters']['resolution'].split('x'))
    if not 12<=frames<=124 or max(width,height)>1920:raise ValueError('El ajuste experimental está limitado a tomas cortas de 12 a 124 fotogramas.')
    path=service.root/'results'/f'{parent["id"]}.mp4'
    if not path.is_file() or digest(path)!=parent.get('direct_result_sha256'):raise ValueError('Cambió la escena registrada; no se crea otra copia.')
    return path,frames,width,height

def register(app,settings):
    @app.get('/api/jobs/{job_id}/geometry-options')
    async def options(job_id:UUID):
        service=app.state.jobs;parent=service.store.job(str(job_id))
        if not parent:raise HTTPException(404,'Trabajo no encontrado.')
        try:
            path,frames,w,h=await asyncio.to_thread(source,service,parent)
            defaults={'start':0,'end':frames/24,'fade':min(.5,frames/48),'scale':.9,'region':None}
            # Reuse the exact reviewed selection only for its bound source hash.
            review=service.root/'identity-tests/head-size-targeted-09/video-report.json'
            if review.is_file():
                record=json.loads(review.read_text(encoding='utf-8'))
                if record.get('source_job')==parent['id'] and record.get('source_sha256')==digest(path):
                    defaults.update(start=1.75,end=3.25,fade=.5,region=[0,0,640,360])
            return {'enabled':enabled(settings),'width':w,'height':h,'frames':frames,'duration':frames/24,'url':f'/api/jobs/{parent["id"]}/result','defaults':defaults}
        except ValueError as e:return {'enabled':False,'reason':str(e)}

    @app.post('/api/jobs/{job_id}/geometry-copy',status_code=202)
    async def submit(job_id:UUID,body:GeometryRequest):
        if not enabled(settings):raise HTTPException(503,'Falta el procesamiento CPU local; no se instalan modelos automáticamente.')
        service=app.state.jobs
        async with service.lock:
            parent=service.store.job(str(job_id))
            if not parent:raise HTTPException(404,'Trabajo no encontrado.')
            try:
                path,frames,w,h=await asyncio.to_thread(source,service,parent)
                x,y,rw,rh=body.region
                if min(x,y)<0 or min(rw,rh)<64 or x+rw>w or y+rh>h:raise ValueError('La zona de cabeza está fuera de la escena.')
                if not body.start<body.end<=frames/24 or body.end-body.start<2*body.fade:raise ValueError('Usa un intervalo válido con espacio para ambas transiciones.')
                payload={**parent['parameters'],'operation':'geometry_copy','geometry_parent_id':parent['id'],'geometry_source_sha256':digest(path),'geometry_region':body.region,'geometry_start':body.start,'geometry_end':body.end,'geometry_fade':body.fade,'geometry_scale':body.scale}
                child=service.store.create_job(str(uuid4()),str(body.request_key),payload)
            except ValueError as e:raise HTTPException(409,str(e)) from e
            service.wake.set();return child

    @app.post('/api/jobs/{job_id}/review-geometry')
    async def review(job_id:UUID,body:Review):
        service=app.state.jobs
        async with service.lock:
            job=service.store.job(str(job_id))
            if not job:raise HTTPException(404,'Trabajo no encontrado.')
            if job['status']!='completed' or job['parameters'].get('operation')!='geometry_copy':raise HTTPException(409,'Elige una copia geométrica terminada.')
            target=service.root/'results'/f'{job["id"]}.mp4'
            if not target.is_file() or digest(target)!=job.get('geometry_result_sha256'):raise HTTPException(409,'La copia publicada cambió; no se guarda su revisión.')
            return service.store.update(job['id'],geometry_review={'approved':body.approved,'review_required':not body.approved,'feedback':body.feedback},stage='Copia geométrica revisada' if body.approved else 'Ajuste geométrico · Incidencia registrada')

async def process(service,job):
    p=job['parameters'];parent=service.store.job(p['geometry_parent_id']);src,frames,w,h=await asyncio.to_thread(source,service,parent)
    if digest(src)!=p['geometry_source_sha256']:raise ValueError('Cambió la toma después del envío.')
    folder=service.root/'jobs'/job['id'];folder.mkdir(parents=True,exist_ok=True);target=service.root/'results'/f'{job["id"]}.mp4';checkpoint=folder/'geometry-report.json'
    if checkpoint.exists():
        report=json.loads(checkpoint.read_text(encoding='utf-8'))
        if not target.exists():
            saved=Path(report['saved_video']).resolve();saved.relative_to(folder.resolve())
            if not saved.is_file() or digest(saved)!=report['result_sha256']:raise ValueError('Cambió la salida registrada antes de publicarla.')
            shutil.copy2(saved,target)
        if not target.is_file() or digest(target)!=report['result_sha256']:raise ValueError('La copia registrada cambió; crea otra copia.')
    else:
        run=folder/('geometry-'+str(uuid4()));run.mkdir()
        manifest={'source':str(src),'source_sha256':p['geometry_source_sha256'],'run_root':str(run),'parent_pid':os.getpid(),'model_root':service.settings['face_models'],'frames':frames,'width':w,'height':h,'region':p['geometry_region'],'start':p['geometry_start'],'end':p['geometry_end'],'fade':p['geometry_fade'],'scale':p['geometry_scale']}
        cfg=run/'manifest.json';cfg.write_text(json.dumps(manifest,indent=2),encoding='utf-8');child=None
        with (run/'stderr.log').open('wb') as log:
            try:
                child=await asyncio.create_subprocess_exec(service.settings['face_python'],'-u',str(ROOT/'scripts/geometry_copy.py'),str(cfg),stdout=asyncio.subprocess.PIPE,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),env={**os.environ,'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','PYTHONIOENCODING':'utf-8'})
                while line:=await child.stdout.readline():
                    try:
                        event=json.loads(line)
                        if event.get('type')=='progress':service.store.update(job['id'],status='refining',stage=event['stage'],progress={'value':event['frame'],'max':event['max']})
                    except ValueError:pass
                if await child.wait():
                    error=run/'error.json';raise ValueError(json.loads(error.read_text(encoding='utf-8'))['error'] if error.exists() else 'Falló el ajuste geométrico; consulta el registro de la copia.')
            finally:
                if child and child.returncode is None:
                    child.terminate()
                    try:await asyncio.wait_for(child.wait(),5)
                    except TimeoutError:child.kill();await child.wait()
        report=json.loads((run/'report.json').read_text(encoding='utf-8'))
        if digest(src)!=p['geometry_source_sha256'] or digest(run/'corrected.mp4')!=report['result_sha256']:raise ValueError('Cambió una salida del ajuste.')
        if target.exists():raise ValueError('Ya existe una salida; se conserva.')
        report['saved_video']=str(run/'corrected.mp4');checkpoint.write_text(json.dumps(report,indent=2),encoding='utf-8');shutil.copy2(run/'corrected.mp4',target)
    service.store.update(job['id'],status='completed',stage='Copia geométrica lista · Revisa proporción y bordes',result_available=True,progress=None,geometry_result_sha256=report['result_sha256'],geometry_review={'approved':False,'review_required':True},geometry_applied_frames=report['applied_frames'])
    service.event(job['id'],'Ajuste geométrico CPU terminado en una copia; no se enviaron trabajos a ComfyUI ni se alteró la alternancia de VRAM.')
