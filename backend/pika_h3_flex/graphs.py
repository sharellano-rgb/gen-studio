from .profiles import SIGMAS,SIZES,dimensions,PROFILES,DIRECT_ENGINE

def graph(p,image_name,job_id):
    w,h=dimensions(p)
    def n(kind,**inputs):return {'class_type':kind,'inputs':inputs}
    result = {
        'dit':n('UNETLoader',unet_name='minimax_h3_fl2va_pruned_int8_convrot.safetensors',weight_dtype='default'),
        'lora':n('LoraLoaderModelOnly',model=['dit',0],lora_name='pika_h3_flex_fl2va_v1_bf16.safetensors',strength_model=1.),
        'encoder':n('CLIPLoader',clip_name='qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors',type='minimax',device='default'),
        'video_vae':n('VAELoader',vae_name='minimax_h3_video_vae_int8_convrot.safetensors'),
        'audio_vae':n('VAELoader',vae_name='minimax_h3_audio_vae_fp32.safetensors'),
        'image':n('LoadImage',image=image_name),
        'conditioning':n('MiniMaxH3ImageToVideo',clip=['encoder',0],vae=['video_vae',0],first_frame=['image',0],prompt=p['prompt'],width=w,height=h,length=124),
        'guider':n('BasicGuider',model=['lora',0],conditioning=['conditioning',0]),
        'noise':n('RandomNoise',noise_seed=p['seed']),
        'sampler':n('KSamplerSelect',sampler_name='lcm'),
        'sigmas':n('ManualSigmas',sigmas=SIGMAS),
        'sampling':n('SamplerCustomAdvanced',noise=['noise',0],guider=['guider',0],sampler=['sampler',0],sigmas=['sigmas',0],latent_image=['conditioning',1]),
        'video_decode':n('VAEDecode',samples=['sampling',0],vae=['video_vae',0]),
        'audio_decode':n('VAEDecodeAudio',samples=['sampling',0],vae=['audio_vae',0]),
        'mux':n('CreateVideo',images=['video_decode',0],audio=['audio_decode',0],fps=24.,bit_depth=8,color_space='sRGB',codec='none'),
        'save':n('SaveVideo',video=['mux',0],filename_prefix=f'gen_studio/pika/{job_id}',format='mp4',**{'format.codec':'h264','format.codec.encoding':'re-encode','format.codec.encoding.crf':18.}),
    }

    if PROFILES[p.get('profile','pika-i2va-480p-5s-v1')]['engine']==DIRECT_ENGINE:
        del result['lora']
        result['guider']['inputs']['model']=['dit',0]
        result['sampler']=n('KSamplerSelect',sampler_name='euler')
        result['sigmas']=n('BasicScheduler',model=['dit',0],scheduler='simple',steps=8,denoise=1.)
        result['save']['inputs']['filename_prefix']=f'gen_studio/h3_i2va/{job_id}'
    return result
