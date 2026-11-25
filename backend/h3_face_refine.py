"""Optional H3 facial refinement of a completed Direct take; originals are immutable."""
import asyncio, json, shutil, subprocess, hashlib, re, math
from pathlib import Path
from uuid import UUID, uuid4
from fastapi import HTTPException
from pydantic import BaseModel, Field
from .workflows import ROOT, check_graph
from .head import digest
from .direct_character import cpu
from .guided_head import phase
from .hands_recipe import render_frames
from .character_update import BASE, TURBO, NATIVE_ENCODER
from .media import probe
from . import h3_face_recipe

VERSION='h3-face-refine-v3'
TRACKING_POLICY='skip_ambiguous_v1'
LMS='minimax_h3_lms_v1.0_r64.safetensors'
FACIAL='minimax-h3-facial-realism-closeup-cp2000.safetensors'
PROMPT=('Facial Realism: Refine the tracked face to the exact identity of <Picture 1>. '
    'Preserve the reference facial proportions, eyes, nose, mouth, jaw, age and beard. '
    'Follow the head rotation, expression, gaze, blinking, mouth motion and timing of the source video latent in every frame. '
    'The picture supplies identity only; do not copy its pose, expression, background or framing. '
    'Keep natural matte skin with subtle pores and consistent lighting. Preserve occlusions, headwear and hair motion. '
    'Do not add faces, hands, objects or change clothing. Keep one continuous, temporally coherent shot.')

class Request(BaseModel):
    request_key: UUID
    reference_id: UUID
    start_frame: int=Field(default=0,ge=0,le=720,strict=True)
    frames: int=Field(default=120,ge=5,le=345,strict=True)
    x: int=Field(ge=0,le=8191,strict=True)
    y: int=Field(ge=0,le=8191,strict=True)
    seed: int=Field(default=123456,ge=0,le=9007199254740991)


def source(service,parent):
    if not parent or parent['status']!='completed' or not parent.get('result_available') or parent['parameters'].get('engine')!='h3_direct' or parent['parameters'].get('operation') not in (None,'h3_face_refine'):
        raise ValueError('Selecciona una toma Direct terminada o uno de sus tramos.')
    path=service.root/'results'/(parent['id']+'.mp4')
    expected=parent.get('h3_face_result_sha256') or parent.get('direct_result_sha256')
    if not expected or not path.is_file() or digest(path)!=expected:raise ValueError('La toma de origen cambió o no tiene un resultado verificado.')
    return path


