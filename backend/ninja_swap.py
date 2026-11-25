"""Separate AI Ninja Face/Body Swap method with a persistent mask review stage."""
import asyncio,json,math,shutil,subprocess
from pathlib import Path
from typing import Literal
from uuid import UUID,uuid4
from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel,Field
from .workflows import ROOT
from .head import digest
from .media import probe
from .ninja_limits import MAX_FRAMES,MAX_MODEL_FRAMES,render_frames
from .direct_character import cpu
from .guided_head import phase
from .h3_face_refine import video_info

VERSION='ai-ninja-v1'
class PrepareRequest(BaseModel):
    request_key:UUID
    video_id:UUID
    reference_id:UUID
    recipe:Literal['w1','w2']='w2'
    mode:Literal['head','body']='head'
    start_seconds:float=Field(default=0,ge=0,le=3600,allow_inf_nan=False)
    frames:int=Field(default=120,ge=5,le=MAX_FRAMES,strict=True)
    x:float=Field(ge=0,le=1,allow_inf_nan=False)
    y:float=Field(ge=0,le=1,allow_inf_nan=False)
    seed:int=Field(default=123456,ge=0,le=9007199254740991,strict=True)
class RenderRequest(BaseModel):
    request_key:UUID
    mask_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    head_motion:Literal['original','preserve']='original'

def prompt(mode):
    identity=('Replace only the selected head with the exact facial identity and hair of <Subject 1>.' if mode=='head' else
              'Replace only the selected person with <Subject 1>, preserving the reference face, hair, body appearance and clothing.')
    return ('<Subject 1> is the character represented in <Picture 1>. '+identity+
      ' Follow the source video motion, pose, head orientation, gaze, facial expression, camera movement and timing in every frame. '
      'The reference supplies appearance only; do not copy its background or pose. Keep the other people, room, background color, lighting and existing objects unchanged. '
      'Do not add people, furniture, hands, keyboards or other objects. Maintain the same identity throughout this continuous shot.')

def previous(service,key,request,operation,parent=None):
    with service.store.connect() as db:row=db.execute('SELECT state FROM jobs WHERE request_key=?',(str(key),)).fetchone()
    if not row:return None
    job=json.loads(row[0]);p=job['parameters']
    if p.get('operation')!=operation or p.get('ninja_request')!=request or (parent is not None and p.get('ninja_parent_id')!=parent):
        raise ValueError('Esta clave de petición ya se usó con otros parámetros.')
    return job

def asset_path(service,asset_id,kind):
    asset=service.store.asset(str(asset_id))
    if not asset or asset['kind']!=kind:raise ValueError('Selecciona un vídeo y una imagen de referencia válidos.')
    root=(service.root/'inputs').resolve();path=(root/asset['filename']).resolve()
    if not path.is_relative_to(root) or not path.is_file():raise ValueError('La entrada no está disponible.')
    return path

def geometry(info):
    sw,sh=info['width'],info['height'];scale=min(1,1024/max(sw,sh))
    return max(32,round(sw*scale/32)*32),max(32,round(sh*scale/32)*32)

def graph_params(p):
    # The preparer fits without distortion; map the selected display point through its padding.
    w,h=p['width'],p['height'];sw,sh=p['source_width'],p['source_height'];scale=min(w/sw,h/sh)
    return {**p,'x':(p['x']*sw*scale+(w-sw*scale)/2)/w,'y':(p['y']*sh*scale+(h-sh*scale)/2)/h}

def validate_inputs(service,p):
    src=asset_path(service,p['video_id'],'video');ref=asset_path(service,p['reference_id'],'image')
    for path,key in [(src,'ninja_source_sha256'),(ref,'ninja_reference_sha256')]:
        if digest(path)!=p[key]:raise ValueError('Cambió una entrada de la toma; crea otra preparación.')
    return src,ref

def reviewed(service,parent,expected_sha):
    if not parent or parent['status']!='completed' or parent['parameters'].get('operation')!='ninja_prepare' or not parent.get('ninja_preview_available'):
        raise ValueError('Prepara y revisa primero la máscara de esta persona.')
    if parent.get('ninja_mask_sha256')!=expected_sha:raise ValueError('La máscara seleccionada no corresponde a esta preparación.')
    path=service.root/'jobs'/parent['id']/'ninja-mask.npz'
    if not path.is_file() or digest(path)!=expected_sha:raise ValueError('Cambió la máscara revisada; no se recalcula automáticamente.')
    return path

