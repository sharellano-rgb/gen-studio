"""Private primary-Comfy image phase, under the existing video GPU lease.
No accepted submission is retried; unknown submissions reconcile by exact metadata.
"""
import asyncio,json,time
from pathlib import Path
from uuid import uuid4
import httpx
from .state import atomic,digest,graph_hash
from .graphs import relative
from .adapter import SubmissionUnknown
from ..job_cancel import checkpoint,entries
from ..qwen_image21 import graph as image_graph,RECIPE
from ..workflows import check_graph
FIELD='viggle_paint_runtime'

def pending(job):
 s=job.get(FIELD) or {}
 return bool(s.get('attempted') and not s.get('completed') and not s.get('cancelled'))

async def idle(service,own=None):
 bases=[service.settings['comfy_url'],*service.settings.get('viggle_external_comfy_urls',['http://127.0.0.1:8199','http://127.0.0.1:8190','http://127.0.0.1:8191','http://127.0.0.1:8197'])]
 for base in dict.fromkeys(bases):
  try:q=await service.get(base,'/queue')
  except httpx.ConnectError:
   if base==service.settings['comfy_url']:raise
   continue
  if any(base!=service.settings['comfy_url'] or row[1]!=own for row in entries(q)):
   raise ValueError('Otro trabajo utiliza la GPU; no se interrumpirá para pintar la referencia.')
 if (await service.get(service.settings['ollama_url'],'/api/ps')).get('models'):
  raise ValueError('Qwen debe liberar la GPU antes de editar el fotograma.')

async def free(service):
 if not entries(await service.get(service.settings['comfy_url'],'/queue')):
  await service.post(service.settings['comfy_url'],'/free',json={'unload_models':True,'free_memory':True})

async def reconcile(service,job,s):
 base=service.settings['comfy_url'];queue=await service.get(base,'/queue');history=await service.get(base,'/history');matches=set()
 for row in [*entries(queue),*(v.get('prompt',[]) for v in history.values())]:
  if not isinstance(row,(list,tuple)) or len(row)<4:continue
  extra=row[3]
  if isinstance(extra,dict) and extra.get('client_id')==s['client_id'] and extra.get('viggle_paint_job')==job['id'] and extra.get('workspace')==s['binding']['workspace'] and extra.get('graph_sha256')==s['graph_sha256'] and graph_hash(row[2])==s['graph_sha256']:
   matches.add(row[1])
 if len(matches)!=1:raise SubmissionUnknown('No se confirmó el envío del fotograma editado. Se conserva su registro y no se reenvía.')
 return matches.pop()

async def cancel(service,job):
 s=job.get(FIELD) or {}
 if not pending(job):return
 pid=s.get('prompt_id') or await reconcile(service,job,s);base=service.settings['comfy_url']
 await service.post(base,'/api/jobs/'+pid+'/cancel')
 for _ in range(60):
  if not any(row[1]==pid for row in entries(await service.get(base,'/queue'))):
   s={**s,'prompt_id':pid,'cancelled':True};atomic(service.root/'jobs'/job['id']/(s['phase']+'-receipt.json'),s)
   service.store.update(job['id'],**{FIELD:s},prompt_id=None);await free(service);return
  await asyncio.sleep(.5)
 raise SubmissionUnknown('No se confirmó la cancelación del editor de imágenes.')

