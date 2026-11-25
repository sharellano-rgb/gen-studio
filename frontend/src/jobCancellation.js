const activeStatuses=new Set(['queued','preparing','submitting','running','refining','downloading','cancelling']);
export function cancellationPending(job){
  return Boolean(job&&activeStatuses.has(job.status)&&(job.status==='cancelling'||job.cancel_requested===true));
}
export function cancellationControl(job,requesting=false){
  const supported=job?.parameters?.engine==='tessera'||Boolean(job?.video_sequence_version||job?.qwen_director_version)||['h3_alisson','h3_mdmz','h3_secourses'].includes(job?.parameters?.engine)||job?.parameters?.engine==='h3_direct'&&job.parameters.operation==='upscale_1080'||['motion_hybrid','motion_anchor','motion_bunny','motion_layers','image_generation'].includes(job?.parameters?.operation)||job?.status==='queued';
  const visible=Boolean(supported&&!job?.parameters?.hybrid_parent_id&&activeStatuses.has(job?.status));
  const pending=cancellationPending(job);
  return {visible,disabled:!visible||requesting||pending,
    label:requesting||pending?'Cancelando y liberando…':job?.status==='queued'?'Quitar de la cola':'Cancelar trabajo y liberar memoria'};
}
