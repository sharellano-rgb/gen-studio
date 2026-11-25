"""Private chat/frame-scoped bridge to the opt-in local correction loop."""
from pathlib import Path
from .. import qwen_director as q,qwen_director_store as records,qwen_runtime as qr
from ..job_cancel import thread_checkpoint
from . import detail_director as d,detail_loop,characters
from .state import digest

@qr.bound
def inputs(service,job):
    state=records.read(service.store,job['id'])
    ctx=q.context(service,job)
    if not state or state.get('fingerprint')!=ctx['fingerprint'] or not state.get('plan',{}).get('ready'):
        raise ValueError('Falta un plan privado de Qwen vinculado a esta toma.')
    p=job['parameters'];refs=characters.references(service,p)
    roles=[{'character':i,'source_target':row['target'],'appearance_reference':i,
            'medium':'Observe and preserve this reference own rendering medium independently.'}
           for i,row in enumerate(characters.assignments(p),1)]
    frame=p.get('sequence_context',0)
    if p.get('viggle_anchor_selection_version'):
        from .anchor_selection import binding
        binding(p);current=service.store.job(job['id']);frame=current.get('viggle_anchor_frame')
        if type(frame) is not int or not 0<=frame<p['frames']:raise ValueError('Falta el fotograma seleccionado para describir los detalles.')
    feedback={'frame_scope':{'shot':p.get('sequence_shot'), 'window':p.get('sequence_index'),
              'visible_frame':frame,'source_time_seconds':ctx['source'].get('original_start_seconds',ctx['source'].get('start_seconds',0))+frame/24,'source':ctx['source']},
              'user_instructions':p.get('prompt',''),
              'current_user_messages':[m['content'] for m in state.get('messages',[]) if m['role']=='user'][-8:],
              'inherited_timed_feedback':state.get('inherited_feedback',[]),
              'director_special_notes':state['plan'].get('special_notes',''),
              'revision':state['revision'],'context_fingerprint':ctx['fingerprint'],
              'scope_rule':'Inspect this source frame. Do not force gestures from another shot or time. Source controls visible articulation; references control identity and style.'}
    return refs,roles,feedback

@qr.bound
async def apply(service,job,source,target,name):
    if job['parameters'].get(d.FIELD) and not d.enabled(job['parameters']):raise ValueError('Esta revision de detalles requiere su version original; crea otra toma.')
    if not d.enabled(job['parameters']):return target,None
    from .painted import message
    refs,roles,feedback=await thread_checkpoint(service,job['id'],inputs,service,job)
    result=await detail_loop.run(service,job,source,target,refs,roles,feedback,name=name)
    count=len(result['corrections'])
    message(service,job,result['plan']['reply_es'])
    service.store.update(job['id'],viggle_detail_review={'version':d.VERSION,'approved':result['approved'],'corrections':count})
    if not result['approved']:
        labels={'photographic':'fotográfico','illustrated':'ilustrado','mixed':'mixto','uncertain':'no confirmado'}
        for item in result.get('medium_reviews',[]):
            if not item['accepted']:
                v=item['review'];message(service,job,f"Personaje {item['character']}: el estilo de la referencia es {labels[v['reference_medium']]} y el resultado es {labels[v['result_medium']]}. Hay que revisar su apariencia antes de continuar.")
        message(service,job,'Qwen no pudo confirmar todos los detalles de este plano. Se conserva la evidencia para revisión; no se enviará esta guía a Viggle.')
        raise ValueError('La revisión de detalles necesita intervención. Consulta el chat de la toma y crea una nueva versión con tus indicaciones.')
    path=Path(result['result']);folder=service.root/'jobs'/job['id'];report=folder/(name+'-loop-result.json')
    if path.parent.resolve()!=folder.resolve() or digest(path)!=result['result_sha256']:raise ValueError('Cambió el resultado de detalles.')
    return path,{'filename':report.name,'sha256':digest(report)}

def verify(folder,proof):
    import json
    if not isinstance(proof,dict):raise ValueError('Falta la revisión de detalles de la guía aceptada.')
    path=(folder/proof['filename']).resolve()
    if path.parent!=folder.resolve() or digest(path)!=proof['sha256']:raise ValueError('Cambió la revisión de detalles aceptada.')
    report=json.loads(path.read_text(encoding='utf-8'))
    if report['version']!=d.VERSION or report['approved'] is not True:raise ValueError('La guía no tiene revisión de detalles aprobada.')
