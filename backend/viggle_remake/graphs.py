"""Versioned graphs derived from official Comfy examples; merged INT8 means no second LoRA."""
from pathlib import PurePosixPath
from .profiles import VERSION,LENGTHS,SIZES,MODELS,SIGMAS
SECTIONS=('subject_definitions','summary','retention_analysis','detailed_description','overall_soundscape','non_diegetic_music')

def relative(value):
    if not isinstance(value,str) or not value or '\\' in value or ':' in value or value.startswith('/') or '..' in PurePosixPath(value).parts:
        raise ValueError('Se requiere un nombre de entrada relativo de ComfyUI.')
    return value

def graph(*,mode,source,references,prompt,frames=124,short_edge=736,seed=0,pins=(),prefix=None,output_prefix):
    if mode not in MODELS or type(frames) is not int or frames not in LENGTHS or short_edge not in SIZES:raise ValueError('Perfil Viggle no compatible.')
    if type(seed) is not int or not 0<=seed<=9007199254739991:raise ValueError('Semilla no válida.')
    if not 1<=len(references)<=4 or len(pins) not in (0,len(references)):raise ValueError('Referencias o pins no válidos.')
    if any(type(x) is not int or not 0<=x<frames for x in pins):raise ValueError('Pin fuera de la ventana local.')
    if not isinstance(prompt,str) or len(prompt)>12000 or not all(s+':' in prompt for s in SECTIONS) or '<Video 1>' not in prompt:raise ValueError('Falta el prompt estructurado de seis secciones y sus roles nativos.')
    if any(f'<Picture {i+1}>' not in prompt for i in range(len(references))):raise ValueError('El prompt no define todas las referencias.')
    def n(kind,**kw):return {'class_type':kind,'inputs':kw}
    g={'1':n('UNETLoader',unet_name=MODELS[mode],weight_dtype='default'),
      '3':n('RemakeKeyframeUntil',model=['1',0],keyframe_until=.9),
      '4':n('CLIPLoader',clip_name='qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors',type='minimax',device='default'),
      '5':n('VAELoader',vae_name='minimax_h3_video_vae_fp16.safetensors'),
      '6':n('VAELoader',vae_name='minimax_h3_audio_vae_fp32.safetensors'),
      '7':n('LoadVideo',file=relative(source)),'8':n('GetVideoComponents',video=['7',0]),
      '11':n('RemakeConditioning',clip=['4',0],vae=['5',0],audio_vae=['6',0],source=['8',0],audio=['8',1],prompt=prompt,pins=', '.join(map(str,pins)),frames=frames,short_edge=short_edge),
      '12':n('RemakeNoise',positive=['11',0],seed=seed),'13':n('BasicGuider',model=['3',0],conditioning=['11',0]),
      '14':n('KSamplerSelect',sampler_name='euler'),'15':n('ManualSigmas',sigmas=SIGMAS[mode]),
      '16':n('SamplerCustomAdvanced',noise=['12',0],guider=['13',0],sampler=['14',0],sigmas=['15',0],latent_image=['11',1]),
      '17':n('VAEDecode',samples=['16',0],vae=['5',0]),'18':n('VAEDecodeAudio',samples=['16',0],vae=['6',0]),
      '19':n('CreateVideo',images=['17',0],audio=['18',0],fps=24.),
      '20':n('SaveVideo',video=['19',0],filename_prefix=relative(output_prefix),format='mp4',**{'format.codec':'h264','format.codec.encoding':'re-encode','format.codec.encoding.crf':18.})}
    for i,ref in enumerate(references):
        key=str(30+i);g[key]=n('LoadImage',image=relative(ref));g['11']['inputs'][f'ref_{i+1}']=[key,0]
    if prefix:
        g['21']=n('LoadVideo',file=relative(prefix));g['22']=n('GetVideoComponents',video=['21',0])
        g['23']=n('GenStudioViggleHop',latent=['11',1],vae=['5',0],prefix=['22',0],noise=['12',0],seed=seed)
        g['16']['inputs'].update(latent_image=['23',0],noise=['23',1])
    return g
