const TERMINAL=new Set(['completed','failed','cancelled','submission_unknown']);
export const isActiveJob=job=>Boolean(job&&!TERMINAL.has(job.status));
const timestamp=value=>typeof value==='string'&&Number.isFinite(Date.parse(value))?Date.parse(value):null;
const nonnegative=value=>Number.isFinite(value)?Math.max(0,value):0;
export function activityClock(anchor,tick){
  if(!anchor||!Number.isFinite(anchor.serverMs)||!Number.isFinite(tick))return null;
  return anchor.serverMs+Math.max(0,tick-anchor.tick);
}
export function mergeActivity(job,snapshot){
  if(!snapshot||snapshot.job_id!==job.id)return job;
  if(!isActiveJob(job)&&isActiveJob(snapshot))return job;
  if(timestamp(job.updated_at)>timestamp(snapshot.updated_at))return job;
  return {...job,...snapshot,id:job.id};
}
export function elapsedSeconds(activity,phase,now,active=true){
  let elapsed=nonnegative(activity?.[phase+'_seconds']);
  const since=timestamp(activity?.phase_since);
  if(active&&activity?.phase===phase&&since!==null&&Number.isFinite(now))
    elapsed+=Math.max(0,(now-since)/1000);
  return elapsed;
}
export function durationLabel(seconds){
  const s=Math.floor(nonnegative(seconds)),minutes=Math.floor(s/60);
  if(minutes>=60)return Math.floor(minutes/60)+' h '+minutes%60+' min';
  return minutes+' min '+s%60+' s';
}
export function ageSeconds(value,now){
  const at=timestamp(value);
  return at!==null&&Number.isFinite(now)?Math.max(0,(now-at)/1000):null;
}
export function remainingLabel(estimate,secondsSinceSnapshot=0){
  if(!estimate||!Number.isFinite(estimate.lower_seconds)||!Number.isFinite(estimate.upper_seconds)
    ||estimate.sample_count<3||!['job','segment'].includes(estimate.scope))return null;
  const lower=Math.max(0,estimate.lower_seconds-nonnegative(secondsSinceSnapshot));
  const upper=estimate.upper_seconds-nonnegative(secondsSinceSnapshot);
  if(upper<=0)return null;
  if(upper<60)return 'menos de 1 min';
  const low=Math.floor(lower/60),high=Math.ceil(upper/60);
  return low===0?'hasta '+high+' min':low===high?'unos '+high+' min':low+'–'+high+' min';
}
export function activityView(job,now,{fresh=true,snapshotAge=0,timingNow=now}={}){
  const active=isActiveJob(job),own=job.activity?.version===1?job.activity:null;
  const source=active?job.progress_source:null;
  const activity=source?.activity?.version===1?source.activity:own;
  const grouped=['long_character','ninja_shots_prepare','ninja_shots_render','motion_hybrid','motion_layers'].includes(job.parameters?.operation);
  const end=active?now:timestamp(activity?.finished_at);
  const advanceAge=active?ageSeconds(activity?.last_advance_at,end):null;
  const stageAge=active?ageSeconds(activity?.stage_started_at,end):null;
  const silence=advanceAge??stageAge;
  const step=activity&&Number.isFinite(activity.progress_value)&&Number.isFinite(activity.progress_max)
    &&activity.progress_max>0&&activity.progress_value>=0&&activity.progress_value<=activity.progress_max
    ?{value:activity.progress_value,max:activity.progress_max}:null;
  const complete=Boolean(own?.complete_timing&&(!source||activity?.complete_timing));
  const prefix=source&&Number.isInteger(source.segment_index)?'Tramo '+(source.segment_index+1)+' · ':'';
  const stateLabel=source?.stage||job.stage||activity?.stage_label||'Preparando trabajo';
  const cancelling=/cancel|detendr|deteni|detenc/i.test(stateLabel);
  const stage=active&&!cancelling&&activity?.phase==='queued'?(job.generation_wait_until?stateLabel:'En cola; esperando turno'):
    active&&!cancelling&&activity?.engine_state==='unreachable'?'Recuperando la conexión con el motor':stateLabel;
  const estimate=active&&fresh&&activity?.phase==='processing'&&activity.engine_state!=='unreachable'
    ?remainingLabel(job.estimate,snapshotAge):null;
  return {active,activity,own,source,grouped,complete,step,
    stage:prefix+stage,
    phase:activity?.phase,
    queued:activity?elapsedSeconds(activity,'queued',timingNow,active):null,
    processing:activity?elapsedSeconds(activity,'processing',timingNow,active):null,
    total:own?elapsedSeconds(own,'queued',timingNow,active)+elapsedSeconds(own,'processing',timingNow,active):null,
    stageAge,advanceAge,contactAge:active?ageSeconds(activity?.last_engine_contact_at,end):null,
    unreachable:active&&activity?.engine_state==='unreachable',
    quiet:active&&fresh&&activity?.phase==='processing'&&silence!==null&&silence>=300,
    silence,estimate};
}
