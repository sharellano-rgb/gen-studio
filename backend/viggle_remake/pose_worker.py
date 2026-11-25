"""CPU-only DWPose comparison using already-installed models. Never downloads."""
import os,sys,json,hashlib,subprocess,contextlib
from pathlib import Path
os.environ.update(CUDA_VISIBLE_DEVICES='-1',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',AUX_ORT_PROVIDERS='CPUExecutionProvider')
def sha(path):
 with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def main():
 manifest=Path(sys.argv[1]);p=json.loads(manifest.read_text(encoding='utf-8'));aux=Path(p['aux'])
 for name,item in p['models'].items():
  if not Path(item['path']).is_file() or sha(item['path'])!=item['sha256']:raise ValueError('Local pose model changed: '+name)
 sys.path.insert(0,str(aux/'src'))
 import cv2,numpy as np,torch,onnxruntime as ort
 from custom_controlnet_aux.dwpose.dw_onnx.cv_ox_det import inference_detector
 from custom_controlnet_aux.dwpose.dw_torchscript.jit_pose import inference_pose
 from custom_controlnet_aux.dwpose.util import guess_onnx_input_shape_dtype
 torch.set_num_threads(8);opts=ort.SessionOptions();opts.intra_op_num_threads=8
 det=ort.InferenceSession(p['models']['det']['path'],sess_options=opts,providers=['CPUExecutionProvider'])
 pose=torch.jit.load(p['models']['pose']['path'],map_location='cpu').eval();size,_=guess_onnx_input_shape_dtype(Path(p['models']['pose']['path']).name)
 def frame(path,n):
  r=subprocess.run(['ffmpeg','-v','error','-i',path,'-vf',f'select=eq(n\\,{n})','-frames:v','1','-f','image2pipe','-vcodec','png','pipe:1'],capture_output=True,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW)
  if r.returncode:raise ValueError(r.stderr.decode(errors='replace'))
  a=cv2.imdecode(np.frombuffer(r.stdout,dtype=np.uint8),cv2.IMREAD_COLOR)
  if a is None:raise ValueError('Unreadable geometry frame')
  return a
 if sha(p['source'])!=p['source_sha256'] or sha(p['result'])!=p['result_sha256']:raise ValueError('Geometry inputs changed')
 frames=[]
 for n in p['frames']:
  a=frame(p['source'],n);b=frame(p['result'],0 if p['still'] else n)
  if a.shape!=b.shape:raise ValueError('Different source/result canvases')
  h,w=a.shape[:2];boxes=inference_detector(det,a,detect_classes=[0],dtype=np.float32)
  measurements=[]
  if boxes is not None and 0<len(boxes)<=12:
   # Same source ROIs for both images: animated targets need not be detected by YOLO.
   with torch.inference_mode():x,xs=inference_pose(pose,boxes,a,size);y,ys=inference_pose(pose,boxes,b,size)
   for i,box in enumerate(boxes):
    valid=(xs[i,:17]>=.4)&(ys[i,:17]>=.4)
    valid[:5]=False # Facial proportions change intentionally.
    valid &= (x[i,:17,0]>=0)&(x[i,:17,0]<w)&(x[i,:17,1]>=0)&(x[i,:17,1]<h)&(y[i,:17,0]>=0)&(y[i,:17,0]<w)&(y[i,:17,1]>=0)&(y[i,:17,1]<h)
    errors=np.linalg.norm((x[i,:17]-y[i,:17])/np.array([w,h]),axis=-1)
    measurements.append({'source_box':box.tolist(),'valid_joints':int(valid.sum()),'median_error':float(np.median(errors[valid])) if valid.any() else None,'max_error':float(np.max(errors[valid])) if valid.any() else None,'source_points':x[i,:17].tolist(),'result_points':y[i,:17].tolist(),'valid_mask':valid.tolist()})
  # A black frame border is deterministic canvas padding, not editable scenery.
  mask=np.zeros((h,w),dtype=bool)
  for axis in ['left','right','top','bottom']:
   for t in range((w if axis in ('left','right') else h)//3):
    col=a[:,t] if axis=='left' else a[:,w-1-t] if axis=='right' else a[t,:] if axis=='top' else a[h-1-t,:]
    if np.quantile(col,.99)>12:break
    if axis=='left':mask[:,t]=True
    elif axis=='right':mask[:,w-1-t]=True
    elif axis=='top':mask[t,:]=True
    else:mask[h-1-t,:]=True
  changed=float(np.mean(np.max(b[mask],axis=1)>24)) if mask.any() else 0.
  frames.append({'frame':n,'detected_people':len(boxes) if boxes is not None else 0,'people':measurements,'padding_changed_fraction':changed})
 if sha(p['source'])!=p['source_sha256'] or sha(p['result'])!=p['result_sha256']:raise ValueError('Geometry inputs changed during review')
 output={'version':'dwpose-source-roi-1','binding':{k:p[k] for k in ['source_sha256','result_sha256','frames','still','models']},'frames':frames}
 Path(p['output']).write_text(json.dumps(output,allow_nan=False),encoding='utf-8')
if __name__=='__main__':main()