def register(app,settings):
    @app.get('/api/ninja/options')
    async def options():return {'enabled':bool(settings.get('ninja_enabled') and settings['generation_enabled']),'version':VERSION,'max_frames':MAX_FRAMES,'max_model_frames':MAX_MODEL_FRAMES,'single_pass':True,'extended_from_frames':360,
        'label':'Refinamiento Facial','recipes':['w2'],'modes':['head','body'],'head_motion':['original','preserve'],'source':'https://huggingface.co/AiNinja94/MiniMax-H3/tree/main/Face-%26-Body%20Swap'}
    @app.post('/api/ninja/prepare',status_code=202)
    async def prepare(body:PrepareRequest):
        service=app.state.jobs;request=body.model_dump(mode='json',exclude={'request_key'})
        async with service.lock:
            try:
                old=previous(service,body.request_key,request,'ninja_prepare')
                if old:return old
                if body.recipe!='w2':raise ValueError('La Receta 1 fue retirada. Crea una preparación con la Receta 2.')
                if not settings.get('ninja_enabled') or not settings['generation_enabled']:raise HTTPException(503,'El método Refinamiento Facial aún no está activado.')
                src=asset_path(service,body.video_id,'video');ref=asset_path(service,body.reference_id,'image')
                info=await asyncio.to_thread(probe,src);w,h=geometry(info)
                start=round(body.start_seconds*24)
                if start+body.frames>math.ceil(info['duration']*24):raise ValueError('El tramo seleccionado supera la duración del vídeo.')
                p={**request,'engine':'h3_ninja','operation':'ninja_prepare','ninja_version':VERSION,'ninja_request':request,
                   'width':w,'height':h,'resolution':f'{w}x{h}','source_width':info['width'],'source_height':info['height'],
                   'render_frames':render_frames(body.frames),'start_frame':start,'steps':8,'preserve_face':False,'prompt':prompt(body.mode),
                   'ninja_source_sha256':await asyncio.to_thread(digest,src),'ninja_reference_sha256':await asyncio.to_thread(digest,ref)}
                source_asset=service.store.asset(str(body.video_id)) or {}
                if source_asset.get('source_result_parent_id'):
                    p['source_result_parent_id']=source_asset['source_result_parent_id']
                result=service.store.create_job(str(uuid4()),str(body.request_key),p);service.wake.set();return result
            except (ValueError,OSError,KeyError) as e:raise HTTPException(409,str(e)) from e
    @app.post('/api/jobs/{job_id}/ninja-render',status_code=202)
    async def render(job_id:UUID,body:RenderRequest):
        service=app.state.jobs;request=body.model_dump(mode='json',exclude={'request_key'})
        # Original and omitted preserve the exact request shape of accepted legacy renders.
        if body.head_motion=='original':request.pop('head_motion')
        async with service.lock:
            try:
                old=previous(service,body.request_key,request,'ninja_render',str(job_id))
                if old:return old
                if not settings.get('ninja_enabled') or not settings['generation_enabled']:raise HTTPException(503,'El método Refinamiento Facial aún no está activado.')
                parent=service.store.job(str(job_id))
                if parent and parent['parameters'].get('recipe')!='w2':raise ValueError('Esta receta fue retirada. Crea otra preparación con la Receta 2.')
                await asyncio.to_thread(reviewed,service,parent,body.mask_sha256)
                await asyncio.to_thread(validate_inputs,service,parent['parameters'])
                if body.head_motion=='preserve' and (parent['parameters']['recipe']!='w2' or parent['parameters']['mode']!='head'):
                    raise ValueError('El ajuste de movimiento requiere Receta 2 y Rostro y cabello.')
                p={**parent['parameters'],'operation':'ninja_render','ninja_parent_id':str(job_id),'ninja_request':request,'ninja_mask_sha256':body.mask_sha256,
                   'ninja_head_motion':body.head_motion}
                result=service.store.create_job(str(uuid4()),str(body.request_key),p);service.wake.set();return result
            except (ValueError,OSError,KeyError) as e:raise HTTPException(409,str(e)) from e
    @app.get('/api/jobs/{job_id}/ninja-preview')
    async def preview(job_id:UUID):
        job=app.state.jobs.store.job(str(job_id));path=app.state.jobs.root/'jobs'/str(job_id)/'ninja-preview.mp4'
        if not job or job['parameters'].get('operation')!='ninja_prepare' or not path.is_file():raise HTTPException(404,'La máscara todavía no está disponible.')
        return FileResponse(path,media_type='video/mp4')

