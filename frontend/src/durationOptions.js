export const MAX_FRAMES=360;
export function limitDuration(params) {
  return params&&params.frames>MAX_FRAMES?{...params,frames:MAX_FRAMES}:params;
}
export function durationOptions(video,current=107,maxFrames=MAX_FRAMES) {
  const available=video&&Number.isFinite(video.duration)?(maxFrames>MAX_FRAMES?Math.ceil(video.duration*24):Math.round(video.duration*24)):null;
  const full=available===null?null:Math.min(maxFrames,available);
  const values=[73,90,107,124,240,360];
  if(maxFrames>MAX_FRAMES)for(const frames of [480,600,720,960,1200,1440,maxFrames])if(frames<=maxFrames&&!values.includes(frames))values.push(frames);
  if(full>=3&&!values.includes(full))values.push(full);
  if(Number.isInteger(current)&&current>=3&&current<=maxFrames&&!values.includes(current))values.push(current);
  return values.sort((a,b)=>a-b).map(frames=>({frames,disabled:available!==null&&frames>available+1,
    label:(frames===full?(available>maxFrames?`Primeros ${maxFrames/24} s · `:'Vídeo completo · '):'')+`${(frames/24).toFixed(2)} s · ${frames} frames`}));
}
