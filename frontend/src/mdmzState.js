export const MDMZ_ENGINE='h3_mdmz';
export const MDMZ_FIELDS=['video_id','reference_id','prompt','target','frames','start_seconds','seed','speed','background','megapixels','guide_megapixels','threshold','audio','preparation'];
export const isMdmz=job=>job?.parameters?.engine===MDMZ_ENGINE;
export function mdmzFullFrames(video,start=0){const frames=Math.round((video?.duration||0)*24)-Math.round(start*24);return frames>=5&&frames<=362?frames:null;}
export function mdmzDefaults(){return {video_id:'',reference_id:'',prompt:'',target:'person',frames:107,start_seconds:0,seed:150020260820,speed:'normal',background:'video',megapixels:0.5,guide_megapixels:0.15,threshold:0.5,audio:true,preparation:'native'};}
export function mdmzForm(value){const p=value?.mdmz_request||value||{},defaults=mdmzDefaults();return Object.fromEntries(MDMZ_FIELDS.map(key=>[key,p[key]??defaults[key]]));}
export function mdmzPromptValid(value){return typeof value==='string'&&['subject_definitions','summary','retention_analysis','detailed_description','overall_soundscape','non_diegetic_music'].every(key=>new RegExp('^'+key+':','m').test(value))&&value.includes('<Video 1>')&&value.includes('<Picture 1>')&&!/<(?:Picture|Video|Audio) (?!1>)[^>]+>/.test(value);}
export function mdmzValid(form,video,reference){return Boolean(video&&reference&&mdmzPromptValid(form.prompt)&&Number.isInteger(form.frames)&&form.frames>=5&&form.frames<=362&&Number.isFinite(form.start_seconds)&&form.start_seconds>=0&&form.start_seconds<=3600&&Math.round(form.start_seconds*24)+form.frames<=Math.round(video.duration*24)+1&&Number.isSafeInteger(form.seed)&&form.seed>=0&&['normal','turbo'].includes(form.speed)&&['video','image'].includes(form.background)&&[0.5,1].includes(form.megapixels)&&Number.isFinite(form.guide_megapixels)&&form.guide_megapixels>=0.1&&form.guide_megapixels<=0.4&&Number.isFinite(form.threshold)&&form.threshold>=0.05&&form.threshold<=0.95);}
export function mdmzRequest(form,key){return {...mdmzForm(form),request_key:key};}
export function mdmzAcknowledges(job,body){return isMdmz(job)&&job?.parameters?.operation===(body.diagnostics?'mdmz_diagnostics':'mdmz_swap')&&MDMZ_FIELDS.every(key=>job.parameters.mdmz_request?.[key]===body[key]);}
export function mdmzAnalysisRequest(form){return {video_id:form.video_id,reference_ids:[form.reference_id],frames:form.frames,start_seconds:form.start_seconds,intent:'Replace the subject described as '+form.target+'.',reference_roles:['Identidad del personaje'],reference_type:'auto',current_prompt:form.prompt,background:form.background};}
export function mdmzAnalysisMatches(record,form){const expected=mdmzAnalysisRequest(form),actual=record?.request;return Boolean(actual&&Object.keys(expected).every(key=>JSON.stringify(actual[key])===JSON.stringify(expected[key])));}
export function mdmzRepeatDraft(job,assets){
 if(!isMdmz(job)||!['completed','failed','cancelled'].includes(job.status))throw Error('Revisa el trabajo MDMZ antes de reutilizar sus ajustes.');
 const params={...mdmzForm(job.parameters),engine:MDMZ_ENGINE};
 if(!assets.some(a=>a.id===params.video_id&&a.kind==='video')||!assets.some(a=>a.id===params.reference_id&&a.kind==='image'))throw Error('Falta el vídeo o la referencia original de MDMZ.');
 return {params,selection:{video:params.video_id,image:params.reference_id,face:null},origin:{id:job.id,created_at:job.created_at}};
}
