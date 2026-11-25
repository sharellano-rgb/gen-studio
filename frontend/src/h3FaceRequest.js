const key=parent=>'gen-h3-face-pending:'+parent;
export function readPendingFace(storage,parent){try{const d=JSON.parse(storage.getItem(key(parent)));return d?.request_key&&d.reference_id&&Number.isInteger(d.start_frame)&&Number.isInteger(d.frames)&&Number.isInteger(d.x)&&Number.isInteger(d.y)?d:null;}catch{return null;}}
export function savePendingFace(storage,parent,body){storage.setItem(key(parent),JSON.stringify(body));}
export function clearPendingFace(storage,parent){storage.removeItem(key(parent));}
export function showsDirectComposer(job){return !['h3_face_refine','upscale_1080'].includes(job?.parameters?.operation);}
export function canSendFace({busy,sending,pending,valid}){return !sending&&(Boolean(pending)||(!busy&&valid));}
export function h3FacePreview(job){
  const p=job?.parameters;
  if(p?.operation!=='h3_face_refine')return null;
  const id=job.result_available?job.id:p.h3_face_parent_id;
  return {id,src:id?`/api/jobs/${id}/result?variant=original`:undefined,
    referenceId:p.h3_face_reference_id,
    title:job.result_available?'Tu copia con refinamiento facial':'Toma de origen · Refinamiento facial'};
}
