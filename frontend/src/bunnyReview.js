// Bunny prepares source intervals on a 24 fps timeline using Python round().
export function bunnySourceStart(seconds=0) {
  const value=Number(seconds)*24;
  if(!Number.isFinite(value)||value<0)return 0;
  const lower=Math.floor(value),fraction=value-lower;
  return (fraction===0.5?(lower%2?lower+1:lower):Math.round(value))/24;
}

// Boundaries come from completed segment records, never from an assumed 5 s plan.
export function bunnyBoundaries(job) {
  if(job?.parameters?.operation!=='motion_bunny'||job.status!=='completed'||!job.result_available)return [];
  const segments=job.bunny_segments,frames=job.parameters.frames;
  if(!Array.isArray(segments)||segments.length<2||!Number.isInteger(frames)||frames<1)return [];
  if(segments.some((segment,index)=>segment.index!==index||!Number.isInteger(segment.frames)||segment.frames<1))return [];
  if(segments.reduce((sum,segment)=>sum+segment.frames,0)!==frames)return [];
  let cursor=0;
  return segments.slice(0,-1).map(segment=>(cursor+=segment.frames));
}
