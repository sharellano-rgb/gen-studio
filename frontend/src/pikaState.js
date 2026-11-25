export const PENDING_KEY='gen-pika-pending-v1';
export const PROFILE='pika-i2va-480p-5s-v1';
export const PROFILES=['pika-i2va-480p-5s-v1','pika-i2va-720p-5s-v1','h3-i2va-480p-5s-v1','h3-i2va-720p-5s-v1'];
export const profileEngine=profile=>profile?.startsWith('h3-')?'h3_i2va':'pika_h3_flex';
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export function pendingRequest(storage){
 const raw=storage.getItem(PENDING_KEY);if(!raw)return null;
 let value;try{value=JSON.parse(raw);}catch{throw Error('La solicitud pendiente de Pika no se puede leer. Conserva este aviso para recuperar tu toma.');}
 if(!uuid.test(value?.request_key)||!uuid.test(value?.reference_id)||typeof value.prompt!=='string'||value.prompt.trim().length<3||value.prompt.length>3000||!Number.isSafeInteger(value.seed)||value.seed<0||!['landscape','portrait'].includes(value.format)||!PROFILES.includes(value.profile))throw Error('La solicitud pendiente de Pika no es válida; no se enviará otra toma automáticamente.');
 return value;
}
export function rememberRequest(storage,p,key){
 const previous=pendingRequest(storage);if(previous)return previous;
 const body={request_key:key,reference_id:p.reference_id,prompt:p.prompt.trim(),format:p.format,seed:p.seed,profile:p.profile||PROFILE};
 storage.setItem(PENDING_KEY,JSON.stringify(body));return body;
}
export function acknowledge(storage,body,job){
 if(!uuid.test(job?.id)||job?.parameters?.engine!==profileEngine(body.profile)||!['reference_id','prompt','format','seed','profile'].every(k=>job.parameters[k]===body[k]))throw Error('La respuesta no corresponde al envío pendiente. Recupera el mismo envío.');
 storage.removeItem(PENDING_KEY);
}
