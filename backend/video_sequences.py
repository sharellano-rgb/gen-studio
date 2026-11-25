"""Shared five-second orchestration. Native methods run as immutable child jobs."""
import asyncio,json,shutil
from copy import deepcopy
from pathlib import Path
from uuid import UUID,uuid4,uuid5,NAMESPACE_URL
from fastapi import HTTPException
from fastapi.responses import FileResponse
from .sequence_plan import VERSION,FAITHFUL_VERSION,VIGGLE_VERSION,DIRECT_7S_VERSION,version_for,window_size,context_for_version,plan as windows
from .head import digest
from .guided_composition import local_path
from .job_cancel import checkpoint,thread_checkpoint
from .long_video import media
from . import qwen_global as whole_video

FINAL={'completed','failed','cancelled','submission_unknown'}

def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    data=json.dumps(value,ensure_ascii=False,indent=2)
    temp=path.with_suffix('.tmp');temp.write_text(data,encoding='utf-8');temp.replace(path)

def file_checked(service,path,sha):
    path=local_path(service.root,path)
    if not path.is_file() or digest(path)!=sha:raise ValueError('Cambió un archivo registrado de la secuencia.')
    return path

def source(service,job):
    p=job['parameters'];asset=service.store.asset(p['video_id'])
    if not asset or asset['kind']!='video':raise ValueError('El vídeo no pertenece a este espacio.')
    path=local_path(service.root,service.root/'inputs'/asset['filename'])
    expected=p.get('direct_source_sha256') or p.get('alisson_source_sha256') or p.get('video_sha256') or p.get('source_sha256')
    if expected and digest(path)!=expected:raise ValueError('Cambió el vídeo original.')
    return asset,path

def child_id(parent,index):
    """Resolve a pinned replacement attempt, retaining earlier immutable child jobs."""
    if isinstance(parent,dict):
        revision=parent.get('sequence_revisions',{}).get(str(index))
        base=str(uuid5(NAMESPACE_URL,parent['id']+(f'/sequence/{index}/revision/{revision}' if revision else f'/sequence/{index}')))
        return parent.get('sequence_approved_variants',{}).get(base,base)
    return str(uuid5(NAMESPACE_URL,parent+f'/sequence/{index}'))

def load(service,job,verify=True):
    path=service.root/'jobs'/job['id']/'sequence-plan.json'
    if not path.exists():return None
    record=json.loads(path.read_text(encoding='utf-8'))
    version=version_for(job['parameters'])
    if job.get('video_sequence_version',version)!=version:raise ValueError('La toma no conserva su política de continuidad.')
    if record['version']!=version or record['frames']!=job['parameters']['frames']:raise ValueError('Cambió la versión o duración de la secuencia.')
    expected=windows(record['frames'],record['cuts'],context_frames=context_for_version(version),chunk_frames=window_size(version))
    if len(record['segments'])!=len(expected) or any(any(s.get(k)!=v for k,v in e.items()) for s,e in zip(record['segments'],expected)):
        raise ValueError('El plan no conserva los límites de sus planos y tramos.')
    if verify:
        for s in record['segments']:file_checked(service,s['path'],s['sha256'])
        file_checked(service,record['normalized'],record['normalized_sha256'])
    return record

def parameters(parent,segment,previous=None):
    p=deepcopy(parent['parameters']);engine=p['engine']
    p.update(video_id=segment['video_id'],frames=segment['render_frames'],start_seconds=0,start_frame=0,
        sequence_parent_id=parent['id'],sequence_index=segment['index'],sequence_shot=segment['shot'],
        sequence_start_frame=segment['input_start'],sequence_keep=segment['keep'],sequence_context=segment['context_frames'],
        sequence_guide_version=version_for(p))
    for key in ('direct_source_sha256','alisson_source_sha256','source_sha256','video_sha256'):
        if key in p:p[key]=segment['sha256']
    if 'render_frames' in p:p['render_frames']=segment['render_frames']
    if engine=='h3_direct':
        edits=p.get('direct_shot_edits',[])
        edit=next((e for e in edits if e['index']==segment['shot']),None)
        scoped_cast=any(e.get('disabled_character_ids') for e in edits)
        if (edit or scoped_cast) and not (edit or {}).get('passthrough'):
            from .studio_editor import shot_payload
            from .h3_marked_target import FIELD as target_field, VERSION as target_version
            marked=parent.get(target_field)==target_version and bool((edit or {}).get('target_selections'))
            p=shot_payload(p,{'shot_edit':edit or {'index':segment['shot']}},scoped_cast=scoped_cast or marked)
            if marked and p.get('direct_dynamic_prompts') and p.get('direct_global_goal'):
                p['prompt']+='\nAdditional scene direction for the confirmed subjects: '+p['direct_global_goal']
        p.pop('operation',None);p['direct_start_seconds']=0
        for key in ('direct_chunk_frames','direct_chunk_version','direct_continuity_version','direct_continuity_guide','direct_shot_plan_id','direct_shot_edits'):
            p.pop(key,None)
        p.update(direct_continuity=False,direct_scene_cuts=False,direct_dynamic_prompts=False)
    if p.get('transfer_method')=='bunny':
        p['bunny_version']='bunny-v1'
        for key in ('bunny_chunk_frames','bunny_continuity_version'):p.pop(key,None)
    cast_revision=parent.get('sequence_cast_inputs',{}).get(str(segment['index']))
    if cast_revision and engine=='h3_direct':
        for field in ('direct_characters','direct_targets','direct_reference_ids','direct_reference_hashes',
                      'reference_id','face_reference_id','direct_visual_target_proofs','prompt'):
            p[field]=deepcopy(cast_revision[field])
    ensemble=parent.get('sequence_ensemble_references',{}).get(str(segment['index']))
    if engine=='h3_direct' and ensemble:
        p['sequence_ensemble_reference']=deepcopy(ensemble)
    if previous and segment['context_frames']:
        p['sequence_previous_id']=previous['id']
        pp=previous['parameters'];p['sequence_previous_frame']=pp['sequence_context']+pp['sequence_keep']-1
    return p

