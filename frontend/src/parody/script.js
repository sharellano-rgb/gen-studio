export const emptyScript=()=>({version:1,lines:[]});
export function newLine(shot,characterId,id=crypto.randomUUID()){
 return {id,shot_id:shot.id,character_id:characterId||null,kind:'speech',start_ms:shot.start_ms,end_ms:shot.end_ms,text:'',acting:'',language:'Spanish',seed:127,pause_before_ms:0,pause_after_ms:0,
 voice:{mode:'design',description:'Voz adulta, clara y conversacional, tono natural. Personaje ficticio.',reference_id:null,reference_start_ms:0,reference_end_ms:5000,transcript:'',authorized:false,permission_note:''}};
}
export function previewBusy(record){return !!record&&(['running','cancelling'].includes(record.status)||(['review_pending','cancelled','failed'].includes(record.status)&&!record.resources_released));}
export function removeShotLines(script,id){return {...script,lines:script.lines.filter(l=>l.shot_id!==id)};}
