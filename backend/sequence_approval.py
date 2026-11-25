"""Publish the exact sequence render accepted during manual review."""
import re
import shutil
import subprocess
from uuid import uuid4

from fastapi import HTTPException

from .guided_composition import local_path
from .h3_face_refine import video_info
from .head import digest
from .store import now
from .video_sequences import child_id, load, verified_rejected_preview


def approve_rejected_segment(service,parent_id,index,attempt_id,sha256):
    parent=service.store.job(parent_id)
    if not parent or not parent.get('video_sequence_version'):
        raise HTTPException(404,'Secuencia no encontrada.')
    plan=load(service,parent,verify=False)
    if not plan or not 0<=index<len(plan['segments']):
        raise HTTPException(404,'Tramo no encontrado.')
    current=child_id(parent,index)
    child=service.store.job(attempt_id)
    variant=bool(child and child['parameters'].get('standalone_segment_attempt')==current)
    if current!=attempt_id and not variant:
        raise HTTPException(409,'Hay otro intento para este tramo. Actualiza la toma.')
    if not child or child['parameters'].get('sequence_parent_id')!=parent_id or child['parameters'].get('sequence_index')!=index:
        raise HTTPException(404,'Intento no encontrado en este tramo.')
    if not re.fullmatch('[0-9a-f]{64}',sha256):
        raise HTTPException(422,'Falta la firma del vídeo revisado.')
    if child.get('sequence_rejected_sha256')==sha256:
        raise HTTPException(409,'Este intento ya fue rechazado.')
    target=local_path(service.root,service.root/'results'/(attempt_id+'.mp4'))
    approval=child.get('sequence_manual_approval') or {}
    if child['status']=='completed' and approval.get('sha256')==sha256:
        if not target.is_file() or digest(target)!=sha256:
            raise HTTPException(409,'Falta o cambió la copia aprobada.')
        return parent
    if (parent['status'] not in ('failed','cancelled') or service.active_job_id==parent_id
            or parent.get('sequence_done',0)!=index or parent.get('sequence_active_child') not in (None,current)
            or child['status'] not in ('failed','cancelled')):
        raise HTTPException(409,'Sólo puedes aprobar el intento rechazado donde se detuvo esta secuencia.')
    source=verified_rejected_preview(service,child,sha256)
    segment=plan['segments'][index]
    try:
        info=video_info(source)
        width,height=map(int,child['parameters']['resolution'].split('x'))
        if (info['width'],info['height'])!=(width,height) or info['frames']<segment['context_frames']+segment['keep']:
            raise ValueError('Geometría o duración incompatible.')
    except (ValueError,KeyError,IndexError,ZeroDivisionError,OSError,subprocess.SubprocessError) as error:
        raise HTTPException(409,'El vídeo revisado no tiene el formato o los fotogramas necesarios para el montaje.') from error

    # A stopped process may have published the same bytes before SQLite committed.
    # Reuse that file on retry. Never replace an existing different result.
    target.parent.mkdir(exist_ok=True)
    temp=target.with_name(f'.{uuid4().hex}.tmp')
    try:
        if not target.exists():
            shutil.copyfile(source,temp)
            if digest(temp)!=sha256:
                raise HTTPException(409,'El vídeo cambió al preparar la copia aprobada.')
        with service.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for job in (parent,child):
                row=db.execute("SELECT json_extract(state,'$.updated_at') FROM jobs WHERE id=?",(job['id'],)).fetchone()
                if not row or row[0]!=job['updated_at']:
                    raise HTTPException(409,'La toma cambió durante la aprobación. Actualízala.')
            if target.exists():
                if digest(target)!=sha256:
                    raise HTTPException(409,'Ya existe un resultado diferente para este intento.')
            else:
                temp.replace(target)
            decision={'sha256':sha256,'approved_at':now(),'workspace':service.settings.get('workspace_id','owner'),
                      'rejection_reason':child.get('error') or child['stage']}
            service.store.update_in_transaction(db,attempt_id,status='completed',stage='Tramo aprobado por ti',
                result_available=True,progress=None,error=None,direct_result_sha256=sha256,
                direct_review={**(child.get('direct_review') or {}),'human_review':'approved','review_required':False,
                               'identity_approved':True,'approved_result_sha256':sha256},sequence_manual_approval=decision)
            stage=(f'Tramo {index+1} aprobado · Listo para continuar con el tramo {index+2}'
                   if index+1<len(plan['segments']) else 'Último tramo aprobado · Listo para montar el vídeo')
            return service.store.update_in_transaction(db,parent_id,status='cancelled',stage=stage,progress=None,error=None,
                sequence_done=index+1,sequence_approved_index=index,sequence_active_child=None,
                **({'sequence_approved_variants':{**parent.get('sequence_approved_variants',{}),
                    child_id({**parent,'sequence_approved_variants':{}},index):attempt_id}} if variant else {}))
    finally:
        temp.unlink(missing_ok=True)
