"""Bounded local plan -> edit -> review loop, disabled unless explicitly invoked.
No UI/default workflow is changed. The caller owns the shared GPU lease.
"""
import asyncio,json,subprocess
from pathlib import Path
from .. import qwen_runtime as qr,qwen_image21
from ..job_cancel import checkpoint,thread_checkpoint
from . import detail_director as director,paint_runtime
from .state import atomic,digest
RECIPE={'version':director.VERSION,'model':qwen_image21.MODEL,'clip':qwen_image21.CLIP,'vae':qwen_image21.VAE,
        'steps':25,'cfg':1.,'sampler':'euler','scheduler':'simple','resolution':0,'APG':False,'FreSca':False}
def graph(p,ident,names):
 g=qwen_image21.graph(p,ident,names)
 g['encode']['inputs'].update(negative_prompt='',resolution=0)
 g['sample']['inputs'].update(steps=25,cfg=1.,sampler_name='euler',scheduler='simple')
 g['cache']['inputs']['model']=['model',0]
 for name in ('negative','apg','fresca'):g.pop(name,None)
 return g

def worker(service,folder,name,request):
 director.safe_name(name)
 script=Path(__file__).with_name('detail_worker.py');python=Path(service.settings['face_python']).resolve()
 if not python.is_file():raise ValueError('Missing installed imaging Python')
 binding={**request,'worker_sha256':digest(script),'before_sha256':digest(Path(request['before'])),
          'source_sha256':digest(Path(request['source']))}
 if 'rendered' in request:binding['rendered_sha256']=digest(Path(request['rendered']))
 manifest=folder/(name+'-input.json');receipt=folder/(name+'-receipt.json')
 if manifest.exists():
  if json.loads(manifest.read_text(encoding='utf-8'))!=binding:raise ValueError('Local edit input changed')
 else:atomic(manifest,binding)
 if receipt.exists():
  v=json.loads(receipt.read_text(encoding='utf-8'))
  if v['binding']!=binding or any(digest(Path(k))!=h for k,h in v['files'].items()):raise ValueError('Local edit output changed')
  return v['value']
 r=subprocess.run([str(python),'-B',str(script),str(manifest)],capture_output=True,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW)
 if r.returncode:raise ValueError('Local crop/composite failed: '+r.stderr.decode(errors='replace')[-900:])
 value=json.loads(r.stdout);paths=[Path(v) for k,v in value.items() if k in ('target','source','result','review_crop')]
 if any(p.parent.resolve()!=folder.resolve() for p in paths):raise ValueError('Invalid worker output path')
 atomic(receipt,{'binding':binding,'value':value,'files':{str(p):digest(p) for p in paths}})
 return value

def nonoverlapping(edits):
 for i,a in enumerate(edits):
  for b in edits[i+1:]:
   def padded(v):
    dx=(v[2]-v[0])*.2;dy=(v[3]-v[1])*.2
    return [max(0,v[0]-dx),max(0,v[1]-dy),min(1000,v[2]+dx),min(1000,v[3]+dy)]
   x,y=padded(a.result_box),padded(b.result_box)
   if min(x[2],y[2])>max(x[0],y[0]) and min(x[3],y[3])>max(x[1],y[1]):
    raise ValueError('Corrections overlap; human review required before sequential editing')

@qr.bound
async def run(service,job,source,current,refs,roles,feedback,*,before_phase=None,name='detail'):
 director.lease(service);director.safe_name(name);namespace=name;folder=service.root/'jobs'/job['id']
 if before_phase:await before_phase()
 plan=await director.plan(service,job,source,current,refs,roles,feedback,name=namespace+'-plan')
 nonoverlapping(plan.edits)
 original=current;results=[];medium_reviews=[];medium_ok=True
 for appearance in plan.appearances:
  if before_phase:await before_phase()
  v=await director.medium_review(service,job,current,refs[appearance.character-1],roles[appearance.character-1],appearance,namespace+f'-medium-{appearance.character}')
  ok=appearance.confidence>=.8 and director.medium_accepted(v)
  medium_reviews.append({'character':appearance.character,'accepted':ok,'review':v.model_dump()})
  medium_ok=medium_ok and ok
 if not plan.needs_human_review and medium_ok:
  for index,detail in enumerate(plan.edits):
   checkpoint(service,job['id'])
   if detail.part not in ('hand','mouth','gaze'):
    results.append({'index':index,'accepted':False,'reason':'requires_full_character_edit'});continue
   if detail.confidence<.8:
    results.append({'index':index,'accepted':False,'reason':'uncertain_location'});continue
   before=current;prompt=detail.prompt;attempts=[]
   for attempt in range(director.MAX_ATTEMPTS):
    checkpoint(service,job['id']);name=f'{namespace}-{index}-{attempt}'
    request={'before':str(before),'source':str(source),'detail':detail.model_dump(),'prefix':name}
    crops=await thread_checkpoint(service,job['id'],worker,service,folder,name+'-prepare',{**request,'action':'prepare'})
    if before_phase:await before_phase()
    service.store.update(job['id'],stage=f'Qwen Image · Correccion {index+1}, intento {attempt+1}')
    try:
     rendered=await paint_runtime.render(service,job,'viggle-paint-'+name,[Path(crops['target']),Path(crops['source'])],
         prompt,(job['parameters']['seed']+index*101+attempt)%9007199254739992,graph_factory=graph,recipe=RECIPE)
    except asyncio.CancelledError:
     cleanup=asyncio.create_task(paint_runtime.cancel(service,service.store.job(job['id'])))
     try:await asyncio.shield(cleanup)
     except asyncio.CancelledError:await cleanup
     raise
    comp=await thread_checkpoint(service,job['id'],worker,service,folder,name+'-compose',{**request,'action':'compose','rendered':str(rendered)})
    if not comp['outside_unchanged']:raise ValueError('Edit escaped permitted area')
    if before_phase:await before_phase()
    service.store.update(job['id'],stage=f'Qwen3.8 · Revisando correccion {index+1}, intento {attempt+1}')
    verdict=await director.review(service,job,source,before,Path(comp['result']),Path(crops['source']),Path(comp['review_crop']),refs,roles,feedback,detail,name+'-review')
    ok=director.accepted(verdict) and comp['changed_bbox'] is not None
    attempts.append({'attempt':attempt+1,'prompt':prompt,'result':comp['result'],'accepted':ok,'review':verdict.model_dump(),'outside_unchanged':True})
    if ok:current=Path(comp['result']);break
    if verdict.needs_human_review or not verdict.retry_prompt.strip():break
    prompt=verdict.retry_prompt
   results.append({'index':index,'character':detail.character,'part':detail.part,'accepted':bool(attempts and attempts[-1]['accepted']),'attempts':attempts})
 result={'version':director.VERSION,'plan':plan.model_dump(),'corrections':results,'result':str(current),
         'source_sha256':digest(source),'initial_sha256':digest(original),'result_sha256':digest(current),
         'medium_reviews':medium_reviews,'approved':medium_ok and not plan.needs_human_review and ((bool(plan.edits) and len(results)==len(plan.edits) and all(r['accepted'] for r in results)) or (not plan.edits and plan.no_changes_needed and plan.confidence>=.8)),
         'review_provider':qr.current_model(),'scope':'still-frame local details only; not full video validation'}
 atomic(folder/(namespace+'-loop-result.json'),result)
 return result