def active_child(service,job):
    child=service.store.job(job.get('sequence_active_child') or job.get('ninja_active_child',''))
    return child if child and (child['parameters'].get('sequence_parent_id') or child['parameters'].get('ninja_sequence_owner'))==job['id'] else None

def reuse_parameters(parameters,prior,segment):
    if not prior or prior['parameters'].get('video_sha256')!=segment['sha256']:
        raise ValueError('Cambió el vídeo del tramo que quieres reutilizar.')
    p=prior['parameters']
    return {**parameters,'reuse_character_job_id':prior['id'],'video_id':p['video_id'],
        'video_sha256':p['video_sha256'],'x':p['x'],'y':p['y']}

async def process(service,job):
    ident=job['id'];p=job['parameters'];folder=service.root/'jobs'/ident;folder.mkdir(parents=True,exist_ok=True)
    checkpoint(service,ident);asset,path=await thread_checkpoint(service,ident,source,service,job)
    record=await thread_checkpoint(service,ident,load,service,job)
    if record is None:
        start=round(float(p.get('direct_start_seconds',p.get('start_seconds',0)))*24)
        inputs=dict(source=str(path),source_sha256=digest(path),frames=p['frames'],start_frame=start,
            width=int(asset.get('display_width',asset['width']))//2*2,height=int(asset.get('display_height',asset['height']))//2*2,
            job_id=ident,face_python=service.settings.get('face_python'))
        inputs['sequence_version']=version_for(p)
        if p.get('direct_shot_plan_id'):
            from .studio_editor import reviewed_plan
            reviewed=await asyncio.to_thread(reviewed_plan,service,p)
            inputs['reviewed_cuts']=reviewed['cuts']
        service.store.update(ident,stage='Analizando cambios de plano y preparando tramos',status='preparing')
        record=await media(service,job,'sequence-plan',inputs)
        checkpoint(service,ident);save(folder/'sequence-plan.json',record)
    service.store.update(ident,sequence_total=len(record['segments']),sequence_shots=len(record['cuts'])+1,
        sequence_cuts=record['cuts'],sequence_plan_ready=True)
    if whole_video.enabled(job):
        await whole_video.ensure(service,job,record)
        checkpoint(service,ident)
        job=service.store.job(ident)
    shared_sheet=None
    if p.get('auto_character_sheet'):
        from .alisson_sheet import ensure
        from .ninja_swap import asset_path
        shared_sheet=await ensure(service,job,asset_path(service,p['reference_id'],'image'),asset_path(service,p['face_reference_id'],'image') if p.get('face_reference_id') else None)
        if shared_sheet is None:return
    outputs=[];identity_outputs=[];face_review_segments=[];previous=None
    reuse=service.store.job(p.get('reuse_character_job_id',''))
    if reuse and reuse.get('video_sequence_version'):
        old=await thread_checkpoint(service,ident,load,service,reuse)
        if old['cuts']!=record['cuts']:raise ValueError('Cambió la división de la toma que quieres reutilizar.')
    for segment in record['segments']:
        checkpoint(service,ident)
        clip=service.root/'inputs'/(segment['video_id']+'.mp4')
        if not clip.exists():await thread_checkpoint(service,ident,shutil.copy2,segment['path'],clip)
        await thread_checkpoint(service,ident,file_checked,service,clip,segment['sha256'])
        if not service.store.asset(segment['video_id']):
            service.store.add_asset(dict(id=segment['video_id'],filename=clip.name,kind='video',name=f'Tramo {segment["index"]+1} · Plano {segment["shot"]+1}',
                width=record['width'],height=record['height'],duration=segment['render_frames']/24,sha256=segment['sha256'],sequence_internal=True))
        cp=parameters(job,segment,previous)
        cp['sequence_input_keep']=segment['input_keep']
        if p.get('operation')=='motion_layers':
            selection=folder/f'selection-{segment["index"]}.json'
            if reuse and reuse.get('video_sequence_version'):
                prior=service.store.job(child_id(reuse,segment['index']))
                if not prior:raise ValueError('Falta la primera pasada de este tramo.')
                cp=reuse_parameters(cp,prior,segment)
            elif selection.exists():cp.update(json.loads(selection.read_text()))
            elif segment['starts_at_cut']:
                service.store.update(ident,status='queued',sequence_waiting=True,sequence_index=segment['index'],
                    stage='Cambio de plano · Marca el personaje para continuar',progress=None)
                return
            elif previous and segment['context_frames']:
                point=await media(service,job,'sequence-point',dict(previous_folder=str(service.root/'jobs'/previous['id']),
                    frame_index=cp['sequence_previous_frame'],parameters=previous['parameters'],sha256=previous.get('layers_raw_mask_sha256')))
                cp.update(x=point['x'],y=point['y']);save(selection,{'x':point['x'],'y':point['y']})
        shot_edit=next((e for e in p.get('direct_shot_edits',[]) if e['index']==segment['shot']),{})
        if shot_edit.get('passthrough'):
            outputs.append(dict(path=segment['path'],sha256=segment['sha256'],keep=segment['keep'],context_frames=segment['context_frames']))
            previous=None;service.store.update(ident,sequence_done=segment['index']+1);continue
        if shared_sheet:
            _,sheet=shared_sheet
            cp.update(auto_character_sheet=False,reference_id=sheet['asset_id'],alisson_reference_sha256=sheet['sha256'],
                alisson_effective_reference_sha256=sheet['sha256'],alisson_sheet_reference=True)
        jid=child_id(job,segment['index'])
        if jid in job.get('sequence_approved_variants',{}).values():
            child=service.store.job(jid)
            if not child or child['status']!='completed' or not child.get('sequence_manual_approval'):
                raise ValueError('Falta el intento aprobado de este tramo.')
        else:
            child=service.store.create_job(jid,jid,cp)
        service.store.update(ident,sequence_active_child=jid,sequence_index=segment['index'],sequence_current_shot=segment['shot'],
            status='running',stage=f'Plano {segment["shot"]+1} · Tramo {segment["index"]+1}/{len(record["segments"])}',progress=None)
        if child['status'] not in FINAL:await service._run(child)
        child=service.store.job(jid)
        if child.get('human_segment_review') and child['status']=='cancelled':
            service.store.update(ident,status='cancelled',sequence_done=segment['index'],progress=None,
                stage=f'Tramo {segment["index"]+1} generado · Pendiente de tu revisión')
            return
        if child['status']=='submission_unknown':
            service.store.update(ident,status='submission_unknown',stage='Envío del tramo sin confirmar; no se repetirá',error=child.get('error'));return
        if child['status']=='queued':service.store.update(ident,status='queued',stage=child['stage']);return
        if child['status']!='completed':raise ValueError(f'Tramo {segment["index"]+1}: '+(child.get('error') or child['status']))
        if (child.get('direct_review') or {}).get('human_review')=='rejected':
            raise ValueError(f'Tramo {segment["index"]+1} rechazado por el usuario. Continúa desde ese tramo.')
        output=service.root/'results'/(jid+'.mp4')
        if not child.get('result_available') or not output.is_file():raise ValueError('Falta el resultado de un tramo.')
        sha=await thread_checkpoint(service,ident,digest,output)
        recorded=next((child.get(k) for k in ('direct_result_sha256','alisson_result_sha256','secourses_result_sha256','motion_result_sha256','layers_result_sha256','pika_result_sha256','viggle_result_sha256') if child.get(k)),None)
        if recorded and recorded!=sha:raise ValueError('Cambió el resultado del tramo.')
        receipt={'path':str(output),'sha256':sha,'keep':segment['keep'],'context_frames':segment['context_frames']}
        outputs.append(receipt);previous=child
        face_review=child.get('direct_fidelity_review')
        if face_review and face_review['status']=='review' and not (child.get('direct_review') or {}).get('approved_result_sha256')==sha:
            face_review_segments.append(segment['index']+1)
        if p.get('operation')=='motion_layers':
            from .motion_layers import character_receipt
            intermediate=await thread_checkpoint(service,ident,character_receipt,service,child)
            identity_outputs.append({**receipt,'path':intermediate['video'],'sha256':intermediate['video_sha256']})
        service.store.update(ident,sequence_done=segment['index']+1)
    checkpoint(service,ident);await thread_checkpoint(service,ident,source,service,job)
    if identity_outputs and not service.store.job(ident).get('layers_character_ready'):
        from .motion_layers import publish_character
        from .continuity import VERSION as CONTEXT
        w,h=map(int,p['resolution'].split('x'))
        intermediate=await media(service,job,'join',dict(frames=p['frames'],width=w,height=h,chunk_frames=120,
            continuity_version=CONTEXT,segments=identity_outputs,audio_source=record['normalized'],audio_source_sha256=record['normalized_sha256']))
        # Sequence receipts are reused window by window, never sent whole to a sampler.
        await publish_character(service,job,intermediate['path'],intermediate['path'],intermediate['sha256'],intermediate['sha256'])
    saved=folder/'sequence-result.json'
    if saved.exists():result=json.loads(saved.read_text(encoding='utf-8'))
    else:
        from .continuity import VERSION as CONTEXT
        from .media import probe
        width,height=map(int,p['resolution'].split('x'))
        service.store.update(ident,stage='Uniendo tramos y recuperando el audio original')
        result=await media(service,job,'join',dict(frames=p['frames'],width=width,height=height,chunk_frames=window_size(record['version']),
            continuity_version=version_for(p) if version_for(p) in (FAITHFUL_VERSION,VIGGLE_VERSION) else CONTEXT,segments=outputs,audio_source=record['normalized'],audio_source_sha256=record['normalized_sha256']))
        checkpoint(service,ident);save(saved,result)
    file_checked(service,result['path'],result['sha256'])
    target=service.root/'results'/(ident+'.mp4');target.parent.mkdir(exist_ok=True)
    if target.exists():file_checked(service,target,result['sha256'])
    else:await thread_checkpoint(service,ident,shutil.copy2,result['path'],target)
    sha_key={'viggle_remake':'viggle_result_sha256','pika_ref2va':'pika_result_sha256','h3_direct':'direct_result_sha256','h3_alisson':'alisson_result_sha256','h3_secourses':'secourses_result_sha256'}.get(p['engine'],'layers_result_sha256' if p.get('operation')=='motion_layers' else 'motion_result_sha256')
    stage='Vídeo completo listo · Revisar tramos '+', '.join(map(str,face_review_segments)) if face_review_segments else 'Vídeo completo listo · Continuidad por planos'
    from .h3_face_policy import FIELD, GUIDED_VERSIONS
    review_fields={'sequence_face_review_segments':face_review_segments} if p.get('engine')=='h3_direct' and job.get(FIELD) in GUIDED_VERSIONS else {}
    service.store.update(ident,status='completed',result_available=True,stage=stage,progress=None,
        scene_resolution=p['resolution'],sequence_result_sha256=result['sha256'],**{sha_key:result['sha256']},**review_fields)
    await summarize_director(service,service.store.job(ident),record)

