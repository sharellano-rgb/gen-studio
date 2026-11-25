import {sourceResolution} from './videoOrientation.js';
export const HYBRID_MAX_FRAMES=360;
export const HYBRID_METHOD='viggle_h3_identity';
export const HYBRID_DIRECTION='Recrea el fotograma con el personaje y el estilo de la imagen de referencia. Conserva la pose exacta de la fuente, el tamaño y la posición del personaje, el encuadre, los apoyos y los objetos con los que interactúa. El vídeo determinará el movimiento.';
export const hybridJob=job=>job?.parameters?.operation==='motion_hybrid';
export const activeHybrid=job=>Boolean(job&&!['completed','failed','cancelled','submission_unknown'].includes(job.status));
export function hybridForm(saved={},job=null,selection={}){
 const p=job?.parameters||saved;
 return {video_id:p.video_id||selection.video||'',reference_id:p.reference_id||selection.image||'',
  frames:p.frames??120,start_seconds:String(p.start_seconds??0),resolution:p.resolution||'640x1152',
  identity_resolution:p.hybrid_identity_resolution||p.identity_resolution||saved.identity_resolution||'768x1344',
  identity_reference_id:p.hybrid_identity_reference_id||p.identity_reference_id||'',
  seed:p.seed??42,anchor_index:p.anchor_index??0,direction:p.direction||HYBRID_DIRECTION,
  anchor_editor:p.anchor_editor||'klein'};
}
export function hybridChooseVideo(form,asset){
 return {...form,video_id:asset?.id||'',frames:Math.max(5,Math.min(120,Math.round((asset?.duration||5)*24))),
  start_seconds:'0',anchor_index:0,resolution:sourceResolution(asset,form.resolution),
  identity_resolution:sourceResolution(asset,form.identity_resolution)};
}
export function hybridStart(value){
 if(typeof value!=='string'||!/^\d+(?:[.,]\d*)?$/.test(value.trim()))return null;
 const result=Number(value.trim().replace(',','.'));
 return Number.isFinite(result)&&result>=0&&result<=30?result:null;
}
export function hybridPrepareBody(form,key){
 return {request_key:key,video_id:form.video_id,reference_id:form.reference_id,frames:form.frames,
  start_seconds:hybridStart(form.start_seconds),anchor_index:form.anchor_index,resolution:form.resolution,
  seed:form.seed,direction:form.direction,scene_mode:'restyle',anchor_editor:form.anchor_editor,qwen_recipe:'pose_v2'};
}
export function hybridMatches(anchor,form){
 if(!anchor?.motion_anchor_available||anchor.status!=='completed'||anchor.parameters.original_reference)return false;
 const p=anchor.parameters,b=hybridPrepareBody(form,'');
 return ['video_id','reference_id','frames','start_seconds','anchor_index','resolution','seed','direction'].every(k=>p[k]===b[k])
  &&(p.anchor_editor||'klein')===form.anchor_editor;
}
export function validHybridForm(form,video,image){
 const start=hybridStart(form.start_seconds);
 return Boolean(video&&image&&start!==null&&Number.isInteger(form.frames)&&form.frames>=5&&form.frames<=360
  &&Math.round(start*24)+form.frames<=Math.round(video.duration*24)+1
  &&Number.isInteger(form.anchor_index)&&form.anchor_index>=0&&form.anchor_index<form.frames
  &&Number.isInteger(form.seed)&&form.seed>=0&&form.seed<=9007199254740991
  &&form.direction.trim().length>=10&&form.direction.length<=2000);
}
export function hybridRenderBody(form,anchor,key){
 return {request_key:key,approved_anchor_sha256:anchor.motion_anchor_sha256,
  identity_reference_id:form.identity_reference_id||form.reference_id,identity_resolution:form.identity_resolution};
}
export function hybridAcknowledged(pending,job){
 if(!pending||!job?.id||!job.parameters)return false;
 const p=job.parameters,b=pending.body;
 if(pending.kind==='prepare')return p.operation==='motion_anchor'
  &&['video_id','reference_id','frames','start_seconds','anchor_index','resolution','seed','direction'].every(k=>p[k]===b[k])
  &&(p.anchor_editor||'klein')===(b.anchor_editor||'klein');
 return p.operation==='motion_hybrid'&&p.transfer_method===HYBRID_METHOD
  &&p.motion_anchor_id===pending.anchor_id&&p.anchor_sha256===b.approved_anchor_sha256
  &&p.hybrid_identity_reference_id===b.identity_reference_id&&p.hybrid_identity_resolution===b.identity_resolution
  &&(p.hybrid_reuse_motion_job_id||null)===(b.reuse_motion_job_id||null);
}

export function validHybridPending(value){
 const uuid=value=>typeof value==='string'&&/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value);
 if(!value||!['prepare','render'].includes(value.kind)||!uuid(value.body?.request_key))return false;
 const b=value.body;
 if(value.kind==='prepare')return value.path==='/motion-transfer/anchors'&&uuid(b.video_id)&&uuid(b.reference_id)
  &&Number.isInteger(b.frames)&&b.frames>=5&&b.frames<=360&&Number.isFinite(b.start_seconds);
 return uuid(value.anchor_id)&&value.path==='/jobs/'+value.anchor_id+'/motion-transfer-hybrid'
  &&typeof b.approved_anchor_sha256==='string'&&/^[a-f0-9]{64}$/.test(b.approved_anchor_sha256)
  &&(!b.reuse_motion_job_id||uuid(b.reuse_motion_job_id))&&uuid(b.identity_reference_id)&&['480x864','864x480','640x1152','1152x640','768x1344','1344x768','512x512'].includes(b.identity_resolution);
}
