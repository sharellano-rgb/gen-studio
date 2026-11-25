export function hasApprovedMotion(job){
  return job?.parameters?.engine==='viggle_motion'&&job.parameters.operation==='motion_transfer'&&job.status==='completed'&&job.result_available&&job.motion_review?.approved===true;
}
export function hasReviewedH3(job){
  return job?.parameters?.transfer_method==='h3_multiview'&&job.status==='completed'&&job.result_available&&job.motion_h3_review?.approved===true;
}
export function resultUrl(job,{download=false,original=false}={}){
  const query=new URLSearchParams();
  if(original)query.set('variant','original');
  else if(hasApprovedMotion(job))query.set('variant','motion-approved');
  if(download)query.set('download','true');
  return '/api/jobs/'+job.id+'/result'+(query.size?'?'+query.toString():'');
}
