"""CPU-only frame extraction and quality evidence. Never downloads or uses GPU."""
import os,sys,json,hashlib,subprocess
from pathlib import Path
os.environ['CUDA_VISIBLE_DEVICES']='-1'
import cv2,numpy as np
from PIL import Image
def sha(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def main(path):
 p=json.loads(Path(path).read_text());folder=Path(path).parent
 if sha(p['source'])!=p['source_sha256']:raise ValueError('Source changed')
 frames=[]
 for n in p['indices']:
  if type(n) is not int or n<0:raise ValueError('Invalid frame')
  r=subprocess.run(['ffmpeg','-v','error','-nostdin','-i',p['source'],'-vf',f'select=eq(n\\,{n})',
   '-frames:v','1','-f','image2pipe','-vcodec','png','pipe:1'],capture_output=True,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW)
  if r.returncode:raise ValueError(r.stderr.decode(errors='replace')[-500:])
  a=cv2.imdecode(np.frombuffer(r.stdout,np.uint8),cv2.IMREAD_COLOR)
  if a is None:raise ValueError('Unreadable source frame')
  gray=cv2.cvtColor(a,cv2.COLOR_BGR2GRAY);gray=cv2.resize(gray,(max(1,round(gray.shape[1]*256/gray.shape[0])),256))
  black=float((gray<=5).mean());white=float((gray>=250).mean())
  name=f'anchor-candidate-{n:04d}.png';dest=folder/name
  if dest.exists():
   if dest.read_bytes()!=r.stdout:raise ValueError('Candidate changed')
  else:dest.write_bytes(r.stdout)
  frames.append({'frame':n,'filename':name,'sha256':sha(dest),'width':a.shape[1],'height':a.shape[0],
   'sharpness':float(cv2.Laplacian(gray,cv2.CV_64F).var()),'black_fraction':black,
   'white_fraction':white,'usable':black<.98 and white<.98 and float(gray.std())>1.5})
 if sha(p['source'])!=p['source_sha256']:raise ValueError('Source changed during extraction')
 Path(p['output']).write_text(json.dumps({'binding':p['binding'],'frames':frames}))
if __name__=='__main__':main(sys.argv[1])
