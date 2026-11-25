import {bunnySourceStart} from './bunnyReview.js';
export const LAYERS_MAX_FRAMES=1440;
export const LAYERS_DEFAULTS={video_id:'',reference_id:'',environment_reference_id:'',frames:120,start_seconds:'0',duration_mode:'interval',quality:'balanced',character_direction:'',scene_description:'',interaction_description:'',x:null,y:null,seed:42};
export function layersFormForJob(job){
 if(job?.parameters?.operation!=='motion_layers')return null;
 const p=job.parameters;return {...LAYERS_DEFAULTS,...Object.fromEntries(Object.keys(LAYERS_DEFAULTS).filter(key=>Object.hasOwn(p,key)).map(key=>[key,p[key]])),environment_reference_id:p.environment_reference_id||'',start_seconds:String(p.start_seconds||0)};
}
export function layersIntervalValid(video,frames,start){
 const n=Number(frames),s=Number(start),total=Math.round(Number(video?.duration)*24);
 return Boolean(video&&String(start).trim()!==''&&Number.isFinite(s)&&s>=0&&Number.isInteger(n)&&n>=5&&n<=LAYERS_MAX_FRAMES&&Math.round(bunnySourceStart(s)*24)+n<=total);
}
export function layersDurations(video,current=120){
 const total=Math.round(Number(video?.duration)*24);
 return [...new Set([24,72,120,240,360,720,1440,Number(current),Math.min(total,LAYERS_MAX_FRAMES)])].filter(n=>Number.isInteger(n)&&n>=5&&n<=LAYERS_MAX_FRAMES).sort((a,b)=>a-b);
}
export function layersFullFrames(video){
 const frames=Math.round(Number(video?.duration)*24);
 return Number.isInteger(frames)&&frames>=5&&frames<=LAYERS_MAX_FRAMES?frames:null;
}
export function layersSelectDuration(form,video,value){
 if(value!=='full')return {...form,frames:Number(value),duration_mode:'interval'};
 const frames=layersFullFrames(video);if(frames===null)throw Error('El vídeo completo supera 60 segundos o no tiene una duración válida.');
 const changed=String(form.start_seconds).trim()===''||!Number.isFinite(Number(form.start_seconds))||Number(form.start_seconds)<0||layersSourceFrame(form.start_seconds)!==0;
 return {...form,frames,start_seconds:'0',duration_mode:'full',...(changed?{x:null,y:null}:{})};
}
const roundEven=x=>{const base=Math.floor(x);return x-base===.5?(base%2?base+1:base):Math.round(x);};
export function layersDimensions(video,quality){
 const w=Number(video?.display_width||video?.width),h=Number(video?.display_height||video?.height);
 if(!(w>0&&h>0))return null;
 const area=quality==='high'?640*1152:544*960,ratio=w/h;
 return [ratio,1/ratio].map(r=>Math.max(1,roundEven(Math.sqrt(area*r)/32))*32);
}
export function layersPointValid(form){return ['x','y'].every(key=>Number.isFinite(form[key])&&form[key]>=0&&form[key]<=1);}
export function layersRequest(form,requestKey,reuse=null){
 const fixed=reuse?layersFormForJob(reuse):form;
 if(!fixed)throw Error('No se encontró la primera pasada para reutilizar.');
 return {request_key:requestKey,video_id:fixed.video_id,reference_id:fixed.reference_id,frames:Number(fixed.frames),start_seconds:Number(fixed.start_seconds),quality:fixed.quality,character_direction:fixed.character_direction||'',x:fixed.x,y:fixed.y,seed:Number(fixed.seed),environment_reference_id:form.environment_reference_id||null,scene_description:form.scene_description.trim(),interaction_description:form.interaction_description.trim(),reuse_character_job_id:reuse?.id||null};
}
export function layersIntermediateComparison(job){
 if(job?.parameters?.operation!=='motion_layers'||!job.layers_character_ready||!job.result_available||job.status!=='completed')return null;
 return {source:'/api/jobs/'+job.id+'/layers-intermediate',result:'/api/jobs/'+job.id+'/result',offset:0,frames:job.parameters.frames,sourceLabel:'Primera pasada · Personaje',resultLabel:'Resultado · Nuevo escenario'};
}

export const layersSourceFrame=seconds=>Math.round(bunnySourceStart(seconds)*24);
export function layersAcknowledges(job,request){
 return Boolean(job?.id&&job.parameters?.operation==='motion_layers'&&Object.entries(request).every(([key,value])=>key==='request_key'||job.parameters[key]===value));
}
