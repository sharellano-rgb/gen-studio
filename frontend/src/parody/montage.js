export function montageBusy(value){return ['running','cancelling'].includes(value?.status);}
export function moveSegment(rows,index,delta){const target=index+delta;if(target<0||target>=rows.length)return rows;const next=[...rows];[next[index],next[target]]=[next[target],next[index]];return next;}
export function segmentFrames(rows){return rows.reduce((n,row)=>n+Math.max(0,Number(row.end_frame)-Number(row.start_frame)),0);}

export function originalFrames(shot){return Math.max(0,Math.floor((shot.end_ms-shot.start_ms)*24/1000));}
export function originalSegment(shot,resolution,projectId){
 if(!shot||originalFrames(shot)<1||!/^\d{2,4}x\d{2,4}$/.test(resolution))throw Error('Selecciona un plano y una resoluciÃ³n.');
 return {source_shot_id:shot.id,...(projectId?{source_project_id:projectId}:{}),start_frame:0,end_frame:Math.min(120,originalFrames(shot)),resolution,fit:'contain',audio:'source',lipsync:'none'};
}
export function reframePatch(enabled,width,height){
 if(!enabled)return {fit:'contain',reframe:null};
 if(!Number.isInteger(width)||!Number.isInteger(height)||width<16||height<16)throw Error('Espera a que cargue el vídeo original.');
 return {fit:'reframe',reframe:{x:0,y:0,width,height,source_width:width,source_height:height}};
}
export function validReframe(c){return !!c&&['x','y','width','height','source_width','source_height'].every(k=>Number.isInteger(c[k]))&&c.x>=0&&c.y>=0&&c.width>=16&&c.height>=16&&c.x+c.width<=c.source_width&&c.y+c.height<=c.source_height;}