async def summarize_director(service,job,record):
    if not job.get('qwen_director_version'):return
    from . import qwen_director as director
    old=director.records.read(service.store,job['id']) or {}
    global_enabled=whole_video.enabled(job)
    if old.get('plan') and not global_enabled:return
    states=[]
    for s in record['segments']:
        state=director.records.read(service.store,child_id(job,s['index']))
        if state and state.get('plan'):states.append({'start':s['start']/24,'end':(s['start']+s['keep'])/24,'shot':s['shot'],
            'observations':state.get('observations'),'plan':state['plan']})
    if not states:return
    ctx=await asyncio.to_thread(director.context,service,job)
    plan=deepcopy(old.get('plan') or states[0]['plan']);plan['reply_es']=f'Vídeo generado en {len(record["segments"])} tramos y {len(record["cuts"])+1} planos. Puedes comentar el vídeo completo e indicar el segundo que quieras corregir.'
    observations={'sequence':states}
    if global_enabled:observations['global_context']=await thread_checkpoint(service,job['id'],whole_video.scoped,service,job)
    revision=(old.get('revision') or 1) if global_enabled else 1
    director.publish(service,job,{**(deepcopy(old) if global_enabled else {}),
        'version':director.VERSION,'context':ctx,'fingerprint':ctx['fingerprint'],
        'observations':observations,'plan':plan,'revision':revision,'phases':{},'messages':old.get('messages',[])+[{'role':'assistant','content':plan['reply_es']}]})
    service.store.update(job['id'],qwen_applied_revision=revision,qwen_question_job_id=None,qwen_waiting=False)

