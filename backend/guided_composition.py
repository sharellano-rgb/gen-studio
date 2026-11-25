"""Recompose an already approved, registered H3 head. No GPU generation here."""
import asyncio,json,os,re,shutil,subprocess
from pathlib import Path
from uuid import UUID,uuid4
from fastapi import HTTPException
from pydantic import BaseModel,Field
from .face import ROOT,available
from .head import digest

class GuidedRequest(BaseModel):
    request_key:UUID
    candidate_id:str=Field(pattern=r'^[a-z0-9-]{1,80}$')

class CompositionReviewRequest(BaseModel):
    approved:bool
    feedback:str=Field(default='',max_length=1000)

def local_path(root,path):
    path=Path(path).resolve()
    try:path.relative_to(Path(root).resolve())
    except ValueError:raise ValueError('El archivo está fuera de los datos de GEN STUDIO.')
    return path

def trial(root,name):
    if not isinstance(name,str) or not re.fullmatch(r'[a-z0-9-]{1,80}',name):raise ValueError('Detalle no válido.')
    return local_path(root,Path(root)/'identity-tests'/name)

def resolve(service,parent,candidate):
    folder=trial(service.root,candidate)
    report=json.loads((folder/'video-report.json').read_text(encoding='utf-8'))
    recipe=json.loads((folder/'guided-composition.json').read_text(encoding='utf-8'))
    if not isinstance(report,dict) or not isinstance(recipe,dict):raise ValueError('Registro de detalle no válido.')
    if recipe.get('version')!=1:raise ValueError('Receta de composición no compatible.')
    if report.get('source_job')!=parent['id'] or recipe.get('source_job')!=parent['id']:
        raise ValueError('Este detalle corresponde a otra toma.')
    if not report.get('identity_approved') or not report.get('review_eligible') or report.get('scene_composited') is not False:
        raise ValueError('Primero hay que revisar y aprobar el detalle de cabeza.')
    body_folder=trial(service.root,recipe['body_trial'])
    body_report=json.loads((body_folder/'video-report.json').read_text(encoding='utf-8'))
    if body_report.get('source_job')!=parent['id']:raise ValueError('La escena corresponde a otra toma.')
    paths={'head':local_path(service.root,folder/'corrected.mp4'),
           'body':local_path(service.root,body_folder/'corrected.mp4'),
           'plan':local_path(service.root,folder/'preparation.json')}
    for key,path in paths.items():
        if not path.is_file() or digest(path)!=recipe[key+'_sha256']:
            raise ValueError('Una entrada aprobada cambió; se conserva el registro y no se recompone.')
    plan=json.loads(paths['plan'].read_text(encoding='utf-8'))
    if not isinstance(plan,dict):raise ValueError('Seguimiento no válido.')
    frames=plan.get('frames')
    if type(frames) is not int or not 3<=frames<=124 or not isinstance(plan.get('rois_in_parent_768'),list) or not isinstance(plan.get('expected_faces_in_768'),list) or len(plan['rois_in_parent_768'])!=frames or len(plan['expected_faces_in_768'])!=frames:
        raise ValueError('El detalle no tiene un seguimiento compatible.')
    return {'id':candidate,'name':recipe.get('name','Detalle H3 aprobado'),'frames':frames,
            'head_url':f'/api/identity-tests/{candidate}/video','paths':paths,'recipe':recipe}

def register(app,settings):
    @app.get('/api/jobs/{job_id}/guided-options')
    async def options(job_id:UUID):
        service=app.state.jobs;parent=service.store.job(str(job_id))
        if not parent:raise HTTPException(404,'Trabajo no encontrado.')
        candidates=[]
        if not parent['parameters'].get('operation') and parent.get('result_available'):
            for path in (service.root/'identity-tests').glob('*/guided-composition.json'):
                try:
                    c=await asyncio.to_thread(resolve,service,parent,path.parent.name)
                    candidates.append({k:c[k] for k in ['id','name','frames','head_url']})
                except (ValueError,KeyError,OSError):continue
        return {'enabled':bool(settings.get('guided_composition_enabled') and settings['generation_enabled'] and available(settings)),
                'candidates':candidates}

    @app.post('/api/jobs/{job_id}/guided-compose',status_code=202)
    async def submit(job_id:UUID,body:GuidedRequest):
        if not settings.get('guided_composition_enabled') or not settings['generation_enabled'] or not available(settings):
            raise HTTPException(503,'La recomposición guiada aún no está activada.')
        service=app.state.jobs
        async with service.lock:
            parent=service.store.job(str(job_id))
            if not parent:raise HTTPException(404,'Trabajo no encontrado.')
            if parent['status'] not in ('completed','failed') or not parent.get('result_available') or parent['parameters'].get('operation'):
                raise HTTPException(409,'Elige una generación de escena terminada.')
            try:
                c=await asyncio.to_thread(resolve,service,parent,body.candidate_id)
                payload={**parent['parameters'],'operation':'identity_anchor','anchor_method':'guided_composition',
                         'anchor_parent_id':parent['id'],'anchor_source':str(c['paths']['body']),
                         'anchor_source_name':'Escena conservada · detalle H3 aprobado','anchor_sha256':c['recipe']['body_sha256'],
                         'guided_head':str(c['paths']['head']),'guided_head_sha256':c['recipe']['head_sha256'],
                         'guided_plan':str(c['paths']['plan']),'guided_plan_sha256':c['recipe']['plan_sha256'],
                         'guided_candidate':c['id'],'preserve_face':False}
                child=service.store.create_job(str(uuid4()),str(body.request_key),payload)
            except (ValueError,KeyError,OSError) as error:raise HTTPException(409,str(error)) from error
            service.wake.set();return child

    @app.post('/api/jobs/{job_id}/review-guided-composition')
    async def review(job_id:UUID,body:CompositionReviewRequest):
        if not settings.get('guided_composition_enabled'):
            raise HTTPException(503,'La revisión de composición no está activada.')
        service=app.state.jobs
        async with service.lock:
            job=service.store.job(str(job_id))
            if not job:raise HTTPException(404,'Trabajo no encontrado.')
            p=job['parameters']
            if job['status']!='completed' or p.get('operation')!='identity_anchor' or p.get('anchor_method')!='guided_composition' or not job.get('face_result_available'):
                raise HTTPException(409,'Selecciona una composición guiada terminada.')
            try:
                report=json.loads((service.root/'jobs'/job['id']/'anchor-report.json').read_text())
                for suffix,key in [('.face.mp4','result_sha256'),('.detail.mp4','detail_sha256')]:
                    path=service.root/'results'/(job['id']+suffix)
                    if not path.is_file() or digest(path)!=report[key]:
                        raise ValueError('La copia publicada cambió; no se guarda su revisión.')
                if body.approved:
                    parent=service.store.job(p['anchor_parent_id'])
                    if not parent:raise ValueError('La escena de origen no está registrada.')
                    resolve(service,parent,p['guided_candidate'])
                review={**(job.get('face_review') or {}),'identity_approved':body.approved,
                    'accessory_approved':body.approved,'review_required':not body.approved,
                    'human_review':'approved' if body.approved else 'rejected','human_feedback':body.feedback}
                return service.store.update(job['id'],face_review=review,
                    stage='Composición estable revisada por el usuario' if body.approved else 'Composición generada · Incidencia registrada')
            except (ValueError,KeyError,OSError) as error:
                raise HTTPException(409,str(error)) from error

