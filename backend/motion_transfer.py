"""Reviewed pose-frame preparation and local Viggle motion transfer."""
import asyncio,json,os,re,shutil,subprocess
import httpx
from pathlib import Path
from typing import Literal
from uuid import UUID,uuid4
from fastapi import HTTPException
from fastapi.responses import FileResponse,JSONResponse
from pydantic import BaseModel,Field
from .workflows import ROOT,check_graph
from .head import digest
from .guided_head import phase
from .motion_limits import MOTION_MAX_FRAMES, VIGGLE_MAX_FRAMES

VENDOR=ROOT/'workflows/vendor/viggle/video_viggle_animate_h3.template.json'
VENDOR_SHA='7bd7cd6d8179dc7a809730ef734d4fdc3bbec47aca6f01152c612b4fd8d43d64'
MODELS={'unet':'minimax_h3_ref2va_viggle_pruned_int8_convrot.safetensors','viggle_lora':'viggle_animate_dmd_lora_r64.safetensors',
 'text_cond':'fixed_embed_fwd_anyframe.safetensors','vae':'minimax_h3_video_vae_int8_convrot.safetensors',
 'klein_unet':'flux2Klein9bFp8_fp8.safetensors','klein_vae':'flux2-vae.safetensors','clip':'qwen_3_8b_fp8mixed.safetensors'}

class AnchorRequest(BaseModel):
    request_key:UUID
    video_id:UUID
    reference_id:UUID|None=None
    environment_reference_id:UUID|None=None
    scene_mode:Literal['character','restyle']='character'
    original_reference:bool=Field(default=False,strict=True)
    anchor_editor:Literal['klein','qwen_image_21']='klein'
    qwen_recipe:Literal['legacy','pose_v2']='pose_v2'
    anchor_pose_notes:str=Field(default='',max_length=1200)
    frames:int=Field(default=73,ge=5,le=MOTION_MAX_FRAMES,strict=True)
    start_seconds:float=Field(default=0,ge=0,le=30,allow_inf_nan=False)
    anchor_index:int=Field(default=0,ge=0,le=MOTION_MAX_FRAMES-1,strict=True)
    resolution:Literal['480x864','864x480','640x1152','1152x640','768x1344','1344x768','512x512']='480x864'
    seed:int=Field(default=42,ge=0,le=9007199254740991)
    direction:str=Field(min_length=10,max_length=2000)
    style_anchor_id:UUID|None=None
    style_anchor_sha256:str|None=Field(default=None,pattern='^[a-f0-9]{64}$')

class AnimateRequest(BaseModel):
    request_key:UUID
    approved_anchor_sha256:str=Field(pattern='^[a-f0-9]{64}$')


def template():
    if digest(VENDOR)!=VENDOR_SHA:raise ValueError('Cambió la copia del workflow Motion Transfer; se conserva el original.')
    values={**MODELS,'ref_video_1':'motion.mp4','ref_image_1':'character.png','width':480,'height':864,
            'seed':42,'prompt':'Replace the source character with the reference character.','filename_prefix':'gen_studio/preflight/motion'}
    source=VENDOR.read_text(encoding='utf-8')
    for key,value in values.items():
        quoted='"{{'+key+'}}"';source=source.replace(quoted,json.dumps(value))
        source=source.replace('{{'+key+'}}',json.dumps(value))
    if '{{' in source:raise ValueError('Quedan parámetros desconocidos en el workflow de movimiento.')
    return json.loads(source)


def subset(graph,output):
    result={}
    def add(key):
        if key in result:return
        result[key]=graph[key]
        for value in graph[key]['inputs'].values():
            if isinstance(value,list) and len(value)==2 and isinstance(value[0],str) and value[0] in graph:add(value[0])
    add(output);return result