def video_info(path):
    r=subprocess.run(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=width,height,nb_frames,r_frame_rate','-of','json',str(path)],capture_output=True,text=True,check=True,timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    s=json.loads(r.stdout)['streams'][0];a,b=map(int,s['r_frame_rate'].split('/'))
    if a/b!=24:raise ValueError('El refinamiento requiere una toma Direct a 24 fps.')
    return {'width':s['width'],'height':s['height'],'frames':int(s['nb_frames']),'duration':int(s['nb_frames'])/24}


def verify_installation():
    install=json.loads((ROOT/'docs/h3-face-refine/installation.json').read_text())
    for name,sha in install['files'].items():
        if digest(Path(install['path'])/name)!=sha:raise ValueError('Cambió un nodo de refinamiento facial; se necesita revisión.')
    for item in json.loads((ROOT/'docs/h3-face-refine/models.json').read_text()):
        with Path(item['path']).open('rb') as f:actual=hashlib.file_digest(f,'sha256').hexdigest()
        if actual!=item['sha256']:raise ValueError('El SHA-256 de un modelo facial no coincide.')


def track_graph(p,video,job_id):
    def n(kind,**kw):return {'class_type':kind,'inputs':kw}
    count=render_frames(p['frames'])
    return {
      'video':n('LoadVideo',file=video),
      'components':n('GetVideoComponents',video=['video',0]),
      'track':n('H3FaceTrackCrop',images=['components',0],detector='face_yolov8m.pt',confidence=.25,
          crop_factor=2.5,canvas_width=768,canvas_height=768,canvas_mode='manual',smooth_window=21,size_smooth_window=51,
          smooth_method='gaussian',size_mode='per_frame',select='closest_to_xy',select_index=0,X=p['h3_face_x'],Y=p['h3_face_y'],frame_index=0,
          identity_track=True,identity_threshold=.28,identity_model='insightface',fallback_detector='none',fallback_head_frac=.5,
          cut_detection='none',cut_threshold=3,absent_shots='off',
          **({'gen_identity_policy':'skip_ambiguous'} if p.get('h3_face_tracking_policy')==TRACKING_POLICY else {})),
      'guard':n('GenH3FaceGuard',images=['track',0],transform=['track',1],report=['track',3],expected=count,x=p['h3_face_x'],y=p['h3_face_y'],run_id=job_id),
      'preview':n('CreateVideo',images=['track',2],fps=24),
      'preview-save':n('SaveVideo',video=['preview',0],filename_prefix=f'gen_studio/{job_id}/tracking',format='mp4',codec='h264')}


def refine_graph(p,video,reference,job_id):
    def n(kind,**kw):return {'class_type':kind,'inputs':kw}
    g=track_graph(p,video,job_id);g.pop('preview');g.pop('preview-save')
    g.update({
      'model':n('UNETLoader',unet_name=h3_face_recipe.base_model(p),weight_dtype='default'),
      'turbo':n('LoraLoaderModelOnly',model=['model',0],lora_name=TURBO,strength_model=1),
      'lms':n('LoraLoaderModelOnly',model=['turbo',0],lora_name=LMS,strength_model=.55),
      'facial':n('LoraLoaderModelOnly',model=['lms',0],lora_name=FACIAL,strength_model=1),
      'clip':n('CLIPLoader',clip_name=NATIVE_ENCODER,type='minimax',device='default'),
      'vae':n('VAELoader',vae_name='minimax_h3_video_vae_fp16.safetensors'),
      'audio-vae':n('VAELoader',vae_name='minimax_h3_audio_vae_fp32.safetensors'),
      'reference':n('LoadImage',image=reference),
      'condition':n('MiniMaxH3ReferenceToVideo',clip=['clip',0],vae=['vae',0],audio_vae=['audio-vae',0],prompt=PROMPT,
          width=768,height=768,length=render_frames(p['frames']),ref_image_size='max',**{'ref_images.ref_image_0':['reference',0]}),
      'inject':n('H3InjectVideoLatent',av_latent=['condition',1],images=['track',0],vae=['vae',0]),
      'denoise':n('H3PerFrameDenoise',model=['facial',0],av_latent=['inject',0],transform=['guard',0],
          denoise_multiplier_small_face=1,denoise_multiplier_large_face=.35,scale_mode='absolute_px',face_px_small=30,face_px_large=120,gamma=1,smooth_frames=9),
      'guider':n('BasicGuider',model=['denoise',2],conditioning=['condition',0]),
      'scheduler':n('BasicScheduler',model=['denoise',2],scheduler='simple',steps=8,denoise=1),
      'sampler':n('KSamplerSelect',sampler_name='euler'),
      'noise':n('RandomNoise',noise_seed=p['seed']),
      'sample':n('SamplerCustomAdvanced',noise=['noise',0],guider=['guider',0],sampler=['sampler',0],sigmas=['scheduler',0],latent_image=['denoise',0]),
      'decode':n('VAEDecode',samples=['sample',0],vae=['vae',0]),
      'exact':n('GenH3ExactFrames',images=['decode',0],expected=render_frames(p['frames'])),
      'stitch':n('H3FaceStitch',base_images=['components',0],refined_crops=['exact',0],transform=['guard',0],
          paste_region='face_ellipse',mask_dilation=8,feather=6,colour_match=1,blend=1,undetected_frames='skip',feather_scales_with_crop=False),
      'output':n('CreateVideo',images=['stitch',0],fps=24),
      'save':n('SaveVideo',video=['output',0],filename_prefix=f'gen_studio/{job_id}/face-refined',format='mp4',codec='h264')})
    return g



def validate_tracking(audit,expected,*,skip_ambiguous=False,visible_frames=None):
    t=audit['transform'];report=audit['report']
    if t['frames']!=expected or t['source_frames']!=expected or t['source']!=list(range(expected)):
        raise ValueError('El seguimiento no cubre todos los fotogramas preparados.')
    m=re.search(r'tracking: (\d+) by continuity, (\d+) ambiguous \((\d+) resolved',report)
    if not m:raise ValueError('El informe de seguimiento no contiene decisiones verificables.')
    if skip_ambiguous:
        details=t.get('gen_tracking',{})
        if details.get('version')!=1 or details.get('policy')!='skip_ambiguous' or details.get('reacquire_identity') is not True:
            raise ValueError('Falta la auditoría por fotograma del seguimiento conservador.')
        def indices(key):
            value=details.get(key)
            if not isinstance(value,list) or any(type(i) is not int or not 0<=i<expected for i in value) or value!=sorted(set(value)):
                raise ValueError('La auditoría de identidad contiene fotogramas inválidos.')
            return set(value)
        ambiguous,resolved,skipped=(indices(k) for k in ('ambiguous_frames','resolved_frames','skipped_frames'))
        detected=t.get('detected',[])
        if len(detected)!=expected or any(type(v) is not bool for v in detected):raise ValueError('El seguimiento está incompleto.')
        if (resolved&skipped or ambiguous!=resolved|skipped or len(ambiguous)!=int(m[2]) or len(resolved)!=int(m[3])
            or any(detected[i] for i in skipped) or any(not detected[i] for i in resolved)
            or int(m[1])+len(resolved)!=sum(detected)):
            raise ValueError('Una ambigüedad de identidad no quedó excluida del refinamiento.')
        visible=expected if visible_frames is None else visible_frames
        if type(visible) is not int or not 1<=visible<=expected:raise ValueError('Intervalo visible inválido.')
        if not detected[0] or sum(detected[:visible])/visible<.8:
            raise ValueError('El rostro se pierde en más del 20% del tramo visible; acorta la prueba.')
    elif int(m[2])>int(m[3]):
        raise ValueError('El seguimiento no pudo distinguir de forma fiable a las personas.')
    centres=[]
    for box,rect in zip(t['boxes'],t['face_rect']):
        x,y,w,h=box;rx,ry,rw,rh=rect;cw,ch=t['canvas']
        centres.append((x+(rx+rw/2)/cw*w,y+(ry+rh/2)/ch*h,rh/ch*h))
    for a,b in zip(centres,centres[1:]):
        if math.hypot(b[0]-a[0],b[1]-a[1])>max(24,min(a[2],b[2])*.75):
            raise ValueError('La trayectoria del rostro salta; acorta el tramo o vuelve a marcarlo.')


def register(app,settings):
    @app.get('/api/jobs/{job_id}/h3-face-options')
    async def options(job_id:UUID):
        parent=app.state.jobs.store.job(str(job_id))
        try:
            path=await asyncio.to_thread(source,app.state.jobs,parent);info=await asyncio.to_thread(video_info,path)
            return {**info,'enabled':bool(settings.get('h3_face_refine_enabled') and settings['generation_enabled']),
                'max_frames':345,'default_frames':min(120,info['frames']),'default_start':max(0,info['frames']-120),
                'url':f'/api/jobs/{job_id}/result','version':VERSION}
        except (ValueError,OSError,KeyError) as e:raise HTTPException(409,str(e)) from e

    @app.post('/api/jobs/{job_id}/refine-h3-face',status_code=202)
    async def submit(job_id:UUID,body:Request):
        if not settings.get('h3_face_refine_enabled') or not settings['generation_enabled']:raise HTTPException(503,'El refinamiento facial H3 aún no está activado.')
        service=app.state.jobs
        async with service.lock:
            try:
                # Recover the same user request before applying newer version defaults.
                with service.store.connect() as db:
                    previous=db.execute('SELECT state FROM jobs WHERE request_key=?',(str(body.request_key),)).fetchone()
                if previous:
                    existing=json.loads(previous[0]);p=existing['parameters']
                    original={'reference_id':p.get('h3_face_reference_id'),'start_frame':p.get('h3_face_start_frame'),
                              'frames':p.get('frames'),'x':p.get('h3_face_x'),'y':p.get('h3_face_y'),'seed':p.get('seed')}
                    requested=body.model_dump(mode='json',exclude={'request_key'})
                    if p.get('operation')!='h3_face_refine' or p.get('h3_face_parent_id')!=str(job_id) or original!=requested:
                        raise ValueError('Esta clave de petición ya se usó con otros parámetros.')
                    return existing
                parent=service.store.job(str(job_id));path=await asyncio.to_thread(source,service,parent)
                info=await asyncio.to_thread(video_info,path)
                if body.start_frame+body.frames>info['frames'] or body.x>=info['width'] or body.y>=info['height']:raise ValueError('El tramo o el punto quedan fuera del vídeo.')
                asset=service.store.asset(str(body.reference_id))
                if not asset or asset['kind']!='image':raise ValueError('Selecciona una foto del rostro de referencia.')
                ref=service.root/'inputs'/asset['filename']
                p={**parent['parameters'],'operation':'h3_face_refine','h3_face_version':VERSION,'h3_face_tracking_policy':TRACKING_POLICY,'h3_face_parent_id':parent['id'],
                    'h3_face_recipe':h3_face_recipe.RECIPE,'h3_face_base_model':h3_face_recipe.CURVE_BASE,'h3_face_base_sha256':h3_face_recipe.CURVE_SHA256,
                    'h3_face_source_sha256':digest(path),'h3_face_reference_id':asset['id'],'h3_face_reference_sha256':digest(ref),
                    'h3_face_start_frame':body.start_frame,'h3_face_x':body.x,'h3_face_y':body.y,'frames':body.frames,'seed':body.seed,
                    'h3_face_missing_policy':'skip','h3_face_denoise':1.0,'h3_face_large_multiplier':.35,'h3_face_small_multiplier':1.0,
                    'steps':8,'prompt':PROMPT,'preserve_face':False,'resolution':f"{info['width']}x{info['height']}"}
                p.pop('long_parent_id',None);p.pop('long_keep_frames',None)
                result=service.store.create_job(str(uuid4()),str(body.request_key),p);service.wake.set();return result
            except (ValueError,OSError,KeyError) as e:raise HTTPException(409,str(e)) from e

    @app.get('/api/jobs/{job_id}/h3-face-tracking')
    async def preview(job_id:UUID):
        from fastapi.responses import FileResponse
        job=app.state.jobs.store.job(str(job_id));path=app.state.jobs.root/'jobs'/str(job_id)/'face-tracking.mp4'
        if not job or job['parameters'].get('operation')!='h3_face_refine' or not path.is_file():raise HTTPException(404,'Seguimiento aún no disponible.')
        return FileResponse(path,media_type='video/mp4')


async def process(service,job):
    p=job['parameters'];folder=service.root/'jobs'/job['id'];folder.mkdir(parents=True,exist_ok=True)
    current=service.store.job(job['id'])
    if current['status']=='submission_unknown':return
    downstream=(current.get('guided_phase')=='face-refine' and current.get('prompt_id')) or (folder/'face-refine-complete.json').exists()
    if downstream:
        for needed in ['direct-prepare.json','face-uploads.json','face-track-complete.json','face-track-history.json','face-refine-workflow.api.json']:
            if not (folder/needed).is_file():raise ValueError('Falta '+needed+' de una generación facial ya aceptada. Se conserva su prompt_id y no se reenvía.')
    parent=service.store.job(p['h3_face_parent_id']);src=await asyncio.to_thread(source,service,parent)
    asset=service.store.asset(p['h3_face_reference_id']);ref=service.root/'inputs'/asset['filename']
    protected={str(src):p['h3_face_source_sha256'],str(ref):p['h3_face_reference_sha256']}
    for file,sha in protected.items():
        if digest(Path(file))!=sha:raise ValueError('Cambió una entrada del refinamiento facial.')
    await asyncio.to_thread(verify_installation)
    recipe=await asyncio.to_thread(h3_face_recipe.verify,p)
    if recipe:
        (folder/'face-recipe-verification.json').write_text(json.dumps(recipe,indent=2),encoding='utf-8')
        service.store.update(job['id'],h3_face_recipe_verification=recipe)
    w,h=map(int,p['resolution'].split('x'));count=render_frames(p['frames'])
    prepared=await cpu(service,job,'prepare',{'source':str(src),'references':[str(ref)],'protected':protected,'width':w,'height':h,
        'frames':p['frames'],'render_frames':count,'start_frame':p['h3_face_start_frame'],'crop_face':False,'reference_type':'human','scene_cut_policy':'strict'})
    cut_record=folder/'face-cuts.json'
    if not cut_record.exists():
        from .scene_detection import parse_metrics,detect_cuts
        def detect():
            r=subprocess.run(['ffmpeg','-hide_banner','-i',prepared['video'],'-vf','scdet=t=0,metadata=print','-an','-f','null','-'],capture_output=True,check=True,timeout=120,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            return detect_cuts(parse_metrics(r.stderr),p['frames'])
        cut_record.write_text(json.dumps(await asyncio.to_thread(detect)))
    if json.loads(cut_record.read_text())['cuts']:raise ValueError('La selección contiene cortes; refina cada plano por separado.')
    names_path=folder/'face-uploads.json'
    if names_path.exists():names=json.loads(names_path.read_text())
    else:
        names={}
        for kind,file in [('video',prepared['video']),('reference',prepared['references'][0])]:
            with Path(file).open('rb') as stream:
                result=await service.post(service.settings['comfy_url'],'/upload/image',files={'image':(Path(file).name,stream)},data={'overwrite':'false','subfolder':f'gen_studio/{job["id"]}/face'})
            names[kind]='/'.join(x for x in [result.get('subfolder',''),result['name']] if x)
        names_path.write_text(json.dumps(names))
    track=await phase(service,job,'face-track',track_graph(p,names['video'],job['id']),'guard','.json')
    if track is None:return
    audit=json.loads(Path(track['path']).read_text())
    conservative=p.get('h3_face_tracking_policy')==TRACKING_POLICY
    service.store.update(job['id'],h3_face_tracking_report=audit['report'],h3_face_tracking_audit=audit['transform'].get('gen_tracking'))
    preview=folder/'face-tracking.mp4'
    preview_hash=folder/'face-tracking-sha256.txt'
    if preview.exists() and preview_hash.exists() and digest(preview)!=preview_hash.read_text():raise ValueError('Cambió la vista de seguimiento registrada.')
    if not preview.exists() or not preview_hash.exists():
        history=json.loads((folder/'face-track-history.json').read_text())
        descriptors=[i for v in history['outputs']['preview-save'].values() if isinstance(v,list) for i in v if isinstance(i,dict) and i.get('filename','').endswith('.mp4')]
        if len(descriptors)!=1:raise ValueError('No hay una vista de seguimiento única.')
        r=await service.client.get(service.settings['comfy_url']+'/view',params={k:descriptors[0][k] for k in ('filename','subfolder','type')},timeout=180);r.raise_for_status()
        temporary=folder/'face-tracking.part.mp4';temporary.write_bytes(r.content)
        info=await asyncio.to_thread(video_info,temporary)
        if info['frames']!=count:raise ValueError('La vista de seguimiento está incompleta.')
        temporary.replace(preview);preview_hash.write_text(digest(preview))
    validate_tracking(audit,count,skip_ambiguous=conservative,visible_frames=p['frames'])
    graph_path=folder/'face-refine-workflow.api.json'
    current=service.store.job(job['id'])
    accepted=(folder/'face-refine-complete.json').exists() or (current.get('guided_phase')=='face-refine' and current.get('prompt_id'))
    g=json.loads(graph_path.read_text()) if accepted else refine_graph(p,names['video'],names['reference'],job['id'])
    result=await phase(service,job,'face-refine',g,'save','.mp4')
    if result is None:return
    final=await cpu(service,job,'finalize',{'video':result['path'],'video_sha256':result['sha256'],'source':prepared['video'],
        'source_sha256':prepared['files'][prepared['video']],'frames':p['frames'],'render_frames':count,'width':w,'height':h})
    for file,sha in protected.items():
        if digest(Path(file))!=sha:raise ValueError('Una entrada cambió durante el refinamiento.')
    target=service.root/'results'/(job['id']+'.mp4')
    if target.exists() and digest(target)!=final['files'][final['video']]:raise ValueError('Existe otra copia; no se sobrescribe.')
    if not target.exists():shutil.copy2(final['video'],target)
    service.event(job['id'],'Pasada facial H3 terminada en una copia. Audio del tramo original conservado; revisa identidad, gestos y bordes.')
    return service.store.update(job['id'],status='completed',stage='Refinamiento facial H3 · Revisión pendiente',result_available=True,progress=None,
        h3_face_result_sha256=digest(target),h3_face_review={'approved':False,'review_required':True},scene_resolution=p['resolution'])