async def fetch_artifact(service,descriptor,target,expected_sha=None):
    if target.exists():
        if expected_sha and digest(target)!=expected_sha:raise ValueError('Cambió un archivo guardado de la máscara.')
        return
    r=await service.client.get(service.settings['comfy_url']+'/view',params={k:descriptor[k] for k in ('filename','subfolder','type')},timeout=180);r.raise_for_status()
    tmp=target.with_suffix(target.suffix+'.part');tmp.write_bytes(r.content)
    if expected_sha and digest(tmp)!=expected_sha:raise ValueError('El SHA-256 de la máscara descargada no coincide.')
    tmp.replace(target)

def checked_prepared(folder,p):
    path=folder/'direct-prepare.json'
    if not path.is_file():raise ValueError('Falta la preparación registrada; no se recalcula una máscara revisada.')
    prepared=json.loads(path.read_text(encoding='utf-8'))
    for file,sha in prepared['files'].items():
        if digest(Path(file))!=sha:raise ValueError('Cambió el vídeo o la referencia preparados.')
    if prepared['frames']!=p['render_frames']:raise ValueError('El intervalo preparado cambió.')
    return prepared

async def upload_inputs(service,folder,job,prepared):
    path=folder/'ninja-uploads.json'
    if path.exists():return json.loads(path.read_text(encoding='utf-8'))
    names={}
    for kind,file in [('video',prepared['video']),('reference',prepared['references'][0])]:
        with Path(file).open('rb') as stream:
            result=await service.post(service.settings['comfy_url'],'/upload/image',files={'image':(Path(file).name,stream)},data={'overwrite':'false','subfolder':f'gen_studio/{job["id"]}/ninja'})
        names[kind]='/'.join(x for x in [result.get('subfolder',''),result['name']] if x)
    path.write_text(json.dumps(names),encoding='utf-8');return names

