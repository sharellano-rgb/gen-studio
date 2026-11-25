import {PIKA_DETAILED,pikaRefMethod} from './pikaRefMethods.js';
import {visualEngineFor,isSecoursesEngine} from './secoursesVisualMethods.js';
import {isNinjaShots,ninjaShotsLabel} from './ninjaShotsState.js';
const pendingKey=(kind,parent='')=>'gen-ninja-pending:'+kind+':'+parent;
export const isNinja=job=>job?.parameters?.engine==='h3_ninja';
export const objectSwapEngine=p=>p?.engine==='cpu_composite'&&p?.operation==='localized_head_repair'?'h3_direct':pikaRefMethod(p)||visualEngineFor(p)||(['h3_direct','h3_ninja','h3_secourses','h3_alisson','h3_mdmz','pika_ref2va','viggle_remake'].includes(p?.engine)?p.engine:p?'h3_sam3':'h3_direct');
const activeEngine=value=>['h3_direct','h3_ninja','h3_alisson','pika_ref2va',PIKA_DETAILED,'viggle_remake'].includes(value)||isSecoursesEngine(value);
export const restoreObjectSwapEngine=(saved,parameters)=>activeEngine(saved)?saved:activeEngine(objectSwapEngine(parameters))?objectSwapEngine(parameters):'h3_direct';
export function keepSelectedJob(incoming,current,id){
  const selected=current.find(job=>job.id===id);
  return selected&&!incoming.some(job=>job.id===id)?[...incoming,selected]:incoming;
}
export const ninjaModeLabel=mode=>mode==='body'?'Cuerpo completo':'Rostro y cabello';
export const ninjaLabel=job=>isNinjaShots(job)?ninjaShotsLabel(job):'Refinamiento Facial · '+ninjaModeLabel(job?.parameters?.mode)+' · '+(job?.parameters?.recipe==='w2'?'Receta 2':'Receta 1')+(job?.parameters?.operation==='ninja_render'&&ninjaHasHeadMotion(job.parameters)?' · '+ninjaMotionLabel(job.parameters.ninja_head_motion):'');
export const ninjaHasHeadMotion=p=>p?.recipe==='w2'&&p?.mode==='head';
export const ninjaMotionLabel=value=>value==='preserve'?'Priorizar movimiento':'Ajuste original';
export function saveNinjaMotion(storage,parent,value){
  if(parent&&['original','preserve'].includes(value))storage.setItem('gen-ninja-motion:'+parent,value);
}
export function clearNinjaMotion(storage,parent){storage.removeItem('gen-ninja-motion:'+parent);}
export function restoreNinjaMotion(storage,{pending,pendingPrepare,context,parentId,draft}={}){
  if(pending)return pending.head_motion==='preserve'?'preserve':'original';
  if(pendingPrepare)return storage.getItem('gen-ninja-motion:request:'+pendingPrepare.request_key)==='preserve'?'preserve':'original';
  if(context?.parameters?.operation==='ninja_render')return context.parameters.ninja_head_motion==='preserve'?'preserve':'original';
  const stored=parentId?storage.getItem('gen-ninja-motion:'+parentId):null;
  if(['original','preserve'].includes(stored))return stored;
  if(!context&&['original','preserve'].includes(draft?.head_motion))return draft.head_motion;
  return 'preserve';
}
export function ninjaRenderBody(preparation,motion,key,pending){
  if(pending)return pending; // Never enrich or change an uncertain legacy request.
  return {request_key:key,mask_sha256:preparation.ninja_mask_sha256,...(ninjaHasHeadMotion(preparation.parameters)?{head_motion:motion}: {})};
}
// Match the API's 24 fps interval bound, including a partial terminal frame.
export function ninjaSourceFrames(video){
  const duration=Number(video?.duration);
  return Number.isFinite(duration)&&duration>0?Math.ceil(duration*24):0;
}
export function ninjaFullTake(form,video,maxFrames=720){
  return {...form,start_seconds:0,frames:Math.min(720,maxFrames,ninjaSourceFrames(video)),...(Math.round(form.start_seconds*24)!==0?{x:null,y:null}:{})};
}
export function ninjaDefaults(video,maxFrames=720){
  const total=ninjaSourceFrames(video);
  const frames=Math.min(120,maxFrames,total||120);
  return {video_id:video?.id||'',reference_id:'',recipe:'w2',mode:'head',start_seconds:Math.max(0,total-frames)/24,frames,x:null,y:null,seed:123456};
}
const fields=['video_id','reference_id','recipe','mode','start_seconds','frames','x','y','seed'];
export function ninjaForm(parameters,video,maxFrames=720){
  const form=ninjaDefaults(video,maxFrames);
  for(const field of fields)if(Object.hasOwn(parameters||{},field)){
    const value=parameters[field];
    if(['start_seconds','frames','seed'].includes(field)&&!Number.isFinite(value))continue;
    form[field]=value;
  }
  return form;
}
export function newNinjaForm(parameters,video,maxFrames=720){
  const form=ninjaForm(parameters,video,maxFrames);
  return {...form,recipe:'w2',...(form.recipe==='w1'?{x:null,y:null}:{})};
}
export function ninjaPrepareBody(form,key){
  return {request_key:key,...Object.fromEntries(fields.map(field=>[field,form[field]]))};
}
export function validNinjaForm(form,assets,maxFrames=720){
  const video=assets.find(a=>a.id===form.video_id&&a.kind==='video');
  const reference=assets.find(a=>a.id===form.reference_id&&a.kind==='image');
  const total=ninjaSourceFrames(video);
  return Boolean(video&&reference&&form.recipe==='w2'&&['head','body'].includes(form.mode)&&
    Number.isFinite(form.start_seconds)&&form.start_seconds>=0&&Number.isInteger(form.frames)&&form.frames>=5&&form.frames<=Math.min(720,maxFrames)&&
    Math.round(form.start_seconds*24)+form.frames<=total&&
    [form.x,form.y].every(v=>typeof v==='number'&&Number.isFinite(v)&&v>=0&&v<=1)&&
    Number.isSafeInteger(form.seed)&&form.seed>=0);
}
export function ninjaPreparation(job,jobs=[]){
  if(!isNinja(job))return null;
  if(job.parameters.operation==='ninja_prepare')return job;
  if(job.parameters.operation==='ninja_render')return jobs.find(j=>j.id===job.parameters.ninja_parent_id&&isNinja(j)&&j.parameters.operation==='ninja_prepare')||null;
  return null;
}
export function matchesNinjaPreparation(form,job){
  return isNinja(job)&&job.parameters.operation==='ninja_prepare'&&fields.every(field=>
    field==='start_seconds'?Math.round(form[field]*24)===Math.round(job.parameters[field]*24):form[field]===job.parameters[field]);
}
export function canRenderNinja(preparation,form){
  return Boolean(preparation?.parameters?.recipe==='w2'&&preparation?.status==='completed'&&preparation.ninja_preview_available&&
    typeof preparation.ninja_mask_sha256==='string'&&/^[a-f0-9]{64}$/i.test(preparation.ninja_mask_sha256)&&matchesNinjaPreparation(form,preparation));
}
export function pointInVideo(clientX,clientY,rect,width,height){
  if(!(rect.width>0&&rect.height>0&&width>0&&height>0))return null;
  const scale=Math.min(rect.width/width,rect.height/height),w=width*scale,h=height*scale;
  const x=(clientX-rect.left-(rect.width-w)/2)/w,y=(clientY-rect.top-(rect.height-h)/2)/h;
  return x>=0&&x<=1&&y>=0&&y<=1?{x,y}:null;
}
export function savePendingNinja(storage,kind,parent,body){
  storage.setItem(pendingKey(kind,parent),JSON.stringify(body));
}
export function readPendingNinja(storage,kind,parent=''){
  try{
    const body=JSON.parse(storage.getItem(pendingKey(kind,parent)));
    if(typeof body?.request_key!=='string'||!body.request_key)return null;
    if(kind==='render')return typeof body.mask_sha256==='string'&&/^[a-f0-9]{64}$/i.test(body.mask_sha256)?body:null;
    return body.video_id&&body.reference_id&&['w1','w2'].includes(body.recipe)&&['head','body'].includes(body.mode)&&
      Number.isFinite(body.start_seconds)&&Number.isInteger(body.frames)&&[body.x,body.y].every(v=>typeof v==='number'&&v>=0&&v<=1)?body:null;
  }catch{return null;}
}
export function clearPendingNinja(storage,kind,parent=''){storage.removeItem(pendingKey(kind,parent));}
export function canSendNinja({busy,sending,pending,valid}){return !sending&&(Boolean(pending)||(!busy&&valid));}
export function ninjaRepeatDraft(selected,jobs,assets){
  if(!['completed','failed','cancelled'].includes(selected?.status))throw Error('Revisa el trabajo pendiente antes de repetir Refinamiento Facial.');
  if(isNinjaShots(selected)){
    const p=selected.parameters;
    if(!assets.some(a=>a.id===p.video_id&&a.kind==='video')||!assets.some(a=>a.id===p.reference_id&&a.kind==='image'))throw Error('Falta el vídeo o la referencia original.');
    return {params:{engine:'h3_ninja',ninja_by_cuts:true,video_id:p.video_id,reference_id:p.reference_id,start_seconds:p.start_seconds,frames:p.frames,seed:p.seed,head_motion:p.ninja_head_motion||'preserve'},selection:{video:p.video_id,image:p.reference_id,face:null},origin:{id:selected.id,created_at:selected.created_at}};
  }
  const preparation=ninjaPreparation(selected,jobs);
  if(!preparation)throw Error('No se pudo recuperar la preparación original de Refinamiento Facial.');
  const p=preparation.parameters;
  for(const [field,kind] of [['video_id','video'],['reference_id','image']]){
    if(!assets.some(a=>a.id===p[field]&&a.kind===kind))throw Error('Falta un archivo original de Refinamiento Facial en la biblioteca.');
  }
  const params={engine:'h3_ninja',...newNinjaForm(p,assets.find(a=>a.id===p.video_id)),head_motion:selected.parameters.operation==='ninja_render'?(selected.parameters.ninja_head_motion==='preserve'?'preserve':'original'):'preserve'};
  return {params,selection:{video:p.video_id,image:p.reference_id,face:null},origin:{id:preparation.id,created_at:preparation.created_at}};
}
