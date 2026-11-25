import {validAlissonQuality,effectiveAlissonQuality} from './alissonQuality.js';
export const ALISSON_ENGINE='h3_alisson';
export const ALISSON_MAX_FRAMES=1440;
export const ALISSON_PENDING_KEY='gen-alisson-pending-v1';
const fields=['video_id','reference_id','face_reference_id','start_seconds','frames','seed','character_details','target','quality','auto_character_sheet','nsfw_h3_motion_booster','nsfw_penis_v2','nsfw_hm_pussy','nsfw_minimax_unlocked','nsfw_strength'];
export const alissonUsesCharacterSheet=form=>form?.auto_character_sheet===true;
export const alissonCharacterSheetReady=(form,options)=>!alissonUsesCharacterSheet(form)||options?.character_sheet?.available===true;
export const isAlisson=job=>job?.parameters?.engine===ALISSON_ENGINE;
export function alissonSourceFrames(video){const duration=Number(video?.duration);return Number.isFinite(duration)&&duration>0?Math.ceil(duration*24):0;}
export function alissonDefaults(video){return {video_id:video?.id||'',reference_id:'',face_reference_id:'',start_seconds:0,frames:Math.min(120,alissonSourceFrames(video)||120),seed:123456,character_details:'',target:'',quality:'preview',auto_character_sheet:false,nsfw_h3_motion_booster:false,nsfw_penis_v2:false,nsfw_hm_pussy:false,nsfw_minimax_unlocked:false,nsfw_strength:1};}
export function alissonChangeVideo(form,video){return {...alissonDefaults(video),reference_id:form.reference_id,face_reference_id:form.face_reference_id||'',seed:form.seed,character_details:form.character_details,quality:effectiveAlissonQuality(form.quality),auto_character_sheet:alissonUsesCharacterSheet(form)};}
export function alissonChangeReference(form,id){return id===form.reference_id?form:{...form,reference_id:id,face_reference_id:'',character_details:''};}
export function alissonForm(parameters,video){
  const original=parameters?.alisson_request||parameters||{},form=alissonDefaults(video);
  for(const field of fields)if(Object.hasOwn(original,field)){
    const value=original[field];
    if(field==='face_reference_id'){form[field]=typeof value==='string'?value:'';continue;}
    if(field==='auto_character_sheet'){form[field]=value===true;continue;}
    if(['nsfw_h3_motion_booster','nsfw_penis_v2','nsfw_hm_pussy','nsfw_minimax_unlocked'].includes(field)){form[field]=value===true;continue;}
    if(field==='nsfw_strength'){if(typeof value==='number'&&Number.isFinite(value)&&value>=0&&value<=2)form[field]=value;continue;}
    if(field==='quality'&&!validAlissonQuality(value))continue;
    if(['start_seconds','frames','seed'].includes(field)&&!Number.isFinite(value))continue;
    if(['video_id','reference_id','character_details','target'].includes(field)&&typeof value!=='string')continue;
    form[field]=value;
  }
  return form;
}
export function alissonBody(form,key){return {request_key:key,...Object.fromEntries(fields.filter(field=>field!=='auto_character_sheet'||alissonUsesCharacterSheet(form)).map(field=>[field,field==='quality'?effectiveAlissonQuality(form[field]):field==='face_reference_id'?(form[field]||null):form[field]]))};}
export function validAlissonForm(form,assets,maxFrames=ALISSON_MAX_FRAMES){
  const video=assets.find(a=>a.id===form.video_id&&a.kind==='video'),reference=assets.find(a=>a.id===form.reference_id&&a.kind==='image');
  const faceValid=form.face_reference_id==null||form.face_reference_id===''||(typeof form.face_reference_id==='string'&&form.face_reference_id!==form.reference_id&&assets.some(a=>a.id===form.face_reference_id&&a.kind==='image'));
  return Boolean(video&&reference&&faceValid&&(form.auto_character_sheet===undefined||typeof form.auto_character_sheet==='boolean')&&Number.isFinite(form.start_seconds)&&form.start_seconds>=0&&form.start_seconds<=3600&&
    Number.isInteger(form.frames)&&form.frames>=5&&form.frames<=Math.min(ALISSON_MAX_FRAMES,maxFrames)&&
    Math.round(form.start_seconds*24)+form.frames<=alissonSourceFrames(video)&&Number.isSafeInteger(form.seed)&&form.seed>=0&&
    typeof form.character_details==='string'&&form.character_details.length<=1000&&typeof form.target==='string'&&form.target.length<=300&&validAlissonQuality(effectiveAlissonQuality(form.quality)));
}
export function alissonInterval(form,video,{full=false,maxFrames=ALISSON_MAX_FRAMES}={}){
  const remaining=Math.max(0,alissonSourceFrames(video)-Math.round(form.start_seconds*24));
  return {...form,frames:Math.min(full?maxFrames:120,maxFrames,ALISSON_MAX_FRAMES,remaining)};
}
export function savePendingAlisson(storage,body){storage.setItem(ALISSON_PENDING_KEY,JSON.stringify(body));}
export function readPendingAlisson(storage){
  try{
    const body=JSON.parse(storage.getItem(ALISSON_PENDING_KEY)||'null');
    return body&&typeof body.request_key==='string'&&body.request_key&&typeof body.video_id==='string'&&body.video_id&&
      typeof body.reference_id==='string'&&body.reference_id&&Number.isFinite(body.start_seconds)&&body.start_seconds>=0&&
      (body.face_reference_id==null||(typeof body.face_reference_id==='string'&&body.face_reference_id&&body.face_reference_id!==body.reference_id))&&
      (body.auto_character_sheet===undefined||typeof body.auto_character_sheet==='boolean')&&
      Number.isInteger(body.frames)&&body.frames>=5&&body.frames<=360&&Number.isSafeInteger(body.seed)&&body.seed>=0&&
      typeof body.character_details==='string'&&typeof body.target==='string'&&validAlissonQuality(effectiveAlissonQuality(body.quality))?body:null;
  }catch{return null;}
}
export function clearPendingAlisson(storage){storage.removeItem(ALISSON_PENDING_KEY);}
export function acknowledgedAlisson(result,body){
  return Boolean(typeof result?.id==='string'&&result.id&&result.parameters?.engine===ALISSON_ENGINE&&
    result.parameters?.operation==='alisson_swap'&&fields.every(field=>field==='quality'?
      effectiveAlissonQuality(result.parameters.alisson_request?.quality)===effectiveAlissonQuality(body.quality):field==='auto_character_sheet'?
      (result.parameters.alisson_request?.[field]===undefined||typeof result.parameters.alisson_request?.[field]==='boolean')&&alissonUsesCharacterSheet(result.parameters.alisson_request)===alissonUsesCharacterSheet(body):field==='face_reference_id'?
      (result.parameters.alisson_request?.[field]??null)===(body[field]??null):result.parameters.alisson_request?.[field]===body[field]));
}
export function definitiveAlissonRejection(status){return Number.isInteger(status)&&status>=400&&status<500&&![408,429].includes(status);}
export function canSendAlisson({pending,sending,uploading,busy,available,valid}){return !sending&&!uploading&&(Boolean(pending)||(!busy&&available&&valid));}
export function alissonRepeatDraft(job,assets){
  if(!isAlisson(job)||!['completed','failed','cancelled'].includes(job.status))throw Error('Revisa el trabajo pendiente antes de repetir este método.');
  const p=job.parameters,form=alissonForm(p,assets.find(a=>a.id===p.video_id));
  for(const [field,kind] of [['video_id','video'],['reference_id','image']])if(!assets.some(a=>a.id===form[field]&&a.kind===kind))throw Error('Falta un archivo original de esta toma en la biblioteca.');
  if(form.face_reference_id&&!assets.some(a=>a.id===form.face_reference_id&&a.kind==='image'))throw Error('Falta la referencia facial original de esta toma en la biblioteca.');
  return {params:{engine:ALISSON_ENGINE,...form},selection:{video:form.video_id,image:form.reference_id,face:form.reference_id},origin:{id:job.id,created_at:job.created_at}};
}
