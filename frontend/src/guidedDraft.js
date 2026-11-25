export const guidedDefaults={seed:42,steps:20,lora_strength:1,control_strength:1.1,
  direction:'Preserve the source headwear, clothing, lighting and background. Bare cheek, jaw and ear with continuous natural skin texture.',
  nsfw_h3_motion_booster:false,nsfw_penis_v2:false,nsfw_hm_pussy:false,nsfw_minimax_unlocked:false,nsfw_strength:1};

export function guidedParams(value={}) {
  const result={...guidedDefaults};
  for(const [key,min,max,integer] of [['seed',0,9007199254740991,true],['steps',8,30,true],['lora_strength',0,1.5,false],['control_strength',.5,1.5,false]]) {
    const number=value?.[key];
    if(typeof number==='number'&&Number.isFinite(number)&&number>=min&&number<=max&&(!integer||Number.isInteger(number)))result[key]=number;
  }
  if(typeof value?.direction==='string'&&value.direction.length<=1000)result.direction=value.direction;
  for(const key of ['nsfw_h3_motion_booster','nsfw_penis_v2','nsfw_hm_pussy','nsfw_minimax_unlocked'])if(value?.[key]===true)result[key]=true;
  const strength=value?.nsfw_strength;
  if(typeof strength==='number'&&Number.isFinite(strength)&&strength>=0&&strength<=2)result.nsfw_strength=strength;
  return result;
}

export function readGuidedDraft(storage,jobId) {
  try {
    const draft=JSON.parse(storage.getItem(`gen-guided-draft:${jobId}`));
    return draft&&draft.version===1&&typeof draft.source==='string'?{...draft,params:guidedParams(draft.params)}:null;
  } catch{return null;}
}

export function restoredRegion(draft,source) {
  const region=draft?.region;
  if(!source||draft?.source!==source.id||draft.width!==source.width||draft.height!==source.height||!Array.isArray(region)||region.length!==3)return null;
  const [x,y,size]=region;
  return region.every(Number.isInteger)&&x>=0&&y>=0&&size>=32&&x+size<=source.width&&y+size<=source.height?[...region]:null;
}

export function saveGuidedDraft(storage,jobId,draft) {
  try {storage.setItem(`gen-guided-draft:${jobId}`,JSON.stringify({version:1,...draft}));return true;}catch{return false;}
}

export function flowState(job,jobs=[]) {
  const operation=job?.parameters?.operation;
  const composition=operation==='identity_anchor'&&job.parameters.anchor_method==='guided_composition';
  if(operation&&!composition&&operation!=='guided_head')return null;
  if(composition)return {stage:3,complete:job.status==='completed'&&!!job.face_review?.identity_approved};
  if(operation==='guided_head')return {stage:job.status==='completed'&&job.result_available?(job.head_review?.identity_approved?3:2):1,complete:false};
  if(job?.result_available&&job.id) {
    const detail=jobs.find(item=>item.parameters?.operation==='guided_head'&&item.parameters.head_parent_id===job.id);
    if(detail)return {...flowState(detail),detailJobId:detail.id};
  }
  return {stage:job?.result_available?1:0,complete:false};
}

export function reviewText(review) {
  const feedback=review?.human_feedback;
  return typeof feedback==='string'?feedback:typeof feedback?.statement==='string'?feedback.statement:'';
}
