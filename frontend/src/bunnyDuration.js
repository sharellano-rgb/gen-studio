export const BUNNY_MAX_FRAMES=1440;
export const BUNNY_CHUNK_FRAMES=120;
export function bunnyDurations(video,current=120){
 const full=Math.round((video?.duration||0)*24);
 return [...new Set([48,120,240,360,480,720,1080,1440,Number(current),...(full<=BUNNY_MAX_FRAMES?[full]:[])])]
  .filter(n=>Number.isInteger(n)&&n>=5&&n<=BUNNY_MAX_FRAMES).sort((a,b)=>a-b);
}
export function bunnyIntervalValid(video,frames,start){
 const n=Number(frames),s=Number(start);
 return Boolean(video&&String(start).trim()!==''&&Number.isFinite(s)&&s>=0&&Number.isInteger(n)&&n>=5&&n<=BUNNY_MAX_FRAMES&&Math.round(s*24)+n<=Math.round(video.duration*24));
}
export function bunnySelectDuration(form,video,frames){
 const n=Number(frames);
 return {...form,frames:n,...(video&&n===Math.round(video.duration*24)?{start_seconds:'0'}:{})};
}

export function bunnyFormForJob(job){
 const p=job?.parameters;
 if(p?.operation!=='motion_bunny')return null;
 return {video_id:p.video_id,reference_id:p.reference_id,environment_reference_id:p.environment_reference_id||'',frames:p.frames,start_seconds:String(p.start_seconds||0),quality:p.quality,scene_mode:p.scene_mode,direction:p.direction||'',seed:p.seed};
}
