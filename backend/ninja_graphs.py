"""Pinned AI Ninja H3 recipe adaptations: selected persisted SAM3 region, original
24fps shot without vendor stretch/truncation, one supplied reference, masked
W1 uncrop, and native PyTorch attention. The two author samplers remain distinct."""
from collections.abc import Mapping
from pathlib import PurePosixPath
import math,re
from .ninja_limits import MAX_FRAMES,MAX_MODEL_FRAMES
BASE="minimax_h3_ref2va_pruned_int8_convrot.safetensors"
TURBO_W1="minimax_h3_fl2v_lightx2v_turbo_8step_v1.0_resized_avg_rank_24_bf16.safetensors"
TURBO_W2="minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors"
ENCODER="qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
VIDEO_VAE="minimax_h3_video_vae_fp16.safetensors"
AUDIO_VAE="minimax_h3_audio_vae_fp32.safetensors"
SAM3="sam3.1_multiplex_fp16.safetensors"
FPS=24
SIGMA_CURVE='{"points":[[0,1,1],[1,1,1],[1,0,1]],"interpolation":"smooth","tension":1,"endpointsLocked":true,"snapToSteps":true}'
def _node(kind,**inputs):return {"class_type":kind,"inputs":inputs}
def _positive_int(value,name):
    if type(value) is not int or value<=0:raise ValueError(name+" must be a positive integer")
    return value
def _relative(value,name):
    if not isinstance(value,str) or not value or chr(0) in value:raise ValueError(name+" must be a relative input path")
    normalized=value.replace(chr(92),"/");path=PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or ":" in normalized:raise ValueError(name+" must be a relative input path")
    return value
def _validate(p,job_id):
    if not isinstance(p,Mapping) or p.get("recipe") not in ("w1","w2") or p.get("mode") not in ("head","body"):raise ValueError("Unknown AI Ninja recipe or mode")
    frames=_positive_int(p.get("frames"),"frames");count=_positive_int(p.get("render_frames"),"render_frames")
    if count<5 or (count-5)%17 or frames>count or frames>MAX_FRAMES or count>MAX_MODEL_FRAMES:raise ValueError("Invalid H3 frame grid")
    for name in ("width","height"):_positive_int(p.get(name),name)
    for name in ("x","y"):
        v=p.get(name)
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=1:raise ValueError("Invalid normalized point")
    if not isinstance(job_id,str) or not re.fullmatch(r"[A-Za-z0-9_-]+",job_id):raise ValueError("Invalid run identifier")
    return count
def _source(p,video,count):
    return {"video":_node("LoadVideo",file=_relative(video,"video")),
      "components":_node("GetVideoComponents",video=["video",0]),
      "source-exact":_node("GenNinjaExact",images=["components",0],expected=count,width=p["width"],height=p["height"])}
def _save_video(g,images,audio,job_id,stem,save_id):
    inputs={"images":images,"fps":FPS}
    if audio is not None:inputs["audio"]=audio
    g[save_id+"-video"]=_node("CreateVideo",**inputs)
    g[save_id]=_node("SaveVideo",video=[save_id+"-video",0],filename_prefix=f"gen_studio/{job_id}/{stem}",format="mp4",codec="h264",
        **{"codec.encoding":"re-encode","codec.encoding.crf":19})
def track_graph(p,video,job_id):
    count=_validate(p,job_id);g=_source(p,video,count)
    g.update({
      "sam3":_node("CheckpointLoaderSimple",ckpt_name=SAM3),
      "sam3-text":_node("CLIPTextEncode",clip=["sam3",1],text="head" if p["mode"]=="head" else "person"),
      "track":_node("SAM3_VideoTrack",images=["source-exact",0],model=["sam3",0],conditioning=["sam3-text",0],detection_threshold=.5,max_objects=0,detect_interval=1),
      "select":_node("GenNinjaSelectMask",track_data=["track",0],x=p["x"],y=p["y"],expected=count),
      "save-mask":_node("GenNinjaSaveMask",masks=["select",0],images=["source-exact",0],expected=count,run_id=job_id,selection=["select",1])})
    _save_video(g,["save-mask",1],None,job_id,"selected-area","preview-save")
    return g
