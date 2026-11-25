export const H3_MAX_FRAMES=107;
export const H3_SEGMENTED_MAX_FRAMES=720;
export function h3FrameLimit(method){return method==='h3_multiview'?H3_MAX_FRAMES:method==='viggle'?360:H3_SEGMENTED_MAX_FRAMES;}
export function motionVideoDuration(video,method){
  const available=video&&Number.isFinite(video.duration)?Math.max(5,Math.ceil(video.duration*24)):73;
  const selectedMethod=method==='h3_multiview'&&available>H3_MAX_FRAMES?'h3_segmented':method;
  return {method:selectedMethod,frames:Math.min(available,h3FrameLimit(selectedMethod))};
}
export function motionMethodForJob(job,savedMethod){
  if(job?.parameters?.transfer_method==='h3_multiview')return job.parameters.motion_h3_segmented?'h3_segmented':'h3_multiview';
  if(job?.parameters?.transfer_method==='viggle'||(job?.parameters?.engine==='viggle_motion'&&job?.parameters?.operation==='motion_transfer'&&!job.parameters.transfer_method))return 'viggle';
  return ['h3_multiview','h3_segmented'].includes(savedMethod)?savedMethod:'viggle';
}
export function validateH3CutFrames(cuts,frames){
  if(!Array.isArray(cuts)||!Number.isInteger(frames)||frames<5||frames>H3_SEGMENTED_MAX_FRAMES)throw Error('La toma y sus cortes deben indicar fotogramas válidos.');
  if(cuts.length>11)throw Error('Indica como máximo 11 cambios de plano.');
  if(Array.from(cuts).some(n=>!Number.isInteger(n)||n<1||n>=frames))throw Error('Cada corte debe ser un fotograma entero entre 1 y '+(frames-1)+'.');
  if(new Set(cuts).size!==cuts.length)throw Error('No repitas fotogramas de corte.');
  return [...cuts].sort((a,b)=>a-b);
}
export function parseH3CutFrames(text,frames){
  if(typeof text!=='string')throw Error('Escribe los fotogramas separados por comas.');
  const tokens=text.trim()?text.split(',').map(s=>s.trim()):[];
  if(tokens.some(s=>!/^\d+$/.test(s)))throw Error('Escribe fotogramas enteros separados por comas, por ejemplo: 48, 120.');
  return validateH3CutFrames(tokens.map(Number),frames);
}
export function canStopH3Segments(job){
  return job?.parameters?.motion_h3_segmented===true&&!job.parameters.motion_h3_source_job&&['queued','preparing','submitting','running','downloading'].includes(job.status);
}
const bindings=['video_id','source_sha256','frames','start_seconds','resolution','scene_mode','anchor_editor','qwen_recipe'];
export function compatibleView(primary,other){
  if(primary?.parameters?.original_reference||other?.parameters?.original_reference)return false;
  if(!primary||!other||other.parameters?.operation!=='motion_anchor'||other.id===primary.id||other.parameters.anchor_index===primary.parameters.anchor_index)return false;
  const shared=bindings.every(k=>(primary.parameters[k]??(k==='scene_mode'?'character':k==='anchor_editor'?'klein':k==='qwen_recipe'?'legacy':null))===(other.parameters[k]??(k==='scene_mode'?'character':k==='anchor_editor'?'klein':k==='qwen_recipe'?'legacy':null)));
  const a=primary.parameters,b=other.parameters,sameReference=(a.reference_id??null)===(b.reference_id??null)&&(a.reference_sha256??null)===(b.reference_sha256??null);
  const inheritedStyle=a.scene_mode==='restyle'&&Boolean(primary.motion_anchor_sha256)&&b.reference_sha256===primary.motion_anchor_sha256;
  return shared&&(sameReference||inheritedStyle);
}
export function h3PresetForJob(job,saved){
  const preset=job?job.parameters?.motion_h3_preset:saved;
  return preset==='turbo8'?'turbo8':'base';
}
export function h3Payload(primary,guide,{key,offset,strength,timeline='',segmented=false,cutFrames=[],vocal=false,preset='base'}){
  if(!['base','turbo8'].includes(preset))throw Error('Selecciona H3 base o Turbo 8-step.');
  if(preset==='turbo8'&&vocal)throw Error('La interpretación vocal requiere H3 base de 20 pasos.');
  const cuts=segmented?validateH3CutFrames(cutFrames,primary.parameters?.frames??H3_SEGMENTED_MAX_FRAMES):[];
  return {request_key:key,approved_anchor_sha256:primary.motion_anchor_sha256,
    additional_views:guide?[{job_id:guide.id,sha256:guide.motion_anchor_sha256}]:[],timing_offset_frames:offset,lora_strength:vocal?0:strength,motion_timing_notes:timeline,...(preset==='turbo8'?{preset}:{}),...(vocal?{vocal_performance:true}:{}),...(segmented?{segmented:true,...(cuts.length?{cut_frames:cuts}:{})}:{})};
}
