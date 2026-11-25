export function compatibleSceneTakes(plans,appearance){
 return plans.filter(r=>['parody-h3-shot-1','parody-h3-speech-1'].includes(r.plan?.version)&&r.plan.appearance?.id===appearance&&
  (r.plan.version==='parody-h3-shot-1'?r.status==='completed'&&r.job?.result_available:['completed','review_pending'].includes(r.status)&&r.artifact));
}