async def before_phase(service,job,name,graph,*,upload_fn=None):
    p=job['parameters'];previous_id=p.get('sequence_previous_id')
    if not previous_id:return graph
    if not any(n['class_type']=='MiniMaxH3ReferenceToVideo' for n in graph.values()):return graph
    from .sequence_guides import apply
    from .motion_transfer import upload
    previous=service.store.job(previous_id);owner=p.get('sequence_parent_id') or p.get('ninja_sequence_owner')
    pp=(previous or {}).get('parameters',{})
    version=version_for(p);context=p.get('sequence_context')
    if p.get('engine')=='h3_direct' and p.get('sequence_guide_version')==DIRECT_7S_VERSION:
        parent=service.store.job(owner) if owner else None
        if not parent or parent.get('video_sequence_version')!=DIRECT_7S_VERSION:
            raise ValueError('El tramo no corresponde a la política de continuidad de la toma.')
        version=DIRECT_7S_VERSION
    expected_context=context_for_version(version)
    if (p.get('sequence_guide_version')!=version or not previous or previous['status']!='completed'
            or not owner or (pp.get('sequence_parent_id') or pp.get('ninja_sequence_owner'))!=owner
            or p.get('sequence_index')!=pp.get('sequence_index',-2)+1 or p.get('sequence_shot')!=pp.get('sequence_shot')
            or type(context) is not int or context!=expected_context
            or p.get('sequence_previous_frame')!=pp.get('sequence_context',0)+pp.get('sequence_keep',0)-1):raise ValueError('La continuidad no pertenece a este plano y tramo.')
    if version==FAITHFUL_VERSION:
        parent=service.store.job(owner)
        if (not parent or parent.get('video_sequence_version')!=FAITHFUL_VERSION
                or version_for(pp)!=FAITHFUL_VERSION or not previous.get('result_available')
                or pp.get('sequence_keep',0)<context
                or p['sequence_start_frame']+context!=pp['sequence_start_frame']+pp['sequence_context']+pp['sequence_keep']):
            raise ValueError('Los fotogramas de continuidad no pertenecen al intervalo visible anterior.')
    file=service.root/'results'/(previous_id+'.mp4');index=p['sequence_previous_frame']
    if name=='layers-identity':
        from .motion_layers import character_receipt
        intermediate=await thread_checkpoint(service,job['id'],character_receipt,service,previous)
        file=Path(intermediate['video'])
    sha=await thread_checkpoint(service,job['id'],digest,file)
    expected=intermediate['video_sha256'] if name=='layers-identity' else next((previous.get(k) for k in
        ('direct_result_sha256','alisson_result_sha256','secourses_result_sha256','motion_result_sha256','layers_result_sha256','ninja_result_sha256','pika_result_sha256') if previous.get(k)),None)
    if version==FAITHFUL_VERSION and not expected:raise ValueError('Falta el hash del vídeo anterior de la guía fiel.')
    if expected and sha!=expected:raise ValueError('Cambió el resultado anterior usado para continuidad.')
    folder=service.root/'jobs'/job['id'];receipt=folder/('sequence-guide-'+name+'.json')
    binding={'previous':previous_id,'source_sha256':sha,'frame_index':index}
    if version==FAITHFUL_VERSION:binding.update(version=version,frame_start=index-context+1,frame_count=context)
    if receipt.exists():
        saved=json.loads(receipt.read_text(encoding='utf-8'))
        if saved['binding']!=binding:raise ValueError('Cambió el fotograma registrado para continuidad.')
        await thread_checkpoint(service,job['id'],file_checked,service,saved['path'],saved['sha256'])
    else:
        from .media import probe
        info=await thread_checkpoint(service,job['id'],probe,file)
        guide_inputs=dict(source=str(file),source_sha256=sha,frame_index=index,width=info['width'],height=info['height'])
        if version==FAITHFUL_VERSION:guide_inputs.update(frame_start=index-context+1,frame_count=context,sequence_version=version)
        guide=await media(service,job,'guide-clip' if version==FAITHFUL_VERSION else 'guide',guide_inputs)
        image=await (upload_fn or upload)(service,guide['path'],job['id'])
        saved={**guide,'binding':binding,'image_name':image};save(receipt,saved)
    result,changed=apply(graph,saved['image_name'],guide_frames=context) if version==FAITHFUL_VERSION else apply(graph,saved['image_name'])
    service.store.update(job['id'],sequence_guide_applied=True,sequence_guide_samplers=changed,
        **({'sequence_guide_frames':context} if version==FAITHFUL_VERSION else {}))
    return result

