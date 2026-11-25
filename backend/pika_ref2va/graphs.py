from .profiles import SIGMAS,dimensions,VERSION

def native_prompt(p):
    swap=p['mode']=='swap'
    scene=('Preserve the source scene, camera motion, background people, objects and lighting; replace only the selected performer.' if swap else
        'Transfer only the selected source performance to the appearance-reference character in the destination scene described by the user and Qwen below. Preserve source timing, camera-relative trajectory and changing scale; adapt supports and lighting coherently to that destination.')
    retention=('Keep one replacement at the source target position; preserve unrelated performers.' if swap else
        'Keep one replacement following the source target trajectory. Source background, scenery and supporting props are removed when they conflict with the destination. Keep the destination and its supporting objects consistent throughout the entire clip; do not return to the source environment.')
    return '''subject_definitions:
<Subject 1> is the selected original performer in <Video 1>. <Subject 2> is the replacement character whose appearance is shown in <Picture 1>.

summary:
'''+('[video editing]' if swap else '[reference generation]')+' '+scene+'''

retention_analysis:
<Subject 1>: source appearance removed; source pose, motion, head yaw/pitch, contacts, timing and trajectory attribute_transfer from <Video 1>.
<Subject 2>: identity, entire head, face, hair, body proportions, outfit and footwear fully_preserved from <Picture 1>. The image backdrop, static reference pose and camera-facing gaze are removed. '''+retention+'''

detailed_description:
Begin already in the actual first source-frame pose. Read torso/chest orientation separately from head yaw/downward pitch, arms, knees and visible foot/hand contacts. Do not begin in the identity image's static stance or add a settling-in transition. Follow the source chronologically without mirroring, retiming or freezing; retain occlusions and uncertainty. At later windows continue their local source pose, never restart the whole-video opening stance. The actual video controls motion when sampled descriptions are uncertain. Preserve observed camera tracking/tilt rather than assuming a locked camera. Appearance references do not determine scene lighting or cast shadows.
'''+scene+'''

overall_soundscape:
Natural ambient scene sound, no invented speech. Original selected source audio is restored during assembly.

non_diegetic_music:
None.'''

def graph(p,image,video,ident):
    if p.get('pika_ref2va_version')!=VERSION:raise ValueError('Versión Ref2VA desconocida.')
    frames=p['frames']
    if not 5<=frames<=124 or (frames-5)%17:raise ValueError('El tramo no conserva la rejilla temporal nativa de H3.')
    w,h=dimensions(p)
    def n(kind,**kw):return {'class_type':kind,'inputs':kw}
    return {
        'dit':n('UNETLoader',unet_name='minimax_h3_ref2va_pruned_int8_convrot.safetensors',weight_dtype='default'),
        'lora':n('LoraLoaderModelOnly',model=['dit',0],lora_name='pika_h3_flex_ref2va_v1_bf16.safetensors',strength_model=1.),
        'encoder':n('CLIPLoader',clip_name='qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors',type='minimax',device='default'),
        'video_vae':n('VAELoader',vae_name='minimax_h3_video_vae_int8_convrot.safetensors'),
        'audio_vae':n('VAELoader',vae_name='minimax_h3_audio_vae_fp32.safetensors'),
        'image':n('LoadImage',image=image),'video':n('LoadVideo',file=video),'components':n('GetVideoComponents',video=['video',0]),
        'conditioning':n('MiniMaxH3ReferenceToVideo',clip=['encoder',0],vae=['video_vae',0],audio_vae=['audio_vae',0],prompt=native_prompt(p),
            width=w,height=h,length=frames,ref_image_size='match',**{'ref_images.ref_image_0':['image',0],'ref_videos.ref_video_0':['components',0]}),
        'guider':n('BasicGuider',model=['lora',0],conditioning=['conditioning',0]),'noise':n('RandomNoise',noise_seed=p['seed']),
        'sampler':n('KSamplerSelect',sampler_name='lcm'),'sigmas':n('ManualSigmas',sigmas=SIGMAS),
        'sampling':n('SamplerCustomAdvanced',noise=['noise',0],guider=['guider',0],sampler=['sampler',0],sigmas=['sigmas',0],latent_image=['conditioning',1]),
        'video_decode':n('VAEDecode',samples=['sampling',0],vae=['video_vae',0]),'audio_decode':n('VAEDecodeAudio',samples=['sampling',0],vae=['audio_vae',0]),
        'mux':n('CreateVideo',images=['video_decode',0],audio=['audio_decode',0],fps=24.,bit_depth=8,color_space='sRGB',codec='none'),
        'save':n('SaveVideo',video=['mux',0],filename_prefix='gen-studio-pika-ref2va/'+ident,format='mp4',**{'format.codec':'h264','format.codec.encoding':'re-encode','format.codec.encoding.crf':18.})}