def anchor_graph(p,frame,reference,ident,environment=None):
    if p.get('environment_reference_id') and not environment:
        raise ValueError('Falta cargar la imagen de escenario vinculada a esta guía.')
    if environment and (p.get('scene_mode')!='restyle' or not reference or p.get('original_reference') or p.get('style_anchor_id')):
        raise ValueError('La imagen de escenario requiere escena completa y una referencia de personaje independiente.')
    if p.get('anchor_editor')=='qwen_image_21':
        from .motion_qwen import graph
        return graph(p,frame,reference,ident,environment)
    g=template()
    g['frame0']={'class_type':'LoadImage','inputs':{'image':frame}}
    g['char_load']['inputs']={'image':reference}
    g['swap_head']['inputs']['string_b']=p['direction']
    if p.get('anchor_pose_notes'):g['swap_head']['inputs']['string_b']+='\nStill-frame pose notes: '+p['anchor_pose_notes']
    g['swap_prompt']['inputs']['string_b']='Image 1 defines the exact pose, limb positions, object contacts, room, camera and light. Image 2 supplies the target character identity and appearance only. Preserve image 1 framing and size, and keep each hand or paw in contact with its original object.'
    if p.get('scene_mode')=='restyle':
        g['swap_head']['inputs']['string_a']='Reinterpret the entire source image in the requested visual style.'
        g['swap_prompt']['inputs']['string_b']='Image 1 defines composition, perspective, character count, silhouettes, exact pose, limb positions and object contacts. Preserve these spatial relationships and image dimensions. Repaint all characters, clothing, objects and environment consistently in the requested style. Do not preserve drawn textures or outlines when photorealism is requested.'
        if reference:
            g['swap_prompt']['inputs']['string_b']+=' Image 2 is an appearance reference; it must not replace the source pose, framing or composition.'
        else:
            # One image defines the scene; no unrelated character photograph is required.
            g['klein_guider']['inputs'].update(positive=['pos_ref1',0],negative=['neg_ref1',0])
    if environment:
        from .motion_qwen import three_image_prompt
        g['swap_head']['inputs']['string_a']='Build one replacement frame from three independent image roles.'
        g['swap_head']['inputs']['string_b']=three_image_prompt(p)
        g['swap_prompt']['inputs']['string_b']='Render one integrated scene with exactly one replacement character in the source pose and scale, inside the completely new destination environment.'
        g['environment_load']={'class_type':'LoadImage','inputs':{'image':environment}}
        g['environment_scale']={'class_type':'ImageScaleToTotalPixels','inputs':{
            'image':['environment_load',0],'upscale_method':'lanczos','megapixels':1.0,'resolution_steps':1}}
        g['enc_environment']={'class_type':'VAEEncode','inputs':{
            'pixels':['environment_scale',0],'vae':['klein_vae',0]}}
        for kind in ('pos','neg'):
            g[kind+'_ref3']={'class_type':'ReferenceLatent','inputs':{
                'conditioning':[kind+'_ref2',0],'latent':['enc_environment',0]}}
        g['klein_guider']['inputs'].update(positive=['pos_ref3',0],negative=['neg_ref3',0])
    g['klein_noise']['inputs']['noise_seed']=p['seed']
    g['save_anchor']={'class_type':'SaveImage','inputs':{'images':['repaint',0],'filename_prefix':f'gen_studio/{ident}/motion-anchor'}}
    return subset(g,'save_anchor')


def transfer_graph(p,video,anchor,ident):
    def n(kind,**inputs):return {'class_type':kind,'inputs':inputs}
    w,h=map(int,p['resolution'].split('x'))
    return {
      'load_video':n('LoadVideo',file=video),'parts':n('GetVideoComponents',video=['load_video',0]),
      'anchor':n('LoadImage',image=anchor),'unet':n('UNETLoader',unet_name=MODELS['unet'],weight_dtype='default'),
      'lora':n('LoraLoaderModelOnly',model=['unet',0],lora_name=MODELS['viggle_lora'],strength_model=1),
      'model':n('MiniMaxH3SigmaShift',model=['lora',0],shift_video=3,shift_audio=3),
      'vae':n('VAELoader',vae_name=MODELS['vae']),'text':n('ViggleTextCondLoader',text_cond=MODELS['text_cond']),
      'conditioning':n('ViggleAnimateConditioningWindowed',cond_video=['parts',0],ref_image=['anchor',0],text_cond=['text',0],
        vae=['vae',0],width=w,height=h,chunk_frames=124,overlap_frames=22,continuation='five_frame_anchor'),
      'guider':n('BasicGuider',model=['model',0],conditioning=['conditioning',1]),
      'sampler':n('KSamplerSelect',sampler_name='euler'),
      'sigmas':n('ManualSigmas',sigmas='1.0, 0.8571428571428571, 0.6, 0.0'),
      'sample':n('ViggleChunkedSampler',guider=['guider',0],sampler=['sampler',0],sigmas=['sigmas',0],cond_set=['conditioning',0],
        vae=['vae',0],seed=p['seed'],rerender_chunk=0,rerender_seed=0),
      'video':n('CreateVideo',images=['sample',0],fps=24),
      'save_video':n('SaveVideo',video=['video',0],filename_prefix=f'gen_studio/{ident}/motion-transfer',format='mp4',codec='h264')}