def resume_sequence(service,job,record,index,expected_child):
    segments=record['segments'] if record else []
    assemble_only=bool(segments) and index==len(segments) and job.get('sequence_approved_index')==index-1 and job.get('sequence_done')==index
    if not assemble_only and not 0<=index<len(segments):
        raise HTTPException(404,'Tramo no encontrado.')
    if job['status'] not in ('failed','cancelled') or service.active_job_id==job['id']:
        raise HTTPException(409,'El trabajo debe haber terminado de detenerse para continuar.')
    if str(expected_child)!=child_id(job,index-1 if assemble_only else index):
        raise HTTPException(409,'Este tramo ya tiene un intento más reciente. Actualiza la toma.')
    passthrough={e['index'] for e in job['parameters'].get('direct_shot_edits',[]) if e.get('passthrough')}
    for seg in segments:
        number=seg['index']
        child=service.store.job(child_id(job,number))
        unsubmitted=child and child['status']=='queued' and not any(child.get(key) for key in (
            'prompt_id','viggle_submission_attempted','pika_submission_attempted','engine_instance_id'))
        if child and child['status'] not in ('completed','failed','cancelled') and not unsubmitted:
            raise HTTPException(409,'Hay un tramo con ejecución o envío pendiente. Revisa su estado primero.')
        if number>=index or seg['shot'] in passthrough:
            continue
        if not child or child['status']!='completed' or not child.get('result_available'):
            raise HTTPException(409,f'Continúa desde el tramo {number+1}; aún falta su resultado.')
        if (child.get('direct_review') or {}).get('human_review')=='rejected':
            raise HTTPException(409,f'Continúa desde el tramo {number+1}; rechazaste ese resultado.')
        path=service.root/'results'/(child['id']+'.mp4')
        checksum=next((child[k] for k in ('direct_result_sha256','alisson_result_sha256','secourses_result_sha256',
            'motion_result_sha256','layers_result_sha256','pika_result_sha256','viggle_result_sha256') if child.get(k)),None)
        if not path.is_file() or (checksum and digest(path)!=checksum):
            raise HTTPException(409,f'El archivo del tramo {number+1} falta o cambió. Continúa desde ese tramo.')
    revision=uuid4().hex
    revisions={**job.get('sequence_revisions',{}),**{str(s['index']):revision for s in segments[index:]}}
    # Preserve assembled artifacts if cancellation happened during finalization.
    folder=service.root/'jobs'/job['id']
    archive=folder/('before-resume-'+revision)
    artifacts=[folder/name for name in ('sequence-result.json','layers-character.mp4','layers-character-model.mp4','layers-character-receipt.json')]
    artifacts.append(service.root/'results'/(job['id']+'.mp4'))
    moved=[]
    try:
        for item in artifacts:
            if item.exists():
                archive.mkdir(exist_ok=True)
                destination=archive/item.name
                item.rename(destination)
                moved.append((item,destination))
        start_segment=segments[-1] if assemble_only else segments[index]
        return service.store.update(job['id'],sequence_revisions=revisions,sequence_index=start_segment['index'],sequence_done=index,
            sequence_approved_index=None,sequence_active_child=None,sequence_current_shot=start_segment['shot'],cancel_requested=False,
            cancel_requested_at=None,cancelled_at=None,cancel_submission_unknown=False,
            qwen_waiting=False,qwen_question_job_id=None,sequence_waiting=False,status='queued',
            stage='En cola para montar el vídeo completo' if assemble_only else f'En cola desde el tramo {index+1}',error=None,progress=None,prompt_id=None,client_id=None,
            result_available=False,layers_character_ready=False)
    except Exception:
        for item,destination in reversed(moved):destination.rename(item)
        raise


