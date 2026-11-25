"""Isolated FastH3 Omni pilot using native ComfyUI reference conditioning."""
from . import direct_audio

MODEL='fasth3_omniref_8step_pruned_int8_convrot.safetensors'
ENCODER='qwen3vl_32b_minimax_h3_int8_convrot.safetensors'
REVISION='82eff2a6eee6c962846879f4bab52610a4df24d3'
SOURCE='https://huggingface.co/FastVideo/FastH3-Omni-Comfy/tree/'+REVISION


def graph(payload,video_name,references,job_id,audio=False):
    if payload['steps']!=8 or not 1<=len(references)<=9:
        raise ValueError('FastH3 Omni requiere 8 pasos y entre 1 y 9 referencias.')
    width,height=map(int,payload['resolution'].split('x'))
    def node(kind,**inputs):return {'class_type':kind,'inputs':inputs}
    result={
        '1':node('UNETLoader',unet_name=MODEL,weight_dtype='default'),
        '2':node('MiniMaxH3SigmaShift',model=['chunk',0],shift_video=12.0,shift_audio=3.0),
        'chunk':node('MiniMaxChunkFeedForward',model=['1',0],chunks=2,seq_threshold=4096),
        '3':node('CLIPLoader',clip_name=ENCODER,type='minimax',device='default'),
        '4':node('VAELoader',vae_name='minimax_h3_video_vae_fp16.safetensors'),
        '5':node('VAELoader',vae_name='minimax_h3_audio_vae_fp32.safetensors'),
        '7':node('LoadVideo',file=video_name),
        '17':node('GetVideoComponents',video=['7',0]),
        '8':node('MiniMaxH3ReferenceToVideo',clip=['3',0],vae=['4',0],audio_vae=['5',0],
                 prompt=payload['prompt'],width=width,height=height,length=payload['frames'],ref_image_size='match',
                 **{'ref_videos.ref_video_0':['17',0]}),
        '9':node('BasicGuider',model=['2',0],conditioning=['8',0]),
        # Euler/simple is the local pilot sampler, not a claimed official workflow.
        '10':node('BasicScheduler',model=['2',0],scheduler='simple',steps=8,denoise=1.0),
        '11':node('KSamplerSelect',sampler_name='euler'),
        '12':node('RandomNoise',noise_seed=payload['seed']),
        '13':node('SamplerCustomAdvanced',noise=['12',0],guider=['9',0],sampler=['11',0],sigmas=['10',0],latent_image=['8',1]),
        '14':node('VAEDecode',samples=['13',1],vae=['4',0]),
        '15':node('CreateVideo',images=['14',0],fps=24),
        '16':node('SaveVideo',video=['15',0],filename_prefix=f'gen_studio/{job_id}/fasth3-omni',format='mp4',codec='h264'),
    }
    if audio:result['8']['inputs']['ref_video_audios.ref_video_audio_0']=['17',1]
    for i,name in enumerate(references):
        key=str(30+i);result[key]=node('LoadImage',image=name)
        result['8']['inputs'][f'ref_images.ref_image_{i}']=[key,0]
    if payload.get('omni_experiment')=='character_v15':
        from .character_lora import filename
        result['omni-character'] = node('LoraLoaderModelOnly',model=['1',0],lora_name=filename('v1.5'),strength_model=0.35)
        result['chunk']['inputs']['model']=['omni-character',0]
    return direct_audio.apply(result,payload)
