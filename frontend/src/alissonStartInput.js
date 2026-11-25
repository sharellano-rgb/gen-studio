// Keep typing separate from the frame-aligned value sent to H3.
export function formatAlissonStart(seconds){return Number.isFinite(seconds)?String(Number(seconds.toFixed(3))):'';}
export function parseAlissonStart(text){
  if(typeof text!=='string')return null;
  const value=text.trim();
  if(!/^(?:\d+(?:[.,]\d*)?|[.,]\d+)$/.test(value))return null;
  const seconds=Number(value.replace(',','.'));
  if(!Number.isFinite(seconds)||seconds<0||seconds>3600)return null;
  return Math.round(seconds*24)/24;
}
export function alissonStartCandidate(form,text){
  const start=parseAlissonStart(text);
  return start===null?null:{...form,start_seconds:start};
}
