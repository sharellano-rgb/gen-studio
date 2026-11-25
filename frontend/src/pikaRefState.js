import {PIKA_DETAILED,pikaRefMethod,pikaRefEndpoint} from './pikaRefMethods.js';
export const MAX_FRAMES=2880;
export function remainingFrames(duration,startSeconds=0,maxFrames=MAX_FRAMES){
 const limit=Number.isInteger(maxFrames)&&maxFrames>=5?Math.min(maxFrames,MAX_FRAMES):MAX_FRAMES;
 if(!Number.isFinite(startSeconds)||startSeconds<0)return 0;
 if(duration==null)return limit;
 if(!Number.isFinite(duration)||duration<=0)return 0;
 return Math.min(limit,Math.max(0,Math.round(duration*24)-Math.round(startSeconds*24)));
}
export const PROFILE='pika-ref2va-480p-6steps-v1';
export const PROFILE_768='pika-ref2va-768p-6steps-v1';
export const knownProfile=value=>[PROFILE,PROFILE_768].includes(value);
export const profileTier=value=>value===PROFILE_768?'768p':'480p';
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const keys=(mode,method='pika_ref2va')=>{pikaRefEndpoint(method);const prefix=method===PIKA_DETAILED?'gen-pika-ref2va-detailed-':'gen-pika-ref2va-';return {draft:prefix+mode+'-draft-v1',pending:prefix+mode+'-pending-v1'};};
export function pendingRequest(storage,mode,method='pika_ref2va'){
 const raw=storage.getItem(keys(mode,method).pending);if(!raw)return null;
 let p;try{p=JSON.parse(raw);}catch{throw Error('No se puede leer el envío pendiente de Pika Ref2VA.');}
 if(!uuid.test(p?.request_key)||!uuid.test(p.video_id)||!uuid.test(p.reference_id)||p.mode!==mode||!knownProfile(p.profile)||!Number.isSafeInteger(p.seed)||p.seed<0||!Number.isInteger(p.frames)||p.frames<5||p.frames>MAX_FRAMES||!Number.isFinite(p.start_seconds)||p.start_seconds<0||!['portrait','landscape'].includes(p.format)||typeof p.prompt!=='string'||p.prompt.trim().length<3||p.prompt.length>3000||typeof p.target!=='string'||!p.target.trim()||p.target.length>300)throw Error('El envío pendiente de Pika no es válido. Consérvalo para recuperar la toma.');
 return p;
}
export function rememberRequest(storage,mode,p,key,method='pika_ref2va'){
 const old=pendingRequest(storage,mode,method);if(old)return old;
 const body={request_key:key,video_id:p.video_id,reference_id:p.reference_id,mode,prompt:p.prompt.trim(),target:p.target.trim(),frames:p.frames,start_seconds:p.start_seconds,format:p.format,seed:p.seed,profile:p.profile??PROFILE};
 storage.setItem(keys(mode,method).pending,JSON.stringify(body));return pendingRequest(storage,mode,method);
}
export function acknowledge(storage,mode,body,job,method='pika_ref2va'){
 const expected=job?.parameters?.pika_ref2va_request;
 if(!uuid.test(job?.id)||pikaRefMethod(job)!==method||!expected||!Object.keys(body).filter(k=>k!=='request_key').every(k=>expected[k]===body[k]))throw Error('La respuesta no corresponde a esta solicitud de Pika. Recupera el mismo envío.');
 storage.removeItem(keys(mode,method).pending);
}
