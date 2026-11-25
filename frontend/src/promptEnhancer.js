export const REFERENCE_ROLES=['Identidad del personaje','Identidad facial','Vestuario y cuerpo','Identidad y apariencia','Otro ángulo del rostro','Hoja de personaje'];
export function enhancementKey(request){
  return JSON.stringify([request.video_id,request.reference_ids,request.frames,request.start_seconds,request.intent,request.reference_roles,request.reference_type||'auto']);
}
export function proposalMatches(analysis,request){return Boolean(analysis?.request&&enhancementKey(analysis.request)===enhancementKey(request));}
export function validProposalPrompt(prompt,count){
  const pictures=[...prompt.matchAll(/<Picture (\d+)>/g)].map(m=>Number(m[1]));
  const videos=[...prompt.matchAll(/<Video (\d+)>/g)].map(m=>Number(m[1]));
  return prompt.trim().length>=10&&prompt.length<=4000&&videos.length>0&&videos.every(n=>n===1)&&
    new Set(pictures).size===count&&pictures.every(n=>n>=1&&n<=count);
}

