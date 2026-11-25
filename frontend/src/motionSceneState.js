import {sourceResolution} from './videoOrientation.js';
import {hybridStart,activeHybrid} from './motionHybridState.js';
export const SCENE_DRAFT='gen-motion-scene-draft-v1';
export const SCENE_PENDING='gen-motion-scene-pending-v1';
export const SCENE_MAX_FRAMES=107;
export const SCENE_METHOD='h3_scene_direct';
export const SCENE_DIRECTION_PREFIX='Create the FIRST FRAME of a complete destination scene for motion transfer. ';
const GUIDE_RULES='Image 1 defines the exact initial pose, camera framing, perspective, character size, placement and contact points. Image 2 defines only the character identity, anatomy, hair, clothing and colors. Rebuild the whole environment and interacting props according to the requested scene. Preserve spatial relationships and supports while replacing their appearance. Do not copy the reference portrait pose or its background. Requested destination scene: ';
const resolutions=['480x864','864x480','640x1152','1152x640','768x1344','1344x768','512x512'];
const uuid=v=>typeof v==='string'&&/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(v);
export const activeScene=activeHybrid;
export function sceneDirection(goal){return SCENE_DIRECTION_PREFIX+GUIDE_RULES+goal;}
export function sceneMethodForJob(job,fallback='scene'){
 const p=job?.parameters||{};
 if(p.transfer_method===SCENE_METHOD)return 'scene';
 if(p.operation==='motion_anchor')return p.direction?.startsWith(SCENE_DIRECTION_PREFIX)?'scene':'hybrid';
 if(p.engine==='viggle_motion')return 'hybrid';
 return fallback==='hybrid'?'hybrid':'scene';
}
export function sceneForm(saved={},job=null,selection={}){
 const p=job?.parameters||saved;
 const prefix=SCENE_DIRECTION_PREFIX+GUIDE_RULES;
 const goal=p.motion_scene_goal??(p.direction?.startsWith(prefix)?p.direction.slice(prefix.length):p.scene_goal??saved.scene_goal??'');
 return {video_id:p.video_id||selection.video||'',reference_id:p.hybrid_identity_reference_id||p.reference_id||selection.image||'',
  frames:Math.min(SCENE_MAX_FRAMES,p.frames??73),start_seconds:String(p.start_seconds??0),
  resolution:p.resolution||'640x1152',identity_resolution:p.hybrid_identity_resolution||p.identity_resolution||saved.identity_resolution||'768x1344',
  seed:p.seed??42,anchor_editor:p.anchor_editor||'klein',scene_goal:goal,environment_reference_id:p.environment_reference_id||''};
}
export function sceneChooseVideo(form,asset){
 return {...form,video_id:asset?.id||'',frames:Math.max(5,Math.min(73,Math.round((asset?.duration||73/24)*24))),start_seconds:'0',
  resolution:sourceResolution(asset,form.resolution),identity_resolution:sourceResolution(asset,form.identity_resolution)};
}
export function sceneChooseEnvironment(form,asset){
 return {...form,environment_reference_id:asset?.id||'',scene_goal:asset&&!form.scene_goal.trim()?'Recrea el entorno de la escena preparada durante todo el vídeo y conserva sus objetos, iluminación y estilo.':form.scene_goal};
}
export function sceneDurations(form,video){
 const start=hybridStart(form.start_seconds);
 const total=video?Math.round(video.duration*24):null;
 const left=total!==null&&start!==null?total-Math.round(start*24):SCENE_MAX_FRAMES;
 return [...new Set([48,73,107,form.frames,...(total!==null&&total<=107&&start===0?[total]:[])])]
  .filter(n=>Number.isInteger(n)&&n>=5&&n<=107&&(n<=left||n===form.frames)).sort((a,b)=>a-b);
}
export function validSceneForm(form,video,image,environment=null){
 const start=hybridStart(form.start_seconds);
 return Boolean(video?.kind==='video'&&image?.kind==='image'&&video.id===form.video_id&&image.id===form.reference_id
  &&(!form.environment_reference_id||(uuid(form.environment_reference_id)&&environment?.kind==='image'&&environment.id===form.environment_reference_id))
  &&start!==null&&Number.isInteger(form.frames)&&form.frames>=5&&form.frames<=107
  &&Math.round(start*24)+form.frames<=Math.round(video.duration*24)+1
  &&Number.isSafeInteger(form.seed)&&form.seed>=0&&resolutions.includes(form.resolution)&&resolutions.includes(form.identity_resolution)
  &&['klein','qwen_image_21'].includes(form.anchor_editor)&&typeof form.scene_goal==='string'&&form.scene_goal.trim().length>=10&&form.scene_goal.length<=800);
}
export function scenePrepareBody(form,key){return {request_key:key,video_id:form.video_id,reference_id:form.reference_id,frames:form.frames,
 start_seconds:hybridStart(form.start_seconds),anchor_index:0,resolution:form.resolution,seed:form.seed,
 direction:sceneDirection(form.scene_goal),scene_mode:'restyle',anchor_editor:form.anchor_editor,qwen_recipe:'pose_v2',...(form.environment_reference_id?{environment_reference_id:form.environment_reference_id}:{})};}