def dismiss_transient_shakes(python,video,detection,frames):
    if not python or not detection['cuts']:return detection
    run=subprocess.run([str(python),str(ROOT/'scripts/ninja_cut_validation.py'),str(video),'-',str(frames)],input=json.dumps(detection['candidates']).encode('utf-8'),capture_output=True,check=True,timeout=90,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    verification=json.loads(run.stdout)
    dismissed=set(verification['dismissed'])
    if not dismissed.issubset(set(detection['cuts'])):raise ValueError('La verificación de cámara devolvió cortes desconocidos.')
    return {**detection,'cuts':[n for n in detection['cuts'] if n not in dismissed],'transient_verification':verification}


def continuous_shot(folder,prepared,p,python=None):
    from .scene_detection import parse_metrics,detect_cuts
    path=folder/'ninja-cuts.json';sha=prepared['files'][prepared['video']]
    if path.exists():
        report=json.loads(path.read_text(encoding='utf-8'))
        if report['source_sha256']!=sha or report['frames']!=p['frames']:raise ValueError('Cambió el análisis de cortes registrado.')
    else:
        run=subprocess.run(['ffmpeg','-hide_banner','-i',prepared['video'],'-vf','scdet=t=0,metadata=print','-an','-f','null','-'],capture_output=True,check=True,timeout=120,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        detection=detect_cuts(parse_metrics(run.stderr),p['frames'])
        detection=dismiss_transient_shakes(python,prepared['video'],detection,p['frames'])
        report={'source_sha256':sha,'frames':p['frames'],'detection':detection}
        path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    if report['detection']['cuts']:raise ValueError('El tramo contiene cortes; selecciona un solo plano continuo para Refinamiento Facial.')
    return report

async def process(service,job):
    from .ninja_graphs import track_graph,render_graph
    p=job['parameters'];folder=service.root/'jobs'/job['id'];folder.mkdir(parents=True,exist_ok=True)
    current=service.store.job(job['id'])
    if current['status']=='submission_unknown':return
    if p.get('ninja_version')!=VERSION:raise ValueError('Versión AI Ninja desconocida; se conserva la toma.')
    src,ref=await asyncio.to_thread(validate_inputs,service,p)
    protected={str(src):p['ninja_source_sha256'],str(ref):p['ninja_reference_sha256']}
    operation=p['operation'];name='ninja-track' if operation=='ninja_prepare' else 'ninja-render'
    accepted=(current.get('guided_phase')==name and current.get('prompt_id')) or (folder/(name+'-complete.json')).exists()
    graph_file=folder/(name+'-workflow.api.json')
    if accepted and (not graph_file.is_file() or not (folder/'ninja-uploads.json').is_file()):
        raise ValueError('Falta un registro de la fase aceptada; se conserva su identificador sin reenviar.')
    if operation=='ninja_prepare':
        if accepted:prepared=checked_prepared(folder,p)
        else:prepared=await cpu(service,job,'prepare',{'source':str(src),'references':[str(ref)],'protected':protected,'width':p['width'],'height':p['height'],
            'frames':p['frames'],'render_frames':p['render_frames'],'start_frame':p['start_frame'],'crop_face':False,'reference_type':'human','scene_cut_policy':'warn'})
        await asyncio.to_thread(continuous_shot,folder,prepared,p,service.settings.get('face_python'))
        names=await upload_inputs(service,folder,job,prepared)
        g=json.loads(graph_file.read_text(encoding='utf-8')) if accepted else track_graph(graph_params(p),names['video'],job['id'])
        result=await phase(service,job,name,g,'save-mask','.json')
        if result is None:return
        audit=json.loads(Path(result['path']).read_text(encoding='utf-8'))
        if (audit['frames'],audit['width'],audit['height'])!=(p['render_frames'],p['width'],p['height']):raise ValueError('El seguimiento no corresponde al tramo.')
        await fetch_artifact(service,audit['bundle'],folder/'ninja-mask.npz',audit['sha256'])
        history=json.loads((folder/'ninja-track-history.json').read_text(encoding='utf-8'))
        descriptors=[i for v in history['outputs']['preview-save'].values() if isinstance(v,list) for i in v if isinstance(i,dict) and i.get('filename','').endswith('.mp4')]
        if len(descriptors)!=1:raise ValueError('Falta una vista previa única de la máscara.')
        await fetch_artifact(service,descriptors[0],folder/'ninja-preview.mp4')
        info=await asyncio.to_thread(video_info,folder/'ninja-preview.mp4')
        if (info['frames'],info['width'],info['height'])!=(p['render_frames'],p['width'],p['height']):raise ValueError('La vista de la máscara está incompleta.')
        await asyncio.to_thread(validate_inputs,service,p)
        return service.store.update(job['id'],status='completed',stage='Máscara de Refinamiento Facial lista · Revisa la persona seleccionada',ninja_preview_available=True,
            ninja_mask_sha256=audit['sha256'],ninja_tracking=audit['selection'],result_available=False,progress=None)
    if operation!='ninja_render':raise ValueError('Operación AI Ninja inválida.')
    parent=service.store.job(p['ninja_parent_id']);bundle_path=await asyncio.to_thread(reviewed,service,parent,p['ninja_mask_sha256'])
    prepared=checked_prepared(service.root/'jobs'/parent['id'],p)
    names=await upload_inputs(service,folder,job,prepared)
    if accepted:
        g=json.loads(graph_file.read_text(encoding='utf-8'))
    else:
        stats=await service.get(service.settings['comfy_url'],'/system_stats');argv=stats['system']['argv']
        if '--input-directory' not in argv:raise ValueError('El motor no declara su carpeta de entrada; no se copia la máscara a una ruta supuesta.')
        input_root=Path(argv[argv.index('--input-directory')+1]).resolve()
        relative=f'gen_studio/{job["id"]}/ninja-mask.npz';destination=input_root/relative
        destination.parent.mkdir(parents=True,exist_ok=True)
        if destination.exists():
            if digest(destination)!=p['ninja_mask_sha256']:raise ValueError('Cambió la máscara entregada al motor.')
        else:shutil.copy2(bundle_path,destination)
        if digest(destination)!=p['ninja_mask_sha256']:raise ValueError('No coincide la copia de la máscara revisada.')
        g=render_graph(graph_params(p),names['video'],names['reference'],job['id'],{'path':relative,'sha256':p['ninja_mask_sha256']})
    result=await phase(service,job,name,g,'save','.mp4')
    if result is None:return
    final=await cpu(service,job,'finalize',{'video':result['path'],'video_sha256':result['sha256'],'source':prepared['video'],'source_sha256':prepared['files'][prepared['video']],
        'frames':p['frames'],'render_frames':p['render_frames'],'width':p['width'],'height':p['height']})
    await asyncio.to_thread(validate_inputs,service,p);await asyncio.to_thread(reviewed,service,parent,p['ninja_mask_sha256'])
    target=service.root/'results'/(job['id']+'.mp4')
    if target.exists() and digest(target)!=final['files'][final['video']]:raise ValueError('Ya existe otra copia diferente; se conserva.')
    if not target.exists():shutil.copy2(final['video'],target)
    return service.store.update(job['id'],status='completed',stage='Refinamiento Facial · Revisión del resultado pendiente',result_available=True,progress=None,
        ninja_result_sha256=digest(target),scene_resolution=p['resolution'])
