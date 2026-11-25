import React,{useEffect,useState} from 'react';
import {activityClock,activityView,durationLabel,isActiveJob,mergeActivity} from './jobActivity.js';
import './jobActivity.css';

export default function JobElapsed({job}){
  const [reading,setReading]=useState(null),[tick,setTick]=useState(()=>performance.now());
  const propActive=isActiveJob(job);
  useEffect(()=>{
    if(!job?.id)return;
    let live=true,timer,request;
    setReading(null);
    async function poll(){
      request=new AbortController();
      const timeout=setTimeout(()=>request.abort(),10000);
      let completed=false;
      try{
        const response=await fetch('/api/jobs/'+encodeURIComponent(job.id)+'/activity',{signal:request.signal,cache:'no-store'});
        if(!response.ok)throw Error('No se pudo consultar el trabajo.');
        const snapshot=await response.json();
        if(snapshot.job_id!==job.id||!Number.isFinite(Date.parse(snapshot.observed_at)))throw Error('Estado incompleto.');
        if(!live)return;
        const received=performance.now();
        setTick(received);
        setReading({jobId:job.id,snapshot,anchor:{serverMs:Date.parse(snapshot.observed_at),tick:received},error:false});
        completed=!isActiveJob(snapshot);
      }catch{
        if(live)setReading(previous=>({...previous,jobId:job.id,error:true}));
      }finally{
        clearTimeout(timeout);
        if(live&&propActive&&!completed)timer=setTimeout(poll,5000);
      }
    }
    poll();
    return()=>{live=false;clearTimeout(timer);request?.abort();};
  },[job?.id,propActive]);
  const data=reading?.jobId===job?.id?reading:null;
  const current=job?mergeActivity(job,data?.snapshot):null;
  const active=isActiveJob(current);
  useEffect(()=>{
    if(!active)return;
    const interval=setInterval(()=>setTick(performance.now()),1000);
    return()=>clearInterval(interval);
  },[active,job?.id]);
  if(!current)return null;
  const age=data?.anchor?Math.max(0,(tick-data.anchor.tick)/1000):0;
  const fresh=Boolean(data?.anchor&&!data.error&&age<20);
  // Freeze uncertain durations at the last server observation if the connection fails.
  const now=activityClock(data?.anchor,tick);
  const timingNow=fresh?now:data?.anchor?.serverMs;
  const unavailable=Boolean(data?.error||(data?.anchor&&!fresh));
  const view=activityView(current,now,{fresh,snapshotAge:fresh?age:0,timingNow});
  const timing=Boolean(view.own&&(view.own.started_at||view.own.complete_timing||view.active));
  const partial=timing&&!view.complete;
  return <section className="job-activity" aria-label="Actividad del trabajo">
    {view.active&&<p className="job-activity-stage" role="status">{view.stage}</p>}
    {timing?<dl className="job-activity-metrics">
      {(view.source||!view.grouped)&&<>
        <div><dt>{view.source?'En cola · tramo':'En cola'}</dt><dd>{durationLabel(view.queued)}</dd></div>
        <div><dt>{view.source?'Procesamiento · tramo':'Procesamiento'}</dt><dd>{durationLabel(view.processing)}</dd></div>
      </>}
      {(view.source||view.grouped)&&<div><dt>Tiempo del trabajo</dt><dd>{durationLabel(view.total)}</dd></div>}
      {view.active&&fresh&&view.phase==='processing'&&!view.unreachable&&view.stageAge!==null&&<div><dt>Desde el cambio de etapa</dt><dd>{durationLabel(view.stageAge)}</dd></div>}
    </dl>:<p className="job-activity-note">{view.active?'Esperando el primer registro de actividad.':'Este trabajo no tiene tiempos de proceso registrados.'}</p>}
    {partial&&<p className="job-activity-note">Medición parcial: sólo se muestran los tiempos registrados.</p>}
    {view.active&&view.step&&<div className="job-activity-step">
      <label>Avance de esta etapa: {view.step.value} / {view.step.max}</label>
      <progress aria-label="Avance de esta etapa" value={view.step.value} max={view.step.max}/>
    </div>}
    {view.active&&<div className="job-activity-signals">
      {view.advanceAge!==null&&<p>Último avance confirmado: hace {durationLabel(view.advanceAge)}.</p>}
      {view.contactAge!==null&&<p>Última respuesta del motor: hace {durationLabel(view.contactAge)}.</p>}
      {view.estimate?<p className="job-activity-estimate">Restante aproximado{current.estimate.scope==='segment'?' del tramo actual':''}: {view.estimate}. Basado en {current.estimate.sample_count} generaciones comparables.</p>:<p>Tiempo restante aún sin estimación.</p>}
    </div>}
    <div>
      {unavailable&&<p className="job-activity-notice">No se pudo actualizar el estado. Se muestra el último registro y se volverá a consultar automáticamente.</p>}
      {!unavailable&&view.unreachable&&<p className="job-activity-notice">El motor no está respondiendo. Gen Studio está intentando recuperar su estado.</p>}
      {!unavailable&&!view.unreachable&&view.quiet&&<p className="job-activity-notice">Esta etapa no ha informado nuevos avances durante {durationLabel(view.silence)}. Puede seguir trabajando; este aviso no confirma que se haya detenido.</p>}
    </div>
    {view.active&&<p className="job-activity-note">Puedes cerrar esta página y volver al historial.</p>}
  </section>;
}