export function sceneMatches(anchor,form){
 if(!anchor?.motion_anchor_available||anchor.status!=='completed')return false;
 const p=anchor.parameters||{},b=scenePrepareBody(form,'');
 return (p.environment_reference_id||'')===(form.environment_reference_id||'')&&!p.original_reference&&p.scene_mode==='restyle'&&p.anchor_index===0&&p.frames>=form.frames
  &&['video_id','reference_id','start_seconds','resolution','seed','direction'].every(k=>p[k]===b[k])
  &&(p.anchor_editor||'klein')===form.anchor_editor&&(form.anchor_editor!=='qwen_image_21'||p.qwen_recipe==='pose_v2');
}
export function sceneRenderBody(form,anchor,key){return {request_key:key,approved_anchor_sha256:anchor.motion_anchor_sha256,
 identity_reference_id:form.reference_id,identity_resolution:form.identity_resolution,frames:form.frames,scene_goal:form.scene_goal};}
export function sceneRequest(kind,form,anchor,key){
 if(!['prepare','render'].includes(kind))throw Error('Solicitud de escena desconocida.');
 return {kind,form:{...form},anchor_id:kind==='render'?anchor.id:null,
  path:kind==='prepare'?'/motion-transfer/anchors':'/jobs/'+anchor.id+'/motion-transfer-scene-trial',
  body:kind==='prepare'?scenePrepareBody(form,key):sceneRenderBody(form,anchor,key)};
}
export function sceneAcknowledged(pending,job){
 if(!pending||!job?.id||!job.parameters)return false;
 const p=job.parameters,b=pending.body;
 if(pending.kind==='prepare')return (p.environment_reference_id||'')===(b.environment_reference_id||'')
  &&(!b.environment_reference_id||/^[a-f0-9]{64}$/.test(p.environment_reference_sha256||''))&&p.operation==='motion_anchor'&&p.scene_mode==='restyle'&&!p.original_reference
  &&['video_id','reference_id','frames','start_seconds','anchor_index','resolution','seed','direction'].every(k=>p[k]===b[k])
  &&(p.anchor_editor||'klein')===b.anchor_editor&&(b.anchor_editor!=='qwen_image_21'||p.qwen_recipe===b.qwen_recipe);
 return pending.kind==='render'&&(p.environment_reference_id||'')===(pending.form?.environment_reference_id||'')&&p.operation==='motion_hybrid'&&p.transfer_method===SCENE_METHOD
  &&p.motion_anchor_id===pending.anchor_id&&p.anchor_sha256===b.approved_anchor_sha256
  &&p.hybrid_identity_reference_id===b.identity_reference_id&&p.hybrid_identity_resolution===b.identity_resolution
  &&p.frames===b.frames&&p.motion_scene_goal===b.scene_goal&&!p.hybrid_reuse_motion_job_id
  &&!p.motion_head_guide&&!p.motion_body_source_job_id&&!p.motion_scene_reference_id;
}
export function validScenePending(value){
 if(!value||!['prepare','render'].includes(value.kind)||!uuid(value.body?.request_key)||!value.form)return false;
 const f=value.form,b=value.body;
 if(!uuid(f.video_id)||!uuid(f.reference_id)||!validSceneForm(f,{kind:'video',id:f.video_id,duration:35},{kind:'image',id:f.reference_id},f.environment_reference_id?{kind:'image',id:f.environment_reference_id}:null))return false;
 if(value.kind==='render'&&(!uuid(value.anchor_id)||!/^([a-f0-9]{64})$/.test(b.approved_anchor_sha256||'')))return false;
 const expected=sceneRequest(value.kind,f,{id:value.anchor_id,motion_anchor_sha256:b.approved_anchor_sha256},b.request_key);
 return value.path===expected.path&&(value.anchor_id??null)===expected.anchor_id
  &&Object.keys(b).length===Object.keys(expected.body).length&&Object.keys(expected.body).every(k=>b[k]===expected.body[k]);
}
