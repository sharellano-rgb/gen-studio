"""Deterministic CPU crop/composite worker using the installed imaging Python."""
import json,math,sys
from pathlib import Path
from PIL import Image,ImageDraw,ImageFilter,ImageChops

def box_pixels(box,size):
 w,h=size
 return (max(0,int(math.floor(box[0]*w/1000))),max(0,int(math.floor(box[1]*h/1000))),
         min(w,int(math.ceil(box[2]*w/1000))),min(h,int(math.ceil(box[3]*h/1000))))
def context_box(b,size):
 w,h=size;x0,y0,x1,y1=b;edge=max(96,int(max(x1-x0,y1-y0)*2.3))
 cw,ch=min(w,edge),min(h,edge);x=max(0,min(w-cw,(x0+x1-cw)//2));y=max(0,min(h-ch,(y0+y1-ch)//2))
 return (x,y,x+cw,y+ch)
def main(path):
 p=json.loads(Path(path).read_text(encoding='utf-8'));folder=Path(path).parent
 before=Image.open(p['before']).convert('RGB');source=Image.open(p['source']).convert('RGB')
 b=box_pixels(p['detail']['result_box'],before.size);sb=box_pixels(p['detail']['source_box'],source.size)
 ctx=context_box(b,before.size);sctx=context_box(sb,source.size)
 # Include the old silhouette when closing fingers; preserve all pixels beyond
 # this bounded margin. Context itself does not authorize broader edits.
 margin=max(6,min(32,math.ceil(max(b[2]-b[0],b[3]-b[1])*.2)))
 b=(max(ctx[0],b[0]-margin),max(ctx[1],b[1]-margin),min(ctx[2],b[2]+margin),min(ctx[3],b[3]+margin))
 if (b[2]-b[0])*(b[3]-b[1])>before.width*before.height*.25:raise ValueError('Local edit plus margin is too large')
 def output(name):return folder/(p['prefix']+'-'+name+'.png')
 if p['action']=='prepare':
  before.crop(ctx).resize((512,512),Image.Resampling.LANCZOS).save(output('target'))
  source.crop(sctx).resize((512,512),Image.Resampling.LANCZOS).save(output('source'))
  value={'target':str(output('target')),'source':str(output('source')),'edit_box':b,'context_box':ctx,'source_context_box':sctx}
 elif p['action']=='compose':
  patch=Image.open(p['rendered']).convert('RGB').resize((ctx[2]-ctx[0],ctx[3]-ctx[1]),Image.Resampling.LANCZOS)
  layer=before.copy();layer.paste(patch,ctx[:2])
  hard=Image.new('L',before.size,0);ImageDraw.Draw(hard).rectangle((b[0],b[1],b[2]-1,b[3]-1),fill=255)
  mask=ImageChops.multiply(hard,hard.filter(ImageFilter.GaussianBlur(2)))
  result=Image.composite(layer,before,mask);result.save(output('composite'))
  result.crop(ctx).resize((512,512),Image.Resampling.LANCZOS).save(output('review'))
  delta=ImageChops.difference(before,result)
  outside=ImageChops.multiply(delta,Image.merge('RGB',(ImageChops.invert(hard),)*3))
  assert outside.getbbox() is None,'Pixels outside permitted edit changed'
  value={'result':str(output('composite')),'review_crop':str(output('review')),'changed_bbox':delta.getbbox(),'outside_unchanged':True}
 else:raise ValueError('Unknown deterministic action')
 print(json.dumps(value))
if __name__=='__main__':main(sys.argv[1])