def record_rejected_preview(service,job,clip):
    """Keep the reviewed render separate from approved sequence results."""
    if not job['parameters'].get('sequence_parent_id'):
        return
    folder=local_path(service.root,service.root/'jobs'/job['id'])
    path=local_path(folder,clip['path'])
    if path.suffix.lower()!='.mp4' or not path.is_file():
        raise ValueError('Falta el vídeo revisado del tramo.')
    save(folder/'rejected-preview.json',{'file':path.relative_to(folder).as_posix(),'sha256':clip['sha256']})


def rejected_preview_url(service,job):
    if job and job['status'] in ('failed','cancelled') and job['parameters'].get('sequence_parent_id'):
        if (service.root/'jobs'/job['id']/'rejected-preview.json').is_file():
            return f'/api/jobs/{job["id"]}/rejected-preview'
    return None


def verified_rejected_preview(service,job,expected_sha256=None):
    if not rejected_preview_url(service,job):
        raise HTTPException(404,'Este intento no tiene un vídeo rechazado disponible.')
    folder=local_path(service.root,service.root/'jobs'/job['id'])
    try:
        receipt=json.loads(local_path(folder,folder/'rejected-preview.json').read_text('utf8'))
        path=local_path(folder,folder/receipt['file'])
        if expected_sha256 is not None and expected_sha256!=receipt['sha256']:
            raise HTTPException(409,'El vídeo revisado cambió. Vuelve a abrir el tramo.')
        if path.suffix.lower()=='.mp4' and path.is_file() and digest(path)==receipt['sha256']:
            return path
    except (OSError,ValueError,KeyError,TypeError):
        pass
    raise HTTPException(409,'El archivo del tramo rechazado falta o cambió.')