async def cpu(service,job,mode,inputs,checkpoint_name=None):
    folder=service.root/'jobs'/job['id'];folder.mkdir(parents=True,exist_ok=True)
    checkpoint=folder/(f'motion-{checkpoint_name or mode}.json')
    if checkpoint.exists():
        report=json.loads(checkpoint.read_text())
        for path,sha in report['files'].items():
            if digest(Path(path))!=sha:raise ValueError('Cambió una salida de movimiento preparada; crea otra copia.')
        return report
    run=folder/(mode+'-'+str(uuid4()));run.mkdir()
    manifest=run/'manifest.json';manifest.write_text(json.dumps({'mode':mode,'run_root':str(run),'parent_pid':os.getpid(),**inputs}),encoding='utf-8')
    with (run/'stderr.log').open('wb') as error_log:
        python=service.settings.get('face_python') if mode.startswith('layers_') else str(ROOT/'.venv/Scripts/python.exe')
        if not python or not Path(python).is_file():raise ValueError('El entorno de procesamiento de vídeo no está disponible.')
        process=await asyncio.create_subprocess_exec(str(python),str(ROOT/'scripts/motion_media.py'),str(manifest),
            stdout=asyncio.subprocess.DEVNULL,stderr=error_log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            if await process.wait():
                error=run/'error.json';raise ValueError(json.loads(error.read_text())['error'] if error.exists() else 'Falló la preparación de movimiento.')
        finally:
            if process.returncode is None:
                if job['parameters'].get('operation') in ('motion_hybrid','motion_anchor','motion_bunny','motion_layers'):
                    from .process_cleanup import stop_process_tree
                    await stop_process_tree(process)
                else:process.terminate();await process.wait()
    report=json.loads((run/'report.json').read_text());checkpoint.write_text(json.dumps(report,indent=2),encoding='utf-8');return report


async def upload(service,path,ident):
    with Path(path).open('rb') as f:
        item=await service.post(service.settings['comfy_url'],'/upload/image',files={'image':(Path(path).name,f)},
            data={'overwrite':'false','subfolder':f'gen_studio/{ident}/motion'})
    return '/'.join(x for x in (item.get('subfolder',''),item['name']) if x)


def environment_reference(service,p):
    ident=p.get('environment_reference_id');sha=p.get('environment_reference_sha256')
    if not ident:
        if sha:raise ValueError('La referencia de escenario no conserva su identificador.')
        return None
    if p.get('scene_mode')!='restyle' or not p.get('reference_id') or p.get('original_reference') or p.get('style_anchor_id'):
        raise ValueError('La imagen de escenario requiere escena completa y una referencia de personaje independiente.')
    image=service.store.asset(ident)
    if not image or image['kind']!='image':raise ValueError('La imagen de escenario no está disponible en este espacio.')
    from .guided_composition import local_path
    path=local_path(service.root/'inputs',service.root/'inputs'/image['filename'])
    if not sha or (image.get('sha256') and image['sha256']!=sha) or not path.is_file() or digest(path)!=sha:
        raise ValueError('La imagen de escenario cambió; prepara y revisa una nueva guía.')
    return path


def inputs(service,p):
    video=service.store.asset(p['video_id']);image=service.store.asset(p['reference_id']) if p.get('reference_id') else None
    if not video or video['kind']!='video' or (p.get('reference_id') and (not image or image['kind']!='image')):raise ValueError('No se encuentra una entrada de movimiento válida.')
    source=service.root/'inputs'/video['filename'];reference=service.root/'inputs'/image['filename'] if image else None
    if digest(source)!=p['source_sha256'] or (reference and digest(reference)!=p['reference_sha256']):raise ValueError('Una entrada de movimiento cambió; se conservan las copias anteriores.')
    if p.get('reference_id') and reference is None:raise ValueError('No se encuentra la referencia de movimiento.')
    environment_reference(service,p)
    return source,reference


def style_reference(service,p):
    if not p.get('style_anchor_id'):return None
    from .motion_h3 import reviewed_view
    job,path=reviewed_view(service,p['style_anchor_id'],p['style_anchor_sha256'])
    if any(job['parameters'].get(k)!=p.get(k) for k in ('video_id','source_sha256','frames','start_seconds','resolution','reference_id','reference_sha256','scene_mode')):
        raise ValueError('La referencia de estilo pertenece a otro tramo.')
    return path


async def process(service,job):
    if job['parameters'].get('operation')=='motion_hybrid':
        from .motion_hybrid import process as hybrid_process
        return await hybrid_process(service,job)
    if job['parameters'].get('transfer_method')=='h3_multiview':
        from .motion_h3 import process as h3_process
        return await h3_process(service,job)
    p=job['parameters'];source,reference=inputs(service,p)
    environment=environment_reference(service,p)
    if p['operation']=='motion_anchor':
        service.store.update(job['id'],stage='Preparando el fotograma guía y el tramo de movimiento')
        ready=await cpu(service,job,'prepare',{'source':str(source),'source_sha256':p['source_sha256'],'frames':p['frames'],
            'start_frame':round(p['start_seconds']*24),'anchor_index':p['anchor_index'],'resolution':p['resolution']})
        if p.get('original_reference'):
            if not reference or p.get('style_anchor_id'):raise ValueError('Selecciona una foto original sin ángulos regenerados.')
            target=service.root/'jobs'/job['id']/('original-reference'+reference.suffix)
            if target.exists() and digest(target)!=p['reference_sha256']:raise ValueError('Cambió la copia de la referencia original.')
            if not target.exists():shutil.copyfile(reference,target)
            inputs(service,p)
            if digest(target)!=p['reference_sha256']:raise ValueError('La copia de la foto no coincide con el original.')
            service.store.update(job['id'],status='completed',stage='Foto original preparada · Revisa apariencia antes de animar',
                progress=None,motion_anchor_available=True,motion_anchor_sha256=digest(target),motion_anchor_path=str(target))
            return
        style=style_reference(service,p)
        reference=style or reference
        edit_parameters=p
        if p.get('anchor_editor')=='qwen_image_21' and p.get('qwen_recipe')=='pose_v2' and not p.get('anchor_pose_notes','').strip():
            from .motion_pose import describe_source
            pose=await describe_source(service,job,ready['frame'])
            edit_parameters={**p,'anchor_pose_notes':pose['notes']}
        names_file=service.root/'jobs'/job['id']/'motion-uploads.json'
        def save_names():
            temporary=names_file.with_name(names_file.name+'.part')
            temporary.write_text(json.dumps(names),encoding='utf-8')
            temporary.replace(names_file)
        if names_file.exists():names=json.loads(names_file.read_text())
        else:
            names={'environment_sha256':p['environment_reference_sha256']} if environment else {}
            save_names()
        if environment and names.get('environment_sha256')!=p['environment_reference_sha256']:
            raise ValueError('La carga guardada no corresponde a la imagen de escenario.')
        if not environment and names.get('environment'):
            raise ValueError('La carga guardada incluye otra imagen de escenario.')
        for key,path in (('frame',ready['frame']),('reference',reference),('environment',environment)):
            if key=='environment' and not environment:continue
            if key not in names:
                inputs(service,p)
                names[key]=await upload(service,path,job['id']) if path else None
                inputs(service,p)
                if key=='environment':names['environment_sha256']=p['environment_reference_sha256']
                save_names()
        inputs(service,p)
        if p.get('anchor_editor')=='qwen_image_21':
            service.store.update(job['id'],stage='Preparando fotograma con Qwen Image Edit 2.1')
        if environment and (not isinstance(names.get('environment'),str) or not names['environment'].strip()):
            raise ValueError('La carga guardada no conserva la imagen de escenario.')
        prepared_graph=anchor_graph(edit_parameters,names['frame'],names['reference'],job['id'],names.get('environment'))
        if environment:
            saved_graph=names_file.parent/'motion-anchor-workflow.api.json'
            if saved_graph.exists():
                previous=json.loads(saved_graph.read_text(encoding='utf-8'))
                keys=('sgopt_ref_image_3__load','sg_encode') if p.get('anchor_editor')=='qwen_image_21' else ('environment_load','environment_scale','enc_environment','pos_ref3','neg_ref3','klein_guider')
                if any(previous.get(key)!=prepared_graph.get(key) for key in keys):
                    raise ValueError('El workflow guardado no conserva la referencia de escenario revisada.')
        result=await phase(service,job,'motion-anchor',prepared_graph,'save_anchor','.png')
        if result is None:return
        report=await cpu(service,job,'validate_anchor',{'anchor':result['path'],'sha256':result['sha256'],'resolution':p['resolution']})
        style_reference(service,p)
        inputs(service,p)
        service.store.update(job['id'],status='completed',stage='Escena preparada · Revisa estilo, pose y objetos' if p.get('scene_mode')=='restyle' else 'Fotograma preparado · Revisa identidad, pose y objetos',
            progress=None,motion_anchor_available=True,motion_anchor_sha256=result['sha256'],motion_anchor_path=result['path'])
    else:
        anchor=service.store.job(p['motion_anchor_id'])
        if not anchor or anchor.get('motion_anchor_sha256')!=p['anchor_sha256'] or digest(Path(anchor['motion_anchor_path']))!=p['anchor_sha256']:
            raise ValueError('Cambió el fotograma revisado; no se anima otra imagen.')
        ready=json.loads((service.root/'jobs'/anchor['id']/'motion-prepare.json').read_text())
        if digest(Path(ready['video']))!=ready['files'][ready['video']]:raise ValueError('Cambió el tramo de movimiento preparado.')
        names_file=service.root/'jobs'/job['id']/'motion-uploads.json';names_file.parent.mkdir(parents=True,exist_ok=True)
        if names_file.exists():names=json.loads(names_file.read_text())
        else:
            names={'video':await upload(service,ready['video'],job['id']),'anchor':await upload(service,anchor['motion_anchor_path'],job['id'])}
            names_file.write_text(json.dumps(names),encoding='utf-8')
        result=await phase(service,job,'motion-viggle',transfer_graph(p,names['video'],names['anchor'],job['id']),'save_video','.mp4')
        if result is None:return
        final=await cpu(service,job,'finalize',{'video':result['path'],'video_sha256':result['sha256'],'audio_source':ready['video'],
            'audio_source_sha256':ready['files'][ready['video']],'frames':p['frames'],'resolution':p['resolution']})
        target=service.root/'results'/(job['id']+'.mp4');target.parent.mkdir(exist_ok=True)
        if target.exists() and digest(target)!=final['files'][final['video']]:raise ValueError('Existe otro resultado; no se sobrescribe.')
        if not target.exists():shutil.copy2(final['video'],target)
        inputs(service,p)
        service.store.update(job['id'],status='completed',stage='Motion Transfer listo · Revisa movimiento y uniones',result_available=True,
            progress=None,scene_resolution=p['resolution'],motion_result_sha256=digest(target),motion_audio=final['audio'])


def register(app,settings):
    @app.get('/api/workflows/motion-transfer/preflight')
    async def preflight(anchor_editor:Literal['klein','qwen_image_21']='klein',qwen_recipe:Literal['legacy','pose_v2']='pose_v2'):
        try:
            p={'resolution':'480x864','seed':42,'direction':'Replace the performer with the reference character.','anchor_editor':anchor_editor,'qwen_recipe':qwen_recipe}
            if anchor_editor=='qwen_image_21':
                from .motion_qwen import VENDOR as qwen_vendor,VENDOR_SHA as qwen_sha
                valid_vendor=digest(qwen_vendor)==qwen_sha
                skipped={('sgopt_ref_image_1__load','image'),('sgopt_ref_image_2__load','image')}
            else:
                valid_vendor=digest(VENDOR)==VENDOR_SHA
                skipped={('frame0','image'),('char_load','image')}
            a=await check_graph(app.state.client,settings['comfy_url'],anchor_graph(p,'frame.png','ref.png','preflight'),valid_vendor,
                                skip_inputs=skipped)
            b=await check_graph(app.state.client,settings['comfy_url'],transfer_graph(p,'video.mp4','anchor.png','preflight'),digest(VENDOR)==VENDOR_SHA,
                                skip_inputs={('load_video','file'),('anchor','image')})
            from .motion_qwen import PROFILE_STEPS
            return {'ok':a['ok'] and b['ok'],'anchor':a,'transfer':b,'anchor_editor':anchor_editor,'qwen_recipe':qwen_recipe if anchor_editor=='qwen_image_21' else None,'anchor_steps':PROFILE_STEPS[qwen_recipe] if anchor_editor=='qwen_image_21' else 4,'maximum_seconds':15,'models':MODELS,'vendor_sha256':digest(VENDOR)}
        except (httpx.HTTPError,ValueError) as error:
            return JSONResponse({'ok':False,'detail':'No se pudo comprobar Motion Transfer. Revisa ComfyUI y vuelve a comprobar.'},status_code=503)

    @app.post('/api/motion-transfer/anchors',status_code=202)
    async def create_anchor(body:AnchorRequest):
        if not settings['generation_enabled']:raise HTTPException(503,'Generación desactivada.')
        service=app.state.jobs;p=body.model_dump(mode='json');key=p.pop('request_key')
        if p['anchor_editor']=='klein':p.pop('anchor_editor')
        if p.get('anchor_editor')!='qwen_image_21' or p['original_reference']:p.pop('qwen_recipe')
        elif p['qwen_recipe']=='legacy':p.pop('qwen_recipe')
        if not p['anchor_pose_notes'].strip():p.pop('anchor_pose_notes')
        video=service.store.asset(p['video_id']);image=service.store.asset(p['reference_id']) if p['reference_id'] else None
        if not video or video['kind']!='video' or (p['reference_id'] and (not image or image['kind']!='image')) or (p['scene_mode']=='character' and not image):raise HTTPException(400,'Selecciona vídeo e imagen válidos.')
        if p['environment_reference_id']:
            if p['scene_mode']!='restyle' or not image or p['original_reference'] or p['style_anchor_id']:
                raise HTTPException(400,'La imagen de escenario requiere escena completa y una referencia de personaje independiente.')
            environment=service.store.asset(p['environment_reference_id'])
            if not environment or environment['kind']!='image':
                raise HTTPException(400,'Selecciona una imagen de escenario de este espacio.')
            try:
                from .guided_composition import local_path
                environment_path=local_path(service.root/'inputs',service.root/'inputs'/environment['filename'])
                p['environment_reference_sha256']=await asyncio.to_thread(digest,environment_path)
                await asyncio.to_thread(environment_reference,service,p)
            except (ValueError,OSError) as error:raise HTTPException(409,str(error)) from error
        else:p.pop('environment_reference_id')
        if p['original_reference']:
            if not image or p['style_anchor_id']:raise HTTPException(400,'La referencia directa requiere una imagen original, sin ángulos regenerados.')
        else:p.pop('original_reference')
        # Keep the default payload compatible with requests made before the experimental mode.
        if p['scene_mode']=='character':p.pop('scene_mode')
        if p['anchor_index']>=p['frames'] or round(p['start_seconds']*24)+p['frames']>round(video['duration']*24)+1:raise HTTPException(400,'El fotograma o tramo no cabe en el vídeo.')
        from .media import source_resolution
        p['resolution']=source_resolution(video,p['resolution'])
        from .motion_qwen import PROFILE_STEPS
        p.update(engine='viggle_motion',operation='motion_anchor',steps=0 if p.get('original_reference') else PROFILE_STEPS[p.get('qwen_recipe','legacy')] if p.get('anchor_editor')=='qwen_image_21' else 4,source_sha256=await asyncio.to_thread(digest,service.root/'inputs'/video['filename']),
            reference_sha256=await asyncio.to_thread(digest,service.root/'inputs'/image['filename']) if image else None)
        if bool(p['style_anchor_id'])!=bool(p['style_anchor_sha256']):raise HTTPException(400,'La referencia de estilo requiere identificador y hash revisado.')
        if p['style_anchor_id']:
            if p.get('scene_mode')!='restyle':raise HTTPException(400,'Los ángulos de escena requieren el modo de escena completa.')
            try:await asyncio.to_thread(style_reference,service,p)
            except (ValueError,OSError) as error:raise HTTPException(409,str(error)) from error
        else:
            p.pop('style_anchor_id');p.pop('style_anchor_sha256')
        try:
            async with service.lock:
                await asyncio.to_thread(environment_reference,service,p)
                job=service.store.create_job(str(uuid4()),key,p)
        except (ValueError,OSError) as error:raise HTTPException(409,str(error)) from error
        service.wake.set();return job

    @app.get('/api/jobs/{job_id}/motion-anchor')
    async def show_anchor(job_id:UUID,source:bool=False):
        service=app.state.jobs;job=service.store.job(str(job_id))
        if not job or job['parameters'].get('operation')!='motion_anchor':raise HTTPException(404,'Fotograma no encontrado.')
        if source:
            checkpoint=service.root/'jobs'/job['id']/'motion-prepare.json'
            if not checkpoint.exists():raise HTTPException(409,'Fotograma aún en preparación.')
            report=json.loads(checkpoint.read_text());path=Path(report['frame']);sha=report['files'][str(path)]
        else:
            if not job.get('motion_anchor_available'):raise HTTPException(409,'Espera al fotograma preparado.')
            path=Path(job['motion_anchor_path']);sha=job['motion_anchor_sha256']
        if not path.is_file() or digest(path)!=sha:raise HTTPException(409,'La imagen registrada cambió.')
        return FileResponse(path,media_type={'.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp'}.get(path.suffix.lower(),'image/png'))

    @app.post('/api/jobs/{job_id}/motion-transfer',status_code=202)
    async def animate(job_id:UUID,body:AnimateRequest):
        if not settings['generation_enabled']:raise HTTPException(503,'Generación desactivada.')
        service=app.state.jobs
        async with service.lock:
            anchor=service.store.job(str(job_id))
            if not anchor or anchor['parameters'].get('operation')!='motion_anchor' or anchor['status']!='completed' or not anchor.get('motion_anchor_available'):
                raise HTTPException(409,'Primero prepara y revisa el fotograma.')
            if anchor['motion_anchor_sha256']!=body.approved_anchor_sha256 or digest(Path(anchor['motion_anchor_path']))!=body.approved_anchor_sha256:
                raise HTTPException(409,'El fotograma revisado cambió; vuelve a comprobarlo.')
            if anchor['parameters']['frames']>VIGGLE_MAX_FRAMES:
                raise HTTPException(400,'Para tomas de más de 15 segundos utiliza H3 con tramos unidos.')
            if anchor['parameters'].get('environment_reference_id'):raise HTTPException(400,'La guía con imagen de escenario requiere H3 · Escena completa.')
            if anchor['parameters'].get('original_reference'):raise HTTPException(400,'La foto original directa requiere H3.')
            p={**anchor['parameters'],'operation':'motion_transfer','transfer_method':'viggle','steps':3,'motion_anchor_id':anchor['id'],'anchor_sha256':body.approved_anchor_sha256}
            try:result=service.store.create_job(str(uuid4()),str(body.request_key),p)
            except ValueError as error:raise HTTPException(409,str(error)) from error
        service.wake.set();return result