async def render(service,job,phase,paths,prompt,seed,*,graph_factory=None,recipe=None):
 ident=job['id'];folder=service.root/'jobs'/ident;base=service.settings['comfy_url']
 if not phase.startswith('viggle-paint-') or not phase.replace('-','').isalnum():raise ValueError('Fase de imagen inválida.')
 if not 2<=len(paths)<=4:raise ValueError('El editor requiere escena y de una a tres referencias por pasada.')
 key={'workspace':service.settings.get('workspace_id','owner'),'job_id':ident,'phase':phase,'inputs':[digest(p) for p in paths],'prompt':prompt,'seed':seed,'recipe':RECIPE if recipe is None else recipe,'qwen_binding':job.get('qwen_model_binding')}
 receipt=folder/(phase+'-receipt.json');workflow=folder/(phase+'-workflow.json');target=folder/(phase+'.png')
 s=json.loads(receipt.read_text(encoding='utf-8')) if receipt.exists() else None
 if s:
  if s.get('binding')!=key or not workflow.exists() or graph_hash(json.loads(workflow.read_text(encoding='utf-8')))!=s.get('graph_sha256'):raise ValueError('Cambió una referencia o el gráfico del fotograma aceptado.')
  if s.get('cancelled'):raise ValueError('El fotograma fue cancelado; crea otra toma.')
  if s.get('completed'):
   if s.get('failed'):raise ValueError('La edición aceptada falló; crea otra toma.')
   if not target.is_file() or digest(target)!=s['sha256']:raise ValueError('Cambió el fotograma editado.')
   if s.get('failed'):raise ValueError('La edición aceptada falló; crea otra toma.')
   active=service.store.job(ident).get(FIELD) or {}
   if active.get('phase')==phase:service.store.update(ident,**{FIELD:s},prompt_id=None)
   # Do not overwrite a later pending pass while replaying earlier completed ones.
   return target
 else:
  if pending(service.store.job(ident)):raise SubmissionUnknown('Hay otra edición pendiente de recuperar.')
  await idle(service);names=[]
  for path in paths:
   checkpoint(service,ident)
   with Path(path).open('rb') as f:u=await service.post(base,'/upload/image',files={'image':(digest(path)[:20]+Path(path).suffix,f)},data={'overwrite':'false','subfolder':'gen-studio-viggle-paint/'+ident},timeout=120)
   names.append(relative('/'.join(x for x in (u.get('subfolder',''),u['name']) if x)))
  p={'mode':'edit','quality':'standard','prompt':prompt,'negative_prompt':'','steps':20,'cfg':3.,'seed':seed,'extra_reference_ids':list(range(len(paths)-1))}
  g=(graph_factory or image_graph)(p,ident+'/'+phase,names);checked=await check_graph(service.client,base,g)
  if not checked['ok']:raise ValueError(' · '.join(checked['errors']))
  atomic(workflow,g);s={'binding':key,'phase':phase,'graph_sha256':graph_hash(g),'client_id':str(uuid4()),'attempted':True}
  checkpoint(service,ident);atomic(receipt,s)
  service.store.update(ident,status='submitting',stage='Qwen Image Edit · Preparando fotograma del plano',**{FIELD:s})
  try:
   r=await service.post(base,'/prompt',json={'prompt':g,'client_id':s['client_id'],'extra_data':{'viggle_paint_job':ident,'workspace':key['workspace'],'graph_sha256':s['graph_sha256']}},timeout=60);s['prompt_id']=r['prompt_id']
  except Exception:s['prompt_id']=await reconcile(service,job,s)
  atomic(receipt,s)
 pid=s.get('prompt_id') or await reconcile(service,job,s);s['prompt_id']=pid;atomic(receipt,s)
 service.store.update(ident,status='running',stage='Qwen Image Edit · Editando personajes del plano',**{FIELD:s},prompt_id=pid)
 started=time.monotonic();missing=0
 while True:
  checkpoint(service,ident);await idle(service,pid)
  try:history=await service.get(base,'/history/'+pid)
  except httpx.HTTPError as e:raise SubmissionUnknown('Se perdió la conexión con la edición aceptada; no se reenvía.') from e
  if pid in history:
   entry=history[pid];atomic(folder/(phase+'-history.json'),entry)
   if entry.get('status',{}).get('status_str')!='success':
    s.update(completed=True,failed=True);atomic(receipt,s);service.store.update(ident,**{FIELD:s},prompt_id=None);await free(service)
    raise ValueError('Falló Qwen Image Edit: '+str(entry.get('status',{}))[:800])
   from ..pika_ref2va.swap_guide import download
   await download(service,job,entry,'save','.png',target)
   from ..media import probe
   info=probe(target)
   if info.get('codec')!='png' or min(info['width'],info['height'])<64:raise ValueError('El editor no devolvió un PNG válido.')
   s.update(completed=True,sha256=digest(target),width=info['width'],height=info['height']);atomic(receipt,s)
   service.store.update(ident,**{FIELD:s},prompt_id=None,status='preparing',stage='Fotograma editado · Liberando GPU para revisar')
   await free(service);return target
  q=await service.get(base,'/queue');missing=0 if any(row[1]==pid for row in entries(q)) else missing+1
  if missing>=3 or time.monotonic()-started>1200:raise SubmissionUnknown('La edición aceptada no terminó o perdió su historial; se conserva para recuperación.')
  service.record_engine_contact(ident,'running');await asyncio.sleep(2)
