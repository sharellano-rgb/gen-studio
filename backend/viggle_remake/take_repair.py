"""Prepare a second pin from a generated take in a NEW private job.

No endpoint, automatic retry, publication or mutation of the original take.
The caller must create/direct a new version and own the shared GPU lease.
"""
import json
from pathlib import Path
from .. import qwen_runtime as qr
from ..job_cancel import thread_checkpoint,checkpoint
from . import media,detail_director,detail_integration,detail_loop
from .state import atomic,digest
VERSION='viggle-take-repair-1'

def pin_contract(frame,p,pins):
 if type(frame) is not int:raise ValueError('Se requiere un Ã­ndice exacto de fotograma.')
 begin=p.get('sequence_context',0)
 end=min(p.get('sequence_input_keep',p['frames']),begin+p.get('sequence_keep',p['frames']))
 if not begin+3<=frame<end-3:raise ValueError('La reparaciÃ³n debe estar dentro de la parte visible del plano, lejos del corte y sin relleno.')
 if len(pins)>=4 or any(type(x) is not int or abs(frame-x)<72 for x in pins):
  raise ValueError('La segunda referencia necesita separaciÃ³n de 72 fotogramas y un mÃ¡ximo de cuatro referencias.')
 return sorted([*pins,frame])

def validate_origin(service,job,origin,frame):
 if not origin or origin['id']==job['id'] or origin.get('status')!='completed':
  raise ValueError('La reparaciÃ³n requiere otra toma terminada del mismo espacio.')
 p=job['parameters'];op=origin['parameters']
 if p.get('viggle_take_repair_version')!=VERSION or p.get('repair_from_job')!=origin['id']:
  raise ValueError('La nueva versiÃ³n no estÃ¡ vinculada a la toma que se repararÃ¡.')
 if origin['parameters'].get('engine')!='viggle_remake' or job.get('prompt_id') or job.get('viggle_submission_attempted'):
  raise ValueError('No se modifica un envÃ­o aceptado ni otro motor.')
 if job.get('status') not in ('preparing','refining'):raise ValueError('La versiÃ³n de reparaciÃ³n no estÃ¡ en preparaciÃ³n.')
 for key in ('video_id','source_sha256','reference_id','reference_sha256','additional_characters',
             'additional_reference_sha256','resolution','frames','sequence_context','sequence_keep',
             'sequence_shot','sequence_start_frame'):
  if p.get(key)!=op.get(key):raise ValueError('La reparaciÃ³n cambiÃ³ las entradas o el plano: '+key)
 pins=[origin.get('viggle_anchor_frame',op.get('pin_frame') or 0)]
 return pin_contract(frame,p,pins)

@qr.bound
async def prepare(service,job,frame,*,execute=False):
 detail_director.lease(service)
 origin=service.store.job(job['parameters'].get('repair_from_job',''))
 pins=validate_origin(service,job,origin,frame)
 folder=service.root/'jobs'/job['id'];folder.mkdir(parents=True,exist_ok=True)
 old=service.root/'jobs'/origin['id'];source=old/'viggle-source.mp4'
 result=service.root/'results'/(origin['id']+'.mp4')
 if not source.is_file() or not result.is_file():raise ValueError('Falta el vÃ­deo privado de origen o resultado.')
 receipt=json.loads((old/'viggle-source.json').read_text())
 if digest(source)!=receipt['sha256'] or digest(result)!=origin.get('viggle_result_sha256'):
  raise ValueError('CambiÃ³ un vÃ­deo aprobado; no se prepararÃ¡ una reparaciÃ³n.')
 key={'version':VERSION,'workspace':str(service.root.resolve()),'job':job['id'],'origin_job':origin['id'],
      'source_sha256':digest(source),'result_sha256':digest(result),'frame':frame,'pins':pins,
      'qwen':job.get(qr.FIELD)}
 record=folder/'take-repair-input.json'
 if record.exists() and json.loads(record.read_text())!=key:raise ValueError('CambiÃ³ el contexto de reparaciÃ³n.')
 if not record.exists():atomic(record,key)
 paths=[]
 for label,path in [('source',source),('take',result)]:
  dest=folder/f'take-repair-{label}.png'
  if not dest.exists():
   await thread_checkpoint(service,job['id'],media.run,['-i',path,'-vf',f'select=eq(n\\,{frame})','-frames:v',1,'-update',1,dest])
  paths.append(dest)
 # Store and verify the exact pair before any Qwen request or image edit.
 proof={p.name:digest(p) for p in paths};pair=folder/'take-repair-pair.json'
 if pair.exists() and json.loads(pair.read_text())!=proof:raise ValueError('CambiÃ³ el par de fotogramas extraÃ­do.')
 if not pair.exists():atomic(pair,proof)
 service.store.update(job['id'],viggle_anchor_frame=frame)
 refs,roles,feedback=await thread_checkpoint(service,job['id'],detail_integration.inputs,service,job)
 feedback['frame_scope'].update(visible_frame=frame,
    source_time_seconds=feedback['frame_scope']['source'].get('original_start_seconds',feedback['frame_scope']['source'].get('start_seconds',0))+frame/24)
 feedback['repair_scope']='Repair this generated take, using the matching SOURCE instant for pose. Never repaint the whole scene. This creates a candidate pin for a NEW render, not a corrected video.'
 if execute:
  checked=await detail_loop.run(service,job,*paths,refs,roles,feedback,name='take-repair')
  report={'binding':key,'plan':checked['plan'],'approved':checked['approved'],
          'candidate':checked['result'],'candidate_sha256':checked['result_sha256']}
 else:
  plan=await detail_director.plan(service,job,*paths,refs,roles,feedback,name='take-repair-plan')
  report={'binding':key,'plan':plan.model_dump(),'approved':False,'candidate':None,'planning_only':True}
 checkpoint(service,job['id'])
 report.update(video_repaired=False,requires_new_render=True,original_take_modified=False,
               background_guarantee='outside local correction only relative to generated take; original-background compositor still required')
 atomic(folder/('take-repair-executed.json' if execute else 'take-repair-proposal.json'),report)
 return report
