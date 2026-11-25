export function timingModePatch(mode,width,height){
 if(mode==='none')return {timing:null};
 if(mode==='manual')return {timing:{mode:'manual',delay_samples:0,region:null,target_confirmed:false},lipsync:'none',region:null};
 if(mode!=='auto'||width<128||height<128)throw Error('El plano no tiene una región válida para medir.');
 return {timing:{mode:'auto',delay_samples:0,region:{x:0,y:0,width:Math.min(640,width),height:Math.min(640,height)},target_confirmed:false},lipsync:'none',region:null};
}
export function timingDelaySamples(value){
 if(String(value).trim()==='')return null;
 const ms=Number(value);
 return Number.isFinite(ms)&&Math.abs(ms)<=1000?Math.round(ms*48):null;
}
export function resetTimingConfirmation(row,patch){
 if(Object.hasOwn(patch,'timing'))return patch;
 if(row.timing?.mode==='auto'&&['start_frame','end_frame','voice_preview_ids'].some(k=>Object.hasOwn(patch,k)))
  return {...patch,timing:{...row.timing,target_confirmed:false}};
 return patch;
}
export function timingLabel(value){
 if(!value)return 'tiempos del guion';
 return value.mode==='auto'?'ajuste automático con revisión':`ajuste manual ${Math.round(value.delay_samples/48*1000)/1000} ms`;
}
