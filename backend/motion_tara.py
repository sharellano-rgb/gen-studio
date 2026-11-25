"""TARA native temporal inference using a separately named local weight profile."""
import asyncio,json,math
from pathlib import Path
from .workflows import ROOT,check_graph
from .head import digest
from .motion_transfer import cpu,upload
from .guided_head import phase

VERSION='tara-native-alisson-v2'
SOURCE_COMMIT='605b6ed78513db5eb2ec4078742c61f029797e8b'
MAX_FRAMES=120
ATTENTION_BACKEND='comfy kitchen (experimental)'
STEPS=8
PROFILE='alisson-character-v1.1-local-v2'  # Not the author's unpublished motion step-10500 checkpoint.


def dimensions(width,height,quality):
    if not width or not height:raise ValueError('El vídeo no tiene dimensiones válidas.')
    area=1344*768 if quality=='high' else 960*544
    return (max(32,32*round(math.sqrt(area*width/height)/32)),max(32,32*round(math.sqrt(area*height/width)/32)))


def graph(p,names,ident,direction=''):
    from .motion_bunny import frame_count,prompt
    if not 5<=p['frames']<=(141 if p.get('sequence_parent_id') else MAX_FRAMES):raise ValueError('TARA admite pruebas de hasta 5 segundos.')
    def n(kind,**inputs):return {'class_type':kind,'inputs':inputs}
    w,h=map(int,p['resolution'].split('x'))
    g={
      'unet':n('UNETLoader',unet_name='minimax_h3_ref2va_pruned_int8_convrot.safetensors',weight_dtype='default'),
      'turbo':n('LoraLoaderModelOnly',model=['unet',0],lora_name='minimax_h3_dmd_ref2va_8step_turbo_pruned.safetensors',strength_model=1.0),
      'character':n('LoraLoaderModelOnly',model=['turbo',0],lora_name='minimax_h3_character_swap_v1.1_ref2va_fro.safetensors',strength_model=1.0),
      'shift':n('MiniMaxH3SigmaShift',model=['character',0],shift_video=12.0,shift_audio=3.0),
      'temporal':n('GenStudioTaraH3Temporal',model=['shift',0],motion_video_index=1,strength=2.0,window_seconds=0.2,falloff_seconds=0.25,max_penalty=6.0,attention_backend=ATTENTION_BACKEND),
      'clip':n('CLIPLoader',clip_name='qwen3vl_32b_h3_ultra_uncensored_heretic_int8_convrot.safetensors',type='minimax',device='default'),
      'vae':n('VAELoader',vae_name='minimax_h3_video_vae_fp16.safetensors'),
      'picture':n('LoadImage',image=names['reference']),
      'video':n('LoadVideo',file=names['video']),
      'components':n('GetVideoComponents',video=['video',0]),
      'condition':n('MiniMaxH3ReferenceToVideo',clip=['clip',0],vae=['vae',0],prompt='body_swap: '+prompt(p,direction),width=w,height=h,length=frame_count(p['frames']),ref_image_size='match',**{'ref_images.ref_image_0':['picture',0],'ref_videos.ref_video_0':['components',0]}),
      'guider':n('BasicGuider',model=['temporal',0],conditioning=['condition',0]),
      'scheduler':n('BasicScheduler',model=['temporal',0],scheduler='simple',steps=STEPS,denoise=1.0),
      'sampler':n('KSamplerSelect',sampler_name='euler'),
      'noise':n('RandomNoise',noise_seed=p['seed']),
      'sample':n('SamplerCustomAdvanced',noise=['noise',0],guider=['guider',0],sampler=['sampler',0],sigmas=['scheduler',0],latent_image=['condition',1]),
      'decode':n('VAEDecode',samples=['sample',1],vae=['vae',0]),
      'result_video':n('CreateVideo',images=['decode',0],fps=24.0),
      'save':n('SaveVideo',video=['result_video',0],filename_prefix=f'gen_studio/{ident}/tara/result',format='mp4',codec='h264')}
    if names.get('environment_reference'):
        g['environment']=n('LoadImage',image=names['environment_reference'])
        g['condition']['inputs']['ref_images.ref_image_1']=['environment',0]
    return g


async def preflight(service):
    from .alisson_swap import verify_assets
    await asyncio.to_thread(verify_assets)
    check=await check_graph(service.client,service.settings['comfy_url'],graph({'frames':120,'resolution':'544x960','seed':42,'scene_mode':'reference'},{'video':'source.mp4','reference':'character.png'},'preflight'))
    return {**check,'method':'tara','version':VERSION,'maximum_frames':MAX_FRAMES,'source_commit':SOURCE_COMMIT,'weight_profile':PROFILE,'author_10500_weights':False,'attention_backend':ATTENTION_BACKEND,'experimental':True}


async def process(service,job):
    from .motion_bunny import checked_assets
    from .job_cancel import checkpoint,thread_checkpoint
    from .motion_hybrid import verified_copy
    p=job['parameters'];ident=job['id'];folder=service.root/'jobs'/ident;folder.mkdir(parents=True,exist_ok=True)
    if p.get('tara_version')!=VERSION or p.get('tara_profile')!=PROFILE:raise ValueError('Perfil TARA no compatible.')
    from .alisson_swap import verify_assets
    await asyncio.to_thread(verify_assets)
    checkpoint(service,ident);paths=await asyncio.to_thread(checked_assets,service,p)
    service.store.update(ident,stage='Preparando vídeo para TARA-H3')
    ready=await cpu(service,job,'tara_prepare',{'source':str(paths['video']),'source_sha256':p['video_sha256'],'frames':p['frames'],'start_frame':round(p['start_seconds']*24),'sequence_window':bool(p.get('sequence_parent_id')),'resolution':p['resolution']})
    direction=p['direction'].strip()
    if direction:
        from .prompt_language import normalize
        direction=(await normalize(service,job,direction,folder/'tara-direction.json'))['prompt']
    checkpoint(service,ident)
    receipt=folder/'tara-uploads.json'
    if receipt.exists():names=json.loads(receipt.read_text(encoding='utf-8'))
    else:
        names={'video':await upload(service,ready['video'],ident),'reference':await upload(service,paths['reference'],ident)}
        if paths.get('environment_reference'):names['environment_reference']=await upload(service,paths['environment_reference'],ident)
        receipt.write_text(json.dumps(names),encoding='utf-8')
    g=graph(p,names,ident,direction)
    (folder/'tara-graph.json').write_text(json.dumps(g,indent=2),encoding='utf-8')
    result=await phase(service,job,'TARA-H3-native',g,'save','.mp4')
    if result is None:return
    checkpoint(service,ident);service.store.update(ident,stage='Validando TARA y recuperando el audio original')
    final=await cpu(service,job,'tara_finalize',{'source':str(paths['video']),'source_sha256':p['video_sha256'],'frames':p['frames'],'start_frame':round(p['start_seconds']*24),'sequence_window':bool(p.get('sequence_parent_id')),'resolution':p['resolution'],'video':result['path'],'video_sha256':result['sha256']})
    await asyncio.to_thread(checked_assets,service,p)
    target=service.root/'results'/(ident+'.mp4');target.parent.mkdir(exist_ok=True)
    await thread_checkpoint(service,ident,verified_copy,final['video'],target,final['files'][final['video']])
    service.store.update(ident,status='completed',stage='TARA-H3 listo · Revisa identidad y movimiento',result_available=True,progress=None,scene_resolution=p['resolution'],motion_result_sha256=digest(target),motion_audio=final['audio'])