def render_graph(p,video,reference,job_id,bundle):
    count=_validate(p,job_id)
    if not isinstance(bundle,Mapping) or not re.fullmatch(r"[0-9a-fA-F]{64}",str(bundle.get("sha256",""))):raise ValueError("Verified mask SHA required")
    mask_path=_relative(bundle.get("path"),"bundle");reference=_relative(reference,"reference")
    seed=p.get("seed")
    if type(seed) is not int or not 0<=seed<=9007199254740991:raise ValueError("Invalid seed")
    prompt=p.get("prompt")
    if not isinstance(prompt,str) or not prompt.strip():raise ValueError("Explicit prompt required")
    motion=p.get('ninja_head_motion','original')
    if motion not in ('original','preserve'):raise ValueError('Unknown head motion setting')
    if motion=='preserve' and (p['recipe']!='w2' or p['mode']!='head'):raise ValueError('Head motion requires W2 head')
    w1=p["recipe"]=="w1";g=_source(p,video,count)
    g.update({
      "audio":_node("GenNinjaAudio",frames=count,audio=["components",1]),
      "load-mask":_node("GenNinjaLoadMask",bundle=mask_path,expected=count,sha256=bundle["sha256"].lower()),
      "model":_node("UNETLoader",unet_name=BASE,weight_dtype="default"),
      "video-vae":_node("VAELoader",vae_name=VIDEO_VAE),
      "audio-vae":_node("VAELoader",vae_name=AUDIO_VAE),
      "clip":_node("CLIPLoader",clip_name=ENCODER,type="minimax",device="default"),
      "reference":_node("LoadImage",image=reference)})
    model=["model",0];masks=["load-mask",0]
    if w1:
        g["attention"]=_node("ModelAttentionBackend",model=model,attention="pytorch attention");model=["attention",0]
        g["cleanup"]=_node("MVEx_MaskCleanup",masks=masks,threshold=.3,method="shrink_grow",edge_grow=16,**{"method.shrink":12,"method.min_frames":4});masks=["cleanup",0]
    g["turbo"]=_node("LoraLoaderModelOnly",model=model,lora_name=TURBO_W1 if w1 else TURBO_W2,strength_model=1. if w1 else .5)
    g["shift"]=_node("MiniMaxH3SigmaShift",model=["turbo",0],shift_video=12.,shift_audio=1.5)
    g["crop"]=_node("MVEx_SubjectCrop",original_images=["source-exact",0],masks=masks,mode="tracked",divisible_by=32,upscale_megapixels=.5 if w1 else .3,resize_method="auto",
        **{"mode.crop_scale":1.75,"mode.padding":"firm","mode.prefer":"stillness","mode.aspect_ratio":0.,"mode.seamless_loop":False})
    g["crop-exact"]=_node("GenNinjaExact",images=["crop",0],expected=count,width=0,height=0)
    g["crop-size"]=_node("GetImageSize",image=["crop-exact",0])
    g["condition"]=_node("MiniMaxH3ReferenceToVideo",clip=["clip",0],vae=["video-vae",0],audio_vae=["audio-vae",0],prompt=prompt,
        width=["crop-size",0],height=["crop-size",1],length=count,ref_image_size="match" if w1 else "max",**{"ref_images.ref_image_0":["reference",0]})
    if w1:
        g.update({
          "latent-mask":_node("MVEx_MaskToLatentSpace",masks=["crop",1],vae=["video-vae",0],compression="auto",spatial_method="max",temporal_method="max",grow_spatial=36,grow_temporal=1,grow_temp_mode="both"),
          "video-encode":_node("VAEEncode",pixels=["crop-exact",0],vae=["video-vae",0]),
          "video-latent":_node("SetLatentNoiseMask",samples=["video-encode",0],mask=["latent-mask",0]),
          "audio-encode":_node("VAEEncodeAudio",audio=["audio",0],vae=["audio-vae",0]),
          "audio-zero":_node("SolidMask",value=0.,width=32,height=32),
          "audio-latent":_node("SetLatentNoiseMask",samples=["audio-encode",0],mask=["audio-zero",0]),
          "av-latent":_node("LTXVConcatAVLatent",video_latent=["video-latent",0],audio_latent=["audio-latent",0]),
          "sample":_node("KSampler",model=["shift",0],positive=["condition",0],negative=["condition",0],latent_image=["av-latent",0],seed=seed,steps=8,cfg=1.,sampler_name="er_sde",scheduler="simple",denoise=1.),
          "audio-decode":_node("VAEDecodeAudio",samples=["sample",0],vae=["audio-vae",0])})
        # SubjectUncrop feathers only the rectangle; soften the selected silhouette separately.
        g["composite-mask"]=_node("NKDMaskOpsLean",mask=["crop",1],fill_holes=False,expand=0,feather=20)
        composite_mask=["composite-mask",0];output_audio=["audio-decode",0]
    else:
        g.update({
          "mask-ops":_node("NKDMaskOps",mask=["crop",1],vae=["video-vae",0],model=["turbo",0],invert=False,black_point=0.,white_point=1.,despeckle=2,fill_holes=False,close_gaps=0,temporal_expand=1,temporal_smooth=0,expand=30,blockify=0,blockify_threshold=.05,feather=0,edge_low=0.,edge_high=1.),
          "composite-mask":_node("NKDMaskOpsLean",mask=["mask-ops",0],fill_holes=False,expand=10,feather=20),
          "av-latent":_node("NKDAVLatent",images=["crop-exact",0],audio=["audio",0],video_vae=["video-vae",0],audio_vae=["audio-vae",0],latent_mask=["mask-ops",2],audio_mode="keep",fps=FPS,ramp_ticks=0,ramp_out_ticks=0,ramp_shape="cosine"),
          "guider":_node("BasicGuider",model=["shift",0],conditioning=["condition",0]),
          "noise":_node("RandomNoise",noise_seed=seed),
          "sampler":_node("KSamplerSelect",sampler_name="sa_solver_pece"),
          "sigmas":_node("NKDSigmasCurve",curve_data=SIGMA_CURVE,steps=8,max_sigma=.9 if motion=="preserve" else 1.),
          "sample":_node("SamplerCustomAdvanced",noise=["noise",0],guider=["guider",0],sampler=["sampler",0],sigmas=["sigmas",0],latent_image=["av-latent",0])})
        composite_mask=["composite-mask",0];output_audio=["audio",0]
    g.update({
      "decode":_node("VAEDecode",samples=["sample",0],vae=["video-vae",0]),
      "decode-exact":_node("GenNinjaExact",images=["decode",0],expected=count,width=["crop-size",0],height=["crop-size",1]),
      "uncrop":_node("MVEx_SubjectUncrop",cropped_images=["decode-exact",0],original_images=["source-exact",0],bboxes=["crop",2],cropped_masks=composite_mask,feather=16,resize_method="auto"),
      "output-exact":_node("GenNinjaExact",images=["uncrop",0],expected=count,width=p["width"],height=p["height"])})
    _save_video(g,["output-exact",0],output_audio,job_id,"render","save")
    return g
