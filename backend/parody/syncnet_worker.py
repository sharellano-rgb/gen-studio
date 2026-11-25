"""CPU evaluator in an existing, explicitly registered Python runtime.

Invoked only with a server-created private request file. No network, installation,
GPU, LLM, frame generation, shell commands or changes to shared environments.
"""
import os
os.environ['CUDA_VISIBLE_DEVICES']='-1'
os.environ['OMP_NUM_THREADS']='4'
import sys
sys.dont_write_bytecode=True
import json, hashlib, subprocess, time
from pathlib import Path


def main():
    request=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    rt=request['runtime'];output=Path(request['output']);started=time.monotonic()
    def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    for item in rt['files']:
        if digest(item['path'])!=item['sha256']:raise ValueError('Changed evaluator dependency: '+Path(item['path']).name)
    sys.path.insert(0,rt['shared_dependencies']);sys.path.insert(0,rt['model_root']);sys.path.insert(0,rt['local_dependencies'])
    import numpy as np, torch, cv2, mediapipe as mp, scipy
    from scipy.signal import medfilt
    from python_speech_features import mfcc
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision
    from SyncNetModel import S
    versions=dict(torch=torch.__version__,numpy=np.__version__,cv2=cv2.__version__,mediapipe=mp.__version__,scipy=scipy.__version__)
    if versions!=rt['versions']:raise ValueError('Changed evaluator package versions; revalidate the isolated registration.')
    torch.set_num_threads(4)
    video=Path(request['video']);voice=Path(request['voice'])
    if digest(video)!=request['video_sha256'] or digest(voice)!=request['voice_sha256']:raise ValueError('Changed input')
    def ff(args):return subprocess.check_output([rt['ffmpeg'],'-v','error','-nostdin',*map(str,args)],timeout=60)
    def finish(**values):
        values.update(elapsed_seconds=round(time.monotonic()-started,3),versions=versions,device='cpu')
        with output.open('x',encoding='utf-8') as f:json.dump(values,f)
    region=request['region'];x,y,w,h=[region[k] for k in ('x','y','width','height')]
    if min(w,h)<128 or max(w,h)>1280 or min(x,y)<0 or not 24<=request['frames']<=120:raise ValueError('Invalid bounded shot/region')
    # FFmpeg observes at 25 fps; delivered video remains 24 fps, untouched.
    raw=ff(['-i',video,'-an','-vf',f'fps=25,crop={w}:{h}:{x}:{y}','-pix_fmt','rgb24','-f','rawvideo','-'])
    frames=np.frombuffer(raw,np.uint8).reshape(-1,h,w,3)
    if len(frames)>126 or abs(len(frames)-request['frames']*25/24)>1:raise ValueError('Unexpected observation duration')
    options=vision.FaceLandmarkerOptions(base_options=python.BaseOptions(model_asset_path=rt['face_model']),num_faces=2)
    boxes=[]
    with vision.FaceLandmarker.create_from_options(options) as detector:
        for frame in frames:
            faces=detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB,data=frame)).face_landmarks
            if len(faces)!=1:
                finish(status='review',reason='No se ve un único rostro en toda la región seleccionada.');return
            points=np.array([[v.x*w,v.y*h] for v in faces[0]])
            low=points.min(axis=0);high=points.max(axis=0);center=(low+high)/2;size=max(high-low)/2
            if min(low)<1 or high[0]>w-1 or high[1]>h-1 or size<24:
                finish(status='review',reason='El rostro está recortado o es demasiado pequeño para medirlo.');return
            boxes.append([*center,size])
    boxes=np.array(boxes)
    # A sudden track jump is ambiguous, not proof that the new face is the same actor.
    if len(boxes)>1 and np.any(np.linalg.norm(np.diff(boxes[:,:2],axis=0),axis=1)>np.minimum(boxes[:-1,2],boxes[1:,2])):
        finish(status='review',reason='El rostro cambia bruscamente de posición; revisa la selección o divide el plano.');return
    for i in range(3):boxes[:,i]=medfilt(boxes[:,i],13)
    crops=[]
    for frame,(cx,cy,size) in zip(frames,boxes):
        pad=int(size*1.8);padded=np.pad(frame,((pad,pad),(pad,pad),(0,0)),constant_values=110)
        crop=padded[int(cy+pad-size):int(cy+pad+size*1.8),int(cx+pad-size*1.4):int(cx+pad+size*1.4)]
        crops.append(cv2.cvtColor(cv2.resize(crop,(224,224)),cv2.COLOR_RGB2BGR))
    # Either prepared PCM or the real muxed delivery is measured on the second pass.
    audio=np.frombuffer(ff(['-i',request['audio'],'-vn','-ar','16000','-ac','1','-f','s16le','-']),np.int16)
    if len(audio)>int(request['frames']/24*16000)+1024:raise ValueError('Audio exceeds shot')
    audio=np.pad(audio,(0,max(0,len(crops)*640-len(audio))))[:len(crops)*640]
    voice_raw=np.frombuffer(ff(['-i',voice,'-vn','-ar','16000','-ac','1','-f','s16le','-']),np.int16)
    blocks=np.pad(voice_raw,(0,(-len(voice_raw))%160)).astype(np.float64).reshape(-1,160)/32768
    rms=np.sqrt(np.mean(blocks**2,axis=1));speech_ms=int(np.sum(rms>max(.003,float(rms.max())*.05))*10)
    model=S().cpu().eval();model.load_state_dict(torch.load(rt['weights'],map_location='cpu',weights_only=True),strict=True)
    im=torch.tensor(np.stack(crops).transpose(3,0,1,2)[None],dtype=torch.float32)
    coeff=torch.tensor(mfcc(audio,16000).T[None,None],dtype=torch.float32);count=len(crops)-5
    with torch.inference_mode():
        visual=torch.cat([model.forward_lip(torch.cat([im[:,:,i:i+5] for i in range(j,min(count,j+4))])) for j in range(0,count,4)])
        sound=torch.cat([model.forward_aud(torch.cat([coeff[:,:,:,i*4:i*4+20] for i in range(j,min(count,j+4))])) for j in range(0,count,4)])
    shift=15;padded=torch.nn.functional.pad(sound,(0,0,shift,shift))
    distances=torch.stack([torch.nn.functional.pairwise_distance(visual[i:i+1].repeat(shift*2+1,1),padded[i:i+shift*2+1]) for i in range(count)])
    def summarize(matrix):
        values=matrix.mean(0);median=float(values.median());best=int(values.argmin())
        candidates=[dict(id='offset-'+str(i),delay_samples=(shift-i)*1920,distance=float(v),confidence=max(0,median-float(v))) for i,v in enumerate(values)]
        return candidates,candidates[best]
    candidates,_=summarize(distances);split=count//2;windows=[]
    for start,end in [(0,split),(split,count)]:
        _,best=summarize(distances[start:end]);windows.append(dict(start_ms=start*40,end_ms=end*40,delay_samples=best['delay_samples'],confidence=best['confidence']))
    # Hashes are checked again after extraction; no changed input is certified.
    if digest(video)!=request['video_sha256'] or digest(voice)!=request['voice_sha256']:raise ValueError('Input changed during measurement')
    finish(status='measured',measurement=dict(video_sha256=request['video_sha256'],voice_sha256=request['voice_sha256'],
        evaluator='syncnet-cpu-mediapipe-2',evaluator_sha256=request['evaluator_sha256'],phonetic=True,
        single_target_verified=request['target_confirmed'],face_coverage=1,speech_ms=speech_ms,frames=request['frames'],
        voice_samples=request['voice_samples'],current_offset_samples=request['current_offset_samples'],candidates=candidates,windows=windows))


if __name__=='__main__':main()
