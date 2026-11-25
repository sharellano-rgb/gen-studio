"""Owned process. Imports existing models read-only; refuses writes outside the private job."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

folder=Path(sys.argv[1]).resolve();request=json.loads((folder/'lip-request.json').read_text())
if request.get('preflight_only'):os.environ['CUDA_VISIBLE_DEVICES']='-1'
else:
    # A backend crash stops heartbeats. The owned worker then exits and releases CUDA.
    def watch_lease():
        while True:
            try:alive=time.time()-(folder/'lip-lease').stat().st_mtime<12
            except OSError:alive=False
            if not alive:os._exit(70)
            time.sleep(1)
    threading.Thread(target=watch_lease,daemon=True).start()
root=Path(Path.home()/'.gen-studio'/'media-engines');comfy=Path('D:/AI/apps/ComfyUI-ComfyField')
os.chdir(folder);sys.dont_write_bytecode=True
for key in ('HF_HOME','TORCH_HOME','XDG_CACHE_HOME','MPLCONFIGDIR','TEMP','TMP'):
    os.environ[key]=str(folder/'cache')
(folder/'cache').mkdir(exist_ok=True)
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',TOKENIZERS_PARALLELISM='false')


def audit(event,args):
    if event=='open':
        path,mode,flags=args
        writing=(isinstance(mode,str) and any(c in mode for c in 'wax+')) or (isinstance(flags,int) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC))
        if writing and isinstance(path,(str,bytes,os.PathLike)) and not Path(os.fsdecode(path)).resolve().is_relative_to(folder):
            raise PermissionError('El trabajador de lip sync no puede escribir fuera de su trabajo.')
    if event in ('os.mkdir','os.remove','os.rmdir','os.rename'):
        paths=args[:2] if event=='os.rename' else args[:1]
        if any(isinstance(p,(str,bytes,os.PathLike)) and not Path(os.fsdecode(p)).resolve().is_relative_to(folder) for p in paths):
            raise PermissionError('Mutación externa bloqueada en lip sync.')
sys.addaudithook(audit)
sys.path[:0]=[str(root/'dependencies'),str(comfy),str(root/'lipsync')]
import numpy as np
import torch
from diffusers import AutoencoderKL, DDIMScheduler
from omegaconf import OmegaConf
import latentsync.pipelines.lipsync_pipeline as pipeline_module
from latentsync.models.unet import UNet3DConditionModel
import latentsync.whisper.audio2feature as audio_module
Audio2Feature=audio_module.Audio2Feature
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision
import mediapipe as mp


def command(args,data=None):
    r=subprocess.run(list(map(str,args)),input=data,capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if r.returncode:raise RuntimeError(r.stderr.decode(errors='replace')[-800:])
    return r.stdout

def ff(*args,data=None):return command(['ffmpeg','-v','error','-nostdin','-n',*args],data)

width,height,n=request['width'],request['height'],request['frames'];fps=request['fps'];roi=request['region'];x,y,w,h=[roi[k] for k in ('x','y','width','height')]
raw=ff('-i',request['video'],'-an','-f','rawvideo','-pix_fmt','rgb24','pipe:1')
original=np.frombuffer(raw,np.uint8).reshape(-1,height,width,3)
if len(original)!=n:raise ValueError('Fotogramas inesperados antes de lip sync.')
crop=original[:,y:y+h,x:x+w].copy()
options=vision.FaceLandmarkerOptions(base_options=mp_python.BaseOptions(model_asset_path=str(root/'lipsync/latentsync/weights/face_landmarker.task')),num_faces=2)
boxes=[]
with vision.FaceLandmarker.create_from_options(options) as detector:
    for frame in crop:
        found=detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB,data=frame)).face_landmarks
        if len(found)!=1:raise ValueError('La región debe contener exactamente un rostro detectable en cada fotograma.')
        pts=np.array([[p.x,p.y] for p in found[0]])
        lo,hi=pts.min(axis=0),pts.max(axis=0)
        if min(lo)<.01 or max(hi)>.99:raise ValueError('El rostro sale de la región seleccionada.')
        boxes.append([*lo.tolist(),*hi.tolist()])
(folder/'face-regions.json').write_text(json.dumps(boxes))
if request.get('preflight_only'):
    print(json.dumps(dict(frames=n,single_face_all_frames=True,torch=torch.__version__,cuda_build=torch.version.cuda)))
    sys.exit(0)
# Only the imported module's runtime debug location changes, never its source file.
pipeline_module.__file__=str(folder/'private-runtime/latentsync/pipelines/lipsync_pipeline.py')
audio_module.__file__=str(folder/'private-runtime/latentsync/whisper/audio2feature.py')
class LocalAudio(Audio2Feature):
    def _resolve_model_reference(self,path):
        candidate=Path(path).resolve()
        if not candidate.is_file() or not candidate.is_relative_to(root/'models'):raise ValueError('Whisper debe estar instalado localmente.')
        return str(candidate),str(candidate)
Base=pipeline_module.LipsyncPipeline
class TrainedMaskPipeline(Base):
    def affine_transform_video(self,frames):
        faces,boxes,matrices,_=super().affine_transform_video(frames)
        keep=self.image_processor.mask_image[:1].expand(len(faces),-1,-1,-1)
        return faces,boxes,matrices,keep
    @staticmethod
    def paste_surrounding_pixels_back(decoded,reference,keep,device,dtype):
        return Base.paste_surrounding_pixels_back(decoded,reference,1-keep,device,dtype)

config=OmegaConf.load(root/'lipsync/configs/unet/stage2_512.yaml');dtype=torch.float16
ff('-i',request['audio'],'-af','apad','-t',n/fps+.25,'-ar','16000','-ac','1','-c:a','pcm_s16le',folder/'conditioning.wav')
audio=LocalAudio(model_path=str(root/'models/latentsync/whisper/tiny.pt'),device='cuda',num_frames=config.data.num_frames)
unet,_=UNet3DConditionModel.from_pretrained(OmegaConf.to_container(config.model),str(root/'models/latentsync/latentsync_unet.pt'),device='cpu')
vae=AutoencoderKL.from_pretrained(root/'models/sd-vae',local_files_only=True,torch_dtype=dtype)
vae.register_to_config(scaling_factor=.18215,shift_factor=0)
scheduler=DDIMScheduler(beta_start=.00085,beta_end=.012,beta_schedule='scaled_linear',clip_sample=False,set_alpha_to_one=False,steps_offset=1)
pipe=TrainedMaskPipeline(vae=vae,audio_encoder=audio,unet=unet.to(dtype=dtype),scheduler=scheduler).to('cuda')
padded=np.concatenate([crop,np.repeat(crop[-1:],8,axis=0)])
torch.cuda.reset_peak_memory_stats()
result=pipe(padded,str(folder/'conditioning.wav'),video_fps=fps,height=512,width=512,num_inference_steps=20,
    guidance_scale=config.run.guidance_scale,weight_dtype=dtype,mask_image_path=str(root/'lipsync/latentsync/utils/mask.png'),
    generator=torch.Generator(device='cuda').manual_seed(request['seed']))
if len(result)<n:raise ValueError('Lip sync devolvió menos fotogramas de los solicitados.')
pixels=result[:n].mul(255).round().clamp(0,255).to(torch.uint8).numpy()
if pixels.shape!=crop.shape:raise ValueError('Lip sync cambió las dimensiones de la región.')
composite=original.copy();composite[:,y:y+h,x:x+w]=pixels
protected=np.ones((height,width),dtype=bool);protected[y:y+h,x:x+w]=False
difference=int(np.abs(composite[:,protected].astype(np.int16)-original[:,protected]).max())
if difference:raise ValueError('La composición alteró píxeles protegidos.')
ff('-f','rawvideo','-pix_fmt','rgb24','-s',f'{width}x{height}','-r',fps,'-i','pipe:0','-frames:v',n,
   '-c:v','libx264','-crf','17','-pix_fmt','yuv420p',folder/'lip.mp4',data=composite.tobytes())
(folder/'lip-proof.json').write_text(json.dumps(dict(frames=n,fps=fps,outside_region_max_difference=difference,
    protected_scope='H3 input pixels outside reviewed rectangle BEFORE lossy encoding; not original movie pixels',
    torch_peak_allocated=torch.cuda.max_memory_allocated(),torch_peak_reserved=torch.cuda.max_memory_reserved(),
    model='LatentSync1.6',mouth_quality='human_review_required')),encoding='utf-8')
