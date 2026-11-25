export const ENGINE='viggle_remake';
export const LABEL='Viggle Remake · Experimental';
export const option={id:ENGINE,label:LABEL,detail:'736p · Qwen · Continuidad nativa de 17 fotogramas'};
export const isViggle=value=>(value?.parameters||value)?.engine===ENGINE;
export {remainingFrames} from './pikaRefState.js';
export const MAX_FRAMES=2880,MAX_SEED=9007199254739991;
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export function keys(mode){if(!['swap','motion'].includes(mode))throw Error('Modo Viggle desconocido.');return {draft:`gen-viggle-remake-${mode}-draft-v1`,pending:`gen-viggle-remake-${mode}-pending-v1`};}
export function validCharacters(p){
 const extra=p?.additional_characters??[];
 if(!Array.isArray(extra)||extra.length>3)return false;
 if(!extra.length)return true;
 if(p.reference_mode!=='appearance'||extra.some(c=>!c||!uuid.test(c.reference_id)||typeof c.target!=='string'||!c.target.trim()||c.target.length>300||/[<>]/.test(c.target)||Object.keys(c).some(k=>!['target','reference_id'].includes(k))))return false;
 const targets=[p.target,...extra.map(c=>c.target)].map(v=>(v||'').trim().toLowerCase());return new Set(targets).size===targets.length;
}
const canonical=v=>JSON.stringify(v&&typeof v==='object'?(Array.isArray(v)?v.map(x=>JSON.parse(canonical(x))):Object.fromEntries(Object.keys(v).sort().map(k=>[k,JSON.parse(canonical(v[k]))]))):v);
export function valid(p){return (p.auto_detail_corrections===undefined||typeof p.auto_detail_corrections==='boolean'&&(!p.auto_detail_corrections||p.auto_keyframes===true))&&(p.auto_keyframes===undefined||typeof p.auto_keyframes==='boolean'&&(!p.auto_keyframes||p.reference_mode==='appearance'))&&validCharacters(p)&&uuid.test(p?.request_key)&&uuid.test(p.video_id)&&uuid.test(p.reference_id)&&['swap','motion'].includes(p.mode)&&p.short_edge===736&&['portrait','landscape'].includes(p.format)&&Number.isSafeInteger(p.seed)&&p.seed>=0&&p.seed<=MAX_SEED&&Number.isInteger(p.frames)&&p.frames>=5&&p.frames<=MAX_FRAMES&&Number.isFinite(p.start_seconds)&&p.start_seconds>=0&&p.start_seconds<=86400&&typeof p.prompt==='string'&&p.prompt.trim().length>=3&&p.prompt.length<=3000&&typeof p.target==='string'&&p.target.trim().length>0&&p.target.length<=300&&!/[<>]/.test(p.prompt+p.target)&&((p.reference_mode==='appearance'&&p.pin_frame===null)||(p.reference_mode==='painted_frame'&&Number.isInteger(p.pin_frame)&&p.pin_frame>=0&&p.pin_frame<p.frames));}
export function pendingRequest(storage,mode){
 const raw=storage.getItem(keys(mode).pending);if(!raw)return null;
 let p;try{p=JSON.parse(raw);}catch{throw Error('No se puede leer el envío pendiente de Viggle. Se conserva para recuperarlo.');}
 if(!valid(p)||p.mode!==mode)throw Error('El envío pendiente de Viggle no es válido. Se conserva para recuperar la toma.');return p;
}
export function requestBody(mode,p,key){return {...(p.auto_detail_corrections?{auto_detail_corrections:true}:{}),...(p.auto_keyframes?{auto_keyframes:true}:{}),request_key:key,mode,video_id:p.video_id,reference_id:p.reference_id,prompt:p.prompt.trim(),target:p.target.trim(),frames:p.frames,start_seconds:p.start_seconds,format:p.format,short_edge:736,seed:p.seed,reference_mode:p.reference_mode,pin_frame:p.reference_mode==='painted_frame'?p.pin_frame:null,...(p.additional_characters?.length?{additional_characters:p.additional_characters.map(c=>({target:c.target.trim(),reference_id:c.reference_id}))}:{})};}
export function rememberRequest(storage,mode,p,key){
 const old=pendingRequest(storage,mode);if(old)return old;
 const body=requestBody(mode,p,key);if(!valid(body))throw Error('Revisa el vídeo, la referencia, la duración y las indicaciones.');
 storage.setItem(keys(mode).pending,JSON.stringify(body));return pendingRequest(storage,mode);
}
export function acknowledge(storage,mode,body,job){
 const expected=job?.parameters?.viggle_request;
 if(!uuid.test(job?.id)||!isViggle(job)||!expected||!Object.keys(body).filter(k=>k!=='request_key').every(k=>expected[k]!==undefined&&canonical(expected[k])===canonical(body[k])))throw Error('La respuesta no corresponde a esta solicitud. Recupera el mismo envío de Viggle.');
 storage.removeItem(keys(mode).pending);
}