def register(app):
    from .segment_background_review import register as register_background_review
    register_background_review(app)
    @app.get('/api/jobs/{job_id}/rejected-preview')
    async def rejected_preview(job_id:UUID,sha256:str|None=None):
        service=app.state.jobs.current
        path=await asyncio.to_thread(verified_rejected_preview,service,service.store.job(str(job_id)),sha256)
        return FileResponse(path,media_type='video/mp4',headers={'Cache-Control':'private, no-store'})

    @app.post('/api/jobs/{job_id}/sequence/{index}/approve')
    async def approve(job_id:UUID,index:int,attempt_id:UUID,sha256:str):
        from .sequence_approval import approve_rejected_segment
        service=app.state.jobs.current
        async with service.lock:
            updated=await asyncio.to_thread(approve_rejected_segment,service,str(job_id),index,str(attempt_id),sha256)
            return {**summary(updated),'sequence_done':updated['sequence_done'],
                    'sequence_approved_index':updated.get('sequence_approved_index')}

    @app.post('/api/jobs/{job_id}/sequence/{index}/reject-preview')
    async def reject_preview(job_id:UUID,index:int,attempt_id:UUID,sha256:str):
        service=app.state.jobs.current
        async with service.lock:
            child=service.store.job(str(attempt_id))
            if not child or child['parameters'].get('sequence_parent_id')!=str(job_id) or child['parameters'].get('sequence_index')!=index:
                raise HTTPException(404,'Intento no encontrado en este tramo.')
            if child['status'] not in ('failed','cancelled'):
                raise HTTPException(409,'Este intento ya no está pendiente de revisión.')
            await asyncio.to_thread(verified_rejected_preview,service,child,sha256)
            service.store.update(str(attempt_id),sequence_rejected_sha256=sha256,stage='Tramo rechazado por ti')
            return {'rejected':True}

    @app.get('/api/jobs/{job_id}/sequence')
    async def get(job_id:UUID):
        service=app.state.jobs.current;job=service.store.job(str(job_id))
        if not job or not job.get('video_sequence_version'):raise HTTPException(404,'Secuencia no encontrada.')
        record=await asyncio.to_thread(load,service,job,False)
        return {'job_id':job['id'],'width':(record or {}).get('width'),'height':(record or {}).get('height'),'cuts':(record or {}).get('cuts',[]),'segments':[
            {**{k:s[k] for k in ('index','shot','start','keep','context_frames','starts_at_cut')},
             'attempt_id':child_id(job,s['index']),
             'job':summary(service.store.job(child_id(job,s['index'])),service),
             'thumbnail_url':f'/api/jobs/{job_id}/sequence/{s["index"]}/thumbnail'} for s in (record or {}).get('segments',[])]}

    @app.post('/api/jobs/{job_id}/repeat-recorded',status_code=202)
    async def repeat_recorded(job_id:UUID,request_id:UUID):
        from .h3_recorded_render import OPERATION, recipe
        service=app.state.jobs.current
        async with service.lock:
            if not service.settings['generation_enabled']:
                raise HTTPException(409,'Generación desactivada.')
            ident=str(uuid5(NAMESPACE_URL,f'{job_id}/repeat-recorded/{request_id}'))
            existing=service.store.job(ident)
            if existing:return summary(existing)
            try:
                _,_,_,source=await asyncio.to_thread(recipe,service,str(job_id))
            except (OSError,ValueError,KeyError,IndexError) as error:
                raise HTTPException(409,'La receta archivada no está disponible: '+str(error)) from error
            params={k:v for k,v in source['parameters'].items()
                    if not k.startswith('sequence_') and k!='standalone_segment_attempt'}
            params.update(operation=OPERATION,recorded_source_id=str(job_id),
                          recorded_result_sha256=source['direct_result_sha256'])
            variant=service.store.create_job(ident,ident,params)
            service.wake.set()
            return summary(variant)

    @app.post('/api/jobs/{job_id}/sequence/{index}/regenerate',status_code=202)
    async def regenerate(job_id:UUID,index:int,attempt_id:UUID,request_id:UUID,whole_shot_identity:bool=False):
        service=app.state.jobs.current
        async with service.lock:
            parent=service.store.job(str(job_id))
            if not parent or not parent.get('video_sequence_version') or not service.settings['generation_enabled']:
                raise HTTPException(409,'La secuencia no está disponible para generación.')
            source=service.store.job(str(attempt_id))
            if not source or str(attempt_id)!=child_id(parent,index) or source['parameters'].get('sequence_parent_id')!=str(job_id):
                raise HTTPException(409,'Actualiza la lista de tramos.')
            if source['status'] not in ('completed','failed','cancelled'):
                raise HTTPException(409,'Espera a que termine el tramo seleccionado.')
            ident=uuid5(NAMESPACE_URL,f'{attempt_id}/manual/{request_id}')
            params={**source['parameters'],'standalone_segment_attempt':str(attempt_id),'seed':ident.int%(2**53-1)}
            existing=service.store.job(str(ident))
            if existing:
                return summary(existing)
            if params.get('engine')=='h3_direct' and params.get('direct_preset')=='hands':
                params['manual_closeup_reference']=1
            if whole_shot_identity:
                if params.get('engine')!='h3_direct' or params.get('direct_preset')!='hands' or len(params.get('direct_characters',[]))!=1:
                    raise HTTPException(409,'Esta variante requiere un personaje asignado en H3 Directo.')
                params['whole_shot_identity']=1
            variant=service.store.create_job(str(ident),str(ident),params)
            service.wake.set()
            return summary(variant)

    @app.post('/api/jobs/{job_id}/sequence/{index}/resume',status_code=202)
    async def resume(job_id:UUID,index:int,attempt_id:UUID):
        service=app.state.jobs.current
        async with service.lock:
            job=service.store.job(str(job_id))
            if not job or not job.get('video_sequence_version'):raise HTTPException(404,'Secuencia no encontrada.')
            record=await asyncio.to_thread(load,service,job,False)
            updated=await asyncio.to_thread(resume_sequence,service,job,record,index,attempt_id)
            service.wake.set()
            return {**summary(updated),'sequence_done':index,'sequence_approved_index':None}

    @app.get('/api/jobs/{job_id}/sequence/{index}/thumbnail')
    async def thumbnail(job_id:UUID,index:int):
        service=app.state.jobs.current;job=service.store.job(str(job_id))
        if not job or not job.get('video_sequence_version'):raise HTTPException(404,'Secuencia no encontrada.')
        record=await asyncio.to_thread(load,service,job,False)
        if not record or not 0<=index<len(record['segments']):raise HTTPException(404,'Tramo no encontrado.')
        s=record['segments'][index];path=await asyncio.to_thread(file_checked,service,s['thumbnail'],s['thumbnail_sha256'])
        return FileResponse(path,media_type='image/jpeg')

    @app.post('/api/jobs/{job_id}/sequence/{index}/selection')
    async def selection(job_id:UUID,index:int,body:dict):
        service=app.state.jobs.current
        async with service.lock:
            job=service.store.job(str(job_id))
            if not job or not job.get('video_sequence_version'):raise HTTPException(404,'Secuencia no encontrada.')
            if job['parameters'].get('operation')!='motion_layers' or job.get('cancel_requested'):raise HTTPException(409,'Esta toma no admite selección.')
            record=load(service,job,False)
            if not record or not 0<=index<len(record['segments']) or not record['segments'][index]['starts_at_cut']:raise HTTPException(409,'Este tramo no comienza en un corte.')
            import math
            if set(body)!={'x','y'} or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1 for v in body.values()):raise HTTPException(422,'Selecciona un punto dentro de la imagen.')
            path=service.root/'jobs'/job['id']/f'selection-{index}.json'
            if path.exists():
                if json.loads(path.read_text())!=body:raise HTTPException(409,'La selección ya fue aceptada.')
                return {'ok':True}
            if not job.get('sequence_waiting') or job.get('sequence_index')!=index:raise HTTPException(409,'Este tramo ya no espera selección.')
            save(path,body);service.store.update(job['id'],sequence_waiting=False,status='queued',stage='Selección guardada · En cola')
            service.wake.set();return {'ok':True}

