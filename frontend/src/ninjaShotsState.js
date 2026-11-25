const operations=new Set(['ninja_shots_plan','ninja_shots_prepare','ninja_shots_render']);
export const isNinjaShots=job=>job?.parameters?.engine==='h3_ninja'&&operations.has(job.parameters.operation);
export const ninjaShotsLabel=job=>({ninja_shots_plan:'Refinamiento Facial · Planos detectados',ninja_shots_prepare:'Refinamiento Facial · Máscaras por plano',ninja_shots_render:'Refinamiento Facial · Vídeo por planos'}[job?.parameters?.operation]||'Refinamiento Facial por planos');
export const ninjaShotLabel=(s,start=0)=>`Plano ${(s.shot??s.index)+1}${s.shot!==undefined?` · Tramo ${s.index+1} · ${s.starts_at_cut?'Cambio de plano':s.context_frames?'Continuidad':'Inicio'}`:''} · ${((start+s.start)/24).toFixed(2)}–${((start+s.start+s.keep)/24).toFixed(2)} s`;
export function validInterval(form,assets){
  const video=assets.find(a=>a.id===form.video_id&&a.kind==='video'),reference=assets.find(a=>a.id===form.reference_id&&a.kind==='image');
  return Boolean(video&&reference&&Number.isFinite(form.start_seconds)&&form.start_seconds>=0&&form.start_seconds<=3600&&
    Number.isInteger(form.frames)&&form.frames>=5&&form.frames<=1440&&Math.round(form.start_seconds*24)+form.frames<=Math.ceil(Number(video.duration)*24)&&
    Number.isSafeInteger(form.seed)&&form.seed>=0);
}
function validSegments(segments){
  if(!Array.isArray(segments)||!segments.length)return false;
  let next=0;
  for(let i=0;i<segments.length;i++){const s=segments[i];if(s.index!==i||s.start!==next||!Number.isInteger(s.keep)||s.keep<1)return false;next+=s.keep;}
  return next<=1440;
}
export const defaultSelections=segments=>validSegments(segments)?segments.map(s=>({index:s.index,passthrough:s.keep<5,x:null,y:null})):[];
export function canPrepare(segments,selections){
  return validSegments(segments)&&selections.length===segments.length&&selections.some(s=>!s.passthrough)&&selections.every((s,i)=>
    s.index===i&&typeof s.passthrough==='boolean'&&(s.passthrough||(segments[i].keep>=5&&[s.x,s.y].every(v=>typeof v==='number'&&Number.isFinite(v)&&v>=0&&v<=1))));
}
export function maskApproval(s){
  const m=s?.mask_job;
  return s?.passthrough===false&&m?.status==='completed'&&m.ninja_preview_available&&/^[a-f0-9]{64}$/.test(m.ninja_mask_sha256)?
    {index:s.index,preparation_id:m.id,mask_sha256:m.ninja_mask_sha256}:null;
}
export const maskApprovalKey=s=>{const a=maskApproval(s);return a?`${a.index}:${a.preparation_id}:${a.mask_sha256}`:null;};
export function canRender(segments,approvals){
  if(!validSegments(segments)||segments.some(s=>typeof s.passthrough!=='boolean'))return false;
  const edited=segments.filter(s=>!s.passthrough);
  return edited.length>0&&edited.length===approvals.length&&edited.every((s,i)=>{const a=maskApproval(s),b=approvals[i];
    return a&&b&&a.index===b.index&&a.preparation_id===b.preparation_id&&a.mask_sha256===b.mask_sha256;});
}
