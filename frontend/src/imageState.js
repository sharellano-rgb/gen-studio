export const isQwenImage=form=>form?.image_model==='qwen_image21'||form?.engine==='qwen_image21';
export const isImageUpscale=form=>isQwenImage(form)&&form.mode==='upscale';
export const imageEndpoint=body=>isQwenImage(body)?'/images/qwen21/generate':'/images/generate';
export const QWEN_IMAGE_SIZES={standard:{square:[1024,1024],portrait:[896,1152],landscape:[1152,896],vertical:[768,1344],wide:[1344,768]},high:{square:[1440,1440],portrait:[1280,1632],landscape:[1632,1280],vertical:[1088,1920],wide:[1920,1088]}};
const qwenRound=v=>{const f=Math.floor(v),r=v-f;return r===.5?(f%2?f+1:f):Math.round(v);};
export function imageModelForm(form,model){
 if((isQwenImage(form)?'qwen_image21':'moss')===model)return form;
 const next={...form,lora_enabled:false,lora_strength:0,steps:form.mode==='edit'?30:50,cfg:form.mode==='edit'?4:3};
 for(const k of ['image_model','extra_reference_ids','edit_profile','mystic_strength','distill_strength','edit_sampler','edit_scheduler','realism_lora','realism_strength','upscale_strength','upscale_denoise'])delete next[k];
 if(form.mode==='upscale'){next.mode='edit';next.steps=30;next.cfg=4;}
 return model==='qwen_image21'?{...next,image_model:model,steps:20,cfg:3,extra_reference_ids:[]}:next;
}
// Preserve the original text-to-image keys so pending requests remain retryable.
export const IMAGE_DEFAULTS={prompt:'',negative_prompt:'',format:'square',quality:'standard',steps:50,cfg:3,seed:42,lora_enabled:false,lora_strength:0};
export const IMAGE_EDIT_DEFAULTS={mode:'edit',edit_reference_id:'',edit_strength:1,edit_reference_width:0,edit_reference_height:0,steps:30,cfg:4,lora_enabled:false,lora_strength:0};
export const IMAGE_MYSTIC_DEFAULTS={edit_profile:'mystic_base8',steps:8,cfg:3,mystic_strength:0.7,distill_strength:0.65,edit_sampler:'er_sde',edit_scheduler:'sgm_uniform'};
const MYSTIC_KEYS=['edit_profile','mystic_strength','distill_strength','edit_sampler','edit_scheduler'];
const REALISM_KEYS=['realism_lora','realism_strength'];
const realismOn=form=>(form.realism_lora||'none')!=='none';
const EDIT_KEYS=['mode','edit_reference_id','edit_strength','edit_reference_width','edit_reference_height'];
export const IMAGE_SIZES={standard:{square:[1024,1024],portrait:[896,1152],landscape:[1152,896],vertical:[768,1344],wide:[1344,768]},high:{square:[1440,1440],portrait:[1264,1632],landscape:[1632,1264],vertical:[1088,1920],wide:[1920,1088]}};
export function imageSize(form){
 if(isQwenImage(form)){
  if(isImageUpscale(form)){
   const w=Number(form.edit_reference_width),h=Number(form.edit_reference_height);
   if(!['standard','high'].includes(form.quality)||!Number.isFinite(w)||!Number.isFinite(h)||Math.min(w,h)<=0)return null;
   const scale=Math.sqrt((form.quality==='high'?4:2)*1024*1024/(w*h));
   const size=[w,h].map(v=>qwenRound(v*scale/32)*32);
   return size.every(v=>v>=64&&v<=4096)&&size[0]>=w&&size[1]>=h&&(size[0]>w||size[1]>h)?size:null;
  }
  if(form.mode!=='edit')return QWEN_IMAGE_SIZES[form.quality]?.[form.format]||null;
  const w=Number(form.edit_reference_width),h=Number(form.edit_reference_height),r=form.quality==='standard'?1024:1440;
  if(!['standard','high'].includes(form.quality)||!Number.isFinite(w)||!Number.isFinite(h)||Math.min(w,h)<=0)return null;
  const size=[w/h,h/w].map(ratio=>Math.max(32,qwenRound(Math.sqrt(r*r*ratio)/32)*32));
  return size.every(v=>v>=64&&v<=4096)?size:null;
 }
 if(form.mode!=='edit')return IMAGE_SIZES[form.quality]?.[form.format]||null;
 if(!['standard','high'].includes(form.quality))return null;
 const w=Number(form.edit_reference_width),h=Number(form.edit_reference_height);
 if(!Number.isFinite(w)||!Number.isFinite(h)||w<=0||h<=0)return null;
 const scale=Math.sqrt((form.quality==='high'?2:1)*1024*1024/(w*h));
 const size=[w,h].map(v=>Math.max(16,Math.round(v*scale/16)*16));
 return size.every(v=>v>=64&&v<=4096)?size:null;
}
export function imageValid(form){
 if(isQwenImage(form)){
  const up=isImageUpscale(form),ids=[form.edit_reference_id,...(form.extra_reference_ids||[])],edit=form.mode==='edit'||up;
  const controls=!up||['upscale_strength','upscale_denoise'].every(k=>form[k]!=null&&String(form[k]).trim()!==''&&Number.isFinite(Number(form[k])))&&Number(form.upscale_strength)>=.1&&Number(form.upscale_strength)<=1.5&&Number(form.upscale_denoise)>=.3&&Number(form.upscale_denoise)<=1;
  return Boolean(controls&&imageSize(form)&&(up||form.prompt?.trim().length>=3)&&typeof form.prompt==='string'&&form.prompt.length<=12000&&(form.negative_prompt||'').length<=3000&&String(form.seed??'').trim()!==''&&Number.isSafeInteger(Number(form.seed))&&Number(form.seed)>=0&&Number.isInteger(Number(form.steps))&&Number(form.steps)>=(up?12:4)&&Number(form.steps)<=(up?40:80)&&String(form.cfg??'').trim()!==''&&Number.isFinite(Number(form.cfg))&&Number(form.cfg)>=1&&Number(form.cfg)<=(up?4:6)&&(!edit||ids.length<=(up?1:4)&&ids.every(v=>typeof v==='string'&&v.length>0)&&new Set(ids).size===ids.length));
 }
 const edit=form.mode==='edit',profile=form.edit_profile||'standard',mystic=profile==='mystic_base8';
 const recipeValid=profile==='standard'||mystic&&!form.lora_enabled&&String(form.mystic_strength).trim()!==''&&Number.isFinite(Number(form.mystic_strength))&&Number(form.mystic_strength)>=0&&Number(form.mystic_strength)<=2&&Number.isFinite(Number(form.distill_strength))&&Number(form.distill_strength)>=0.1&&Number(form.distill_strength)<=2&&['er_sde','euler'].includes(form.edit_sampler)&&['normal','sgm_uniform'].includes(form.edit_scheduler);
 if(realismOn(form)&&(!['lenovo','oldnokia','smartphone'].includes(form.realism_lora)||form.realism_strength==null||String(form.realism_strength).trim()===''||!Number.isFinite(Number(form.realism_strength))||Number(form.realism_strength)<0||Number(form.realism_strength)>2))return false;
 return Boolean(recipeValid&&imageSize(form)&&form.prompt?.trim().length>=3&&form.prompt.length<=12000&&String(form.seed).trim()!==''&&Number.isSafeInteger(Number(form.seed))&&Number(form.seed)>=0&&Number.isInteger(Number(form.steps))&&Number(form.steps)>=(mystic?4:20)&&Number(form.steps)<=80&&Number.isFinite(Number(form.cfg))&&Number(form.cfg)>=1&&Number(form.cfg)<=6&&(form.negative_prompt||'').length<=3000&&Number.isFinite(Number(form.lora_strength))&&Number(form.lora_strength)>=0&&Number(form.lora_strength)<=2&&(!edit||typeof form.edit_reference_id==='string'&&form.edit_reference_id.length>0&&Number.isFinite(Number(form.edit_strength))&&Number(form.edit_strength)>=0.1&&Number(form.edit_strength)<=2));
}
export function imagePreflight(form){
 if(isQwenImage(form)){const mode=isImageUpscale(form)?'upscale':form.mode==='edit'?'edit':'generate';return {key:'qwen_image21:'+mode,path:'/images/qwen21/preflight?mode='+mode};}
 const mode=form.mode==='edit'?'edit':'generate',style=Boolean(form.lora_enabled)&&Number(form.lora_strength)>0;
 if(form.edit_profile==='mystic_base8'){
  const query=new URLSearchParams({mode,edit_profile:form.edit_profile,mystic_strength:Number(form.mystic_strength),distill_strength:Number(form.distill_strength),edit_sampler:form.edit_sampler,edit_scheduler:form.edit_scheduler});
  if(realismOn(form)){query.set('realism_lora',form.realism_lora);query.set('realism_strength',Number(form.realism_strength));}
  return {key:query.toString(),path:'/images/preflight?'+query};
 }
 if(realismOn(form)){
  const query=new URLSearchParams({mode,realism_lora:form.realism_lora,realism_strength:Number(form.realism_strength),lora_enabled:Boolean(form.lora_enabled),lora_strength:Number(form.lora_strength)});
  return {key:query.toString(),path:'/images/preflight?'+query};
 }
 return {key:mode+':'+style,path:'/images/preflight'+(mode==='edit'?'?mode=edit'+(style?'&lora_enabled=true':''):style?'?lora_enabled=true':'')};
}
export function imageRequest(form,key){
 if(isQwenImage(form)){const up=isImageUpscale(form);return {request_key:key,image_model:'qwen_image21',mode:up?'upscale':form.mode==='edit'?'edit':'generate',prompt:form.prompt.trim(),negative_prompt:form.negative_prompt||'',format:form.format,quality:form.quality,steps:Number(form.steps),cfg:Number(form.cfg),seed:Number(form.seed),edit_reference_id:form.mode==='edit'||up?form.edit_reference_id:null,extra_reference_ids:form.mode==='edit'?[...(form.extra_reference_ids||[])]:[],...(up?{upscale_strength:Number(form.upscale_strength),upscale_denoise:Number(form.upscale_denoise)}:{})};}
 const body={...Object.fromEntries(Object.keys(IMAGE_DEFAULTS).map(k=>[k,form[k]??IMAGE_DEFAULTS[k]])),prompt:form.prompt.trim(),steps:Number(form.steps),cfg:Number(form.cfg),seed:Number(form.seed),lora_enabled:Boolean(form.lora_enabled),lora_strength:Number(form.lora_strength),request_key:key};
 const edit=form.mode==='edit'?{...body,mode:'edit',edit_reference_id:form.edit_reference_id,edit_strength:Number(form.edit_strength)}:body;
 const recipe=form.edit_profile==='mystic_base8'?{...edit,edit_profile:form.edit_profile,mystic_strength:Number(form.mystic_strength),distill_strength:Number(form.distill_strength),edit_sampler:form.edit_sampler,edit_scheduler:form.edit_scheduler}:edit;
 return realismOn(form)?{...recipe,realism_lora:form.realism_lora,realism_strength:Number(form.realism_strength)}:recipe;
}
export function imageAcknowledges(job,body){
 if(isQwenImage(body))return Boolean(job?.id&&job.parameters?.engine==='qwen_image21'&&job.parameters.operation==='image_generation'&&Object.entries(body).filter(([k])=>k!=='request_key').every(([k,v])=>JSON.stringify(job.parameters.qwen_image_request?.[k])===JSON.stringify(v)));
 const p=job?.parameters;
 if(!job?.id||p?.operation!=='image_generation'||p.engine!=='moss_image'||!Object.keys(IMAGE_DEFAULTS).every(k=>p[k]===body[k]))return false;
 if(body.mode==='edit'){
  if(p.mode!=='edit'||p.edit_reference_id!==body.edit_reference_id||p.edit_strength!==body.edit_strength)return false;
 }else if(p.mode==='edit')return false;
 if((p.realism_lora||'none')!==(body.realism_lora||'none')||realismOn(body)&&p.realism_strength!==body.realism_strength)return false;
 return body.edit_profile==='mystic_base8'?MYSTIC_KEYS.every(k=>p[k]===body[k]):!p.edit_profile||p.edit_profile==='standard';
}
export function imageForm(job){
 if(isQwenImage(job?.parameters)){
  const p=job.parameters,keys=['prompt','negative_prompt','format','quality','steps','cfg','seed','mode','edit_reference_id','edit_reference_width','edit_reference_height','extra_reference_ids','upscale_strength','upscale_denoise'];
  return {...IMAGE_DEFAULTS,image_model:'qwen_image21',mode:'generate',steps:20,cfg:3,extra_reference_ids:[],...Object.fromEntries(keys.filter(k=>Object.hasOwn(p,k)).map(k=>[k,p[k]]))};
 }
 const p=job?.parameters||{},form={...IMAGE_DEFAULTS,...Object.fromEntries(Object.keys(IMAGE_DEFAULTS).filter(k=>Object.hasOwn(p,k)).map(k=>[k,p[k]]))};
 if(p.mode!=='edit'){
  if(p.edit_profile!=='mystic_base8')return realismOn(p)?{...form,...Object.fromEntries(REALISM_KEYS.filter(k=>Object.hasOwn(p,k)).map(k=>[k,p[k]]))}:form;
  return {...form,...IMAGE_MYSTIC_DEFAULTS,...Object.fromEntries(['steps','cfg',...MYSTIC_KEYS,...(realismOn(p)?REALISM_KEYS:[])].filter(k=>Object.hasOwn(p,k)).map(k=>[k,p[k]]))};
 }
 const recipe=p.edit_profile==='mystic_base8'?IMAGE_MYSTIC_DEFAULTS:{};
 const edit={...form,...IMAGE_EDIT_DEFAULTS,...recipe,...Object.fromEntries([...EDIT_KEYS,'steps','cfg','lora_enabled','lora_strength',...MYSTIC_KEYS,...(realismOn(p)?REALISM_KEYS:[])].filter(k=>Object.hasOwn(p,k)).map(k=>[k,p[k]]))};
 if(!p.edit_profile||p.edit_profile==='standard')for(const k of MYSTIC_KEYS)delete edit[k];
 return edit;
}
export function imageEditProfileForm(form,profile){
 if(!['standard','mystic_base8'].includes(profile)||(form.edit_profile||'standard')===profile)return form;
 const next={...form,lora_enabled:false,lora_strength:0};
 for(const k of REALISM_KEYS)delete next[k];
 for(const k of MYSTIC_KEYS)delete next[k];
 const defaults=form.mode==='edit'?IMAGE_EDIT_DEFAULTS:IMAGE_DEFAULTS;
 return profile==='mystic_base8'?{...next,...IMAGE_MYSTIC_DEFAULTS}:{...next,steps:defaults.steps,cfg:defaults.cfg};
}
export function imageModeForm(form,mode){
 if(isQwenImage(form)){
  if(form.mode===mode)return form;
  if(mode==='upscale')return {...form,mode,prompt:'',negative_prompt:'',steps:25,cfg:1,quality:'high',extra_reference_ids:[],upscale_strength:1,upscale_denoise:.8};
  const next={...form,mode};delete next.upscale_strength;delete next.upscale_denoise;
  return isImageUpscale(form)?{...next,steps:20,cfg:3}:next;
 }
 if((form.mode==='edit'?'edit':'generate')===mode)return form;
 const recipe=form.edit_profile==='mystic_base8'?Object.fromEntries([...MYSTIC_KEYS,...REALISM_KEYS,'steps','cfg'].filter(k=>Object.hasOwn(form,k)).map(k=>[k,form[k]])):realismOn(form)?Object.fromEntries(REALISM_KEYS.filter(k=>Object.hasOwn(form,k)).map(k=>[k,form[k]])):{};
 if(mode==='edit')return {...form,...IMAGE_EDIT_DEFAULTS,...recipe};
 return {...Object.fromEntries(Object.keys(IMAGE_DEFAULTS).map(k=>[k,form[k]??IMAGE_DEFAULTS[k]])),steps:IMAGE_DEFAULTS.steps,cfg:IMAGE_DEFAULTS.cfg,...recipe};
}
export function imageReferenceForm(form,asset){
 return {...form,mode:isImageUpscale(form)?'upscale':'edit',edit_reference_id:asset?.id||'',edit_reference_width:Number(asset?.display_width||asset?.width||0),edit_reference_height:Number(asset?.display_height||asset?.height||0)};
}
export function readImageDraft(storage){
 try{return imageForm({parameters:JSON.parse(storage.getItem('moss-image-draft-v1')||'null')||{}});}catch{return {...IMAGE_DEFAULTS};}
}