def summary(job,service=None):
    if not job:return None
    item={k:job.get(k) for k in ('id','status','stage','result_available')}
    if (job.get('direct_review') or {}).get('human_review')=='rejected':
        item['stage']='Rechazado por el usuario · '+(job['direct_review'].get('human_feedback') or 'Requiere otra toma.')
    if service and job['status']=='completed' and job.get('parameters',{}).get('engine')=='h3_direct':
        from .h3_recorded_render import available_source
        recorded=available_source(service.root,job)
        if recorded:item['recorded_source_id']=recorded
    if job.get('sequence_rejected_sha256'):item['sequence_rejected_sha256']=job['sequence_rejected_sha256']
    if job.get('human_segment_review'):item['human_segment_review']=True
    if service and (url:=rejected_preview_url(service,job)):
        item['rejected_preview_url']=url
        try:
            receipt=json.loads((service.root/'jobs'/job['id']/'rejected-preview.json').read_text('utf8'))
            item['rejected_preview_sha256']=receipt['sha256']
        except (OSError,ValueError,KeyError,TypeError):
            pass
    from .segment_background_review import eligible
    sha=item.get('rejected_preview_sha256') or (job.get('direct_result_sha256') if job.get('status')=='completed' and job.get('result_available') else None)
    if sha and eligible(job):item['background_review_sha256']=sha
    return item