async def process(service,job):
    p=job['parameters'];folder=service.root/'jobs'/job['id'];folder.mkdir(parents=True,exist_ok=True)
    parent=service.store.job(p['anchor_parent_id'])
    if not parent:raise ValueError('La escena de origen ya no está registrada.')
    # An approval can be withdrawn while a copy waits in the queue.
    resolve(service,parent,p['guided_candidate'])
    files={'body':local_path(service.root,p['anchor_source']), 'head':local_path(service.root,p['guided_head']),
           'plan':local_path(service.root,p['guided_plan'])}
    hashes={'body':p['anchor_sha256'],'head':p['guided_head_sha256'],'plan':p['guided_plan_sha256']}
    for key,path in files.items():
        if digest(path)!=hashes[key]:raise ValueError('Una entrada cambió desde el envío de la copia.')
    results=service.root/'results';results.mkdir(exist_ok=True)
    targets={'result_sha256':results/(job['id']+'.face.mp4'),'detail_sha256':results/(job['id']+'.detail.mp4')}
    checkpoint=folder/'anchor-report.json'
    if checkpoint.is_file():
        report=json.loads(checkpoint.read_text())
        if all(path.is_file() and digest(path)==report[key] for key,path in targets.items()):return report
        raise ValueError('La copia registrada cambió; crea otra copia.')
    run=folder/('guided-'+str(uuid4()));run.mkdir()
    manifest={**{key:str(path) for key,path in files.items()},'hashes':hashes,'run_root':str(run),'parent_pid':os.getpid(),'model_root':service.settings.get('face_models')}
    manifest_path=run/'manifest.json';manifest_path.write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    process=None
    with (run/'stderr.log').open('wb') as errors:
        try:
            process=await asyncio.create_subprocess_exec(service.settings['face_python'],'-u',str(ROOT/'scripts/compose_pose_head.py'),
                '--stable','--manifest',str(manifest_path),stdout=asyncio.subprocess.PIPE,stderr=errors,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),env={**os.environ,'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','PYTHONIOENCODING':'utf-8'})
            while line:=await process.stdout.readline():
                try:
                    event=json.loads(line.decode('utf-8'))
                    if event.get('type')=='progress':service.store.update(job['id'],stage=event['stage'],progress={'value':event['frame'],'max':event['max']})
                except (ValueError,UnicodeDecodeError):pass
            if await process.wait():
                error=run/'error.json'
                raise ValueError(json.loads(error.read_text())['error'] if error.exists() else 'Falló la recomposición; consulta el registro de la copia.')
        finally:
            if process and process.returncode is None:
                process.terminate()
                try:await asyncio.wait_for(process.wait(),5)
                except TimeoutError:process.kill();await process.wait()
    report=json.loads((run/'composition-report.json').read_text())
    report.update(applied_frames=list(range(report['frames'])),skipped_frames=[],detail_sha256=hashes['head'],method='guided_composition')
    for source,dest,key in [(run/'corrected.mp4',targets['result_sha256'],'result_sha256'),(files['head'],targets['detail_sha256'],'detail_sha256')]:
        if dest.exists():
            if digest(dest)!=report[key]:raise ValueError('Ya existe otra copia diferente; se conserva.')
        else:await asyncio.to_thread(shutil.copy2,source,dest)
    checkpoint.write_text(json.dumps(report,indent=2),encoding='utf-8');return report
