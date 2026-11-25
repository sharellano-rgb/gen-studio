export const longRetryKey=id=>'gen-long-retry:'+id;
export function readLongRetry(storage,parentId){
  try{
    const value=JSON.parse(storage.getItem(longRetryKey(parentId))||'null');
    if(!value||typeof value.request_key!=='string'||!value.request_key.trim()||!Number.isInteger(value.segment_index)||value.segment_index<0||!['analysis','generation'].includes(value.mode)||typeof value.reject_completed!=='boolean')return null;
    if(Object.hasOwn(value,'seed')&&(!Number.isSafeInteger(value.seed)||value.seed<0||value.mode!=='generation'))return null;
    return {request_key:value.request_key,segment_index:value.segment_index,mode:value.mode,reject_completed:value.reject_completed,...(Object.hasOwn(value,'seed')?{seed:value.seed}:{})};
  }catch{return null;}
}
export function longRetryBody(segment,mode,requestKey,seed){
  if(!segment||!(segment.retry_modes||[]).includes(mode))throw Error('Este tramo no admite ese reintento. Actualiza su estado.');
  if(typeof requestKey!=='string'||!requestKey.trim())throw Error('Falta la clave de recuperación.');
  if(seed!==undefined&&(!Number.isSafeInteger(seed)||seed<0||mode!=='generation'))throw Error('La semilla alternativa no es válida.');
  return {request_key:requestKey,segment_index:segment.index,mode,reject_completed:segment.job?.status==='completed',...(seed!==undefined?{seed}:{})};
}
export function alternativeSeed(current,candidate){
  if(!Number.isSafeInteger(candidate)||candidate<0)throw Error('La semilla alternativa no es válida.');
  return candidate===current?(candidate===Number.MAX_SAFE_INTEGER?0:candidate+1):candidate;
}
export function passthroughText(segment){
  const reasons={manual:'Conservado por decisión del usuario.',no_target:'No hay un personaje asignado en este plano; se conserva la fuente.',short:'Plano demasiado corto: se conserva la fuente.'};
  return reasons[segment.passthrough_reason]||segment.passthrough_reason||'Este tramo conserva el vídeo fuente sin generación.';
}
