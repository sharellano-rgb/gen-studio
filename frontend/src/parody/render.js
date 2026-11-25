export function isNativePlan(plan){return plan?.plan?.version==='parody-pika-ref2va-1';}
export function isSpeechPlan(plan){return plan?.plan?.version==='parody-h3-speech-1'||isNativePlan(plan);}
export function ref2vaRequest(project,selection,scene,sceneReviewed,voice,voiceReviewed,directions){
 const chosen=renderSelection(project,selection);
 if(!chosen.shot||!chosen.references.length||!scene||!sceneReviewed||!voice||!voiceReviewed)throw Error('Revisa la escena, la ficha y la voz antes de preparar Ref2VA.');
 const duration=Math.min(2000,chosen.shot.end_ms-chosen.shot.start_ms);
 if(duration<1000)throw Error('Este piloto requiere un plano de referencia de al menos un segundo.');
 return {expected_revision:project.revision,appearance_id:selection,reference_ids:[chosen.references[0].id],start_ms:chosen.shot.start_ms,duration_ms:duration,
   quality:'768p',preset:'hands',clothing:'reference',performance:'new',seed:314159,engine:'pika_ref2va_native',
   scene_asset_id:scene,scene_reviewed:true,voice_preview_id:voice,voice_reviewed:true,directions:directions||''};
}
export function renderBusy(plan){return isSpeechPlan(plan)?['running','cancelling'].includes(plan.status):Boolean(plan?.native_job_id&&!['completed','failed','cancelled','submission_unknown'].includes(plan.status));}
export function eligibleSpeechVoices(project,selection,voices){
 const selected=renderSelection(project,selection);
 return voices.filter(v=>v.status==='review_pending'&&!v.stale&&v.artifact&&v.binding?.line?.kind==='speech'&&v.binding.line.shot_id===selected.shot?.id&&v.binding.line.character_id===selected.character?.id);
}
export function speechRequest(base,voice,reviewed,directions){
 if(!voice||!reviewed)throw new Error('Selecciona y escucha una voz preparada.');
 if(base.reference_ids.length!==1||base.duration_ms>4000||base.preset!=='hands')throw new Error('H3 con voz requiere una referencia, Realismo Manos y hasta cuatro segundos.');
 return {...base,performance:'new',voice_preview_id:voice,voice_reviewed:true,directions:directions||''};
}
export function renderProgress(progress){return Number.isFinite(progress?.value)&&Number.isFinite(progress?.max)&&progress.max>0?Math.max(0,Math.min(100,Math.round(progress.value/progress.max*100))):null;}
export function renderSelection(project,id){
 const appearance=project.cast?.appearances.find(a=>a.id===id),shot=project.draft.shots.find(s=>s.id===appearance?.shot_id),character=project.cast?.characters.find(c=>c.id===appearance?.character_id);
 return {appearance,shot,character,references:(character?.references||[]).filter(r=>['front','profile','body','wardrobe','accessory'].includes(r.role))};
}
