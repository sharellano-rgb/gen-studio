import {UpscalingButton} from './Upscaling.jsx';
import {characterVersionLabel} from './characterVersion.js';
import {longRetryKey,readLongRetry,longRetryBody,passthroughText,alternativeSeed} from './longRetry.js';
import {openNotesForSegment,categoryLabel} from './qualityNotes.js';
import React,{useEffect,useRef,useState} from 'react';
import SegmentTimeline from './SegmentTimeline.jsx';
import {validTimeline,initialSegment,regenerationImpact,segmentHashIndex} from './segmentTimeline.js';

export default function LongVideoReview({job,onOpen,onUpdated,onCreated,onError,busy,onSeek}) {
  const [data,setData]=useState(null),[sending,setSending]=useState(false),[pendingRetry,setPendingRetry]=useState(null);
  const [feedback,setFeedback]=useState(job.long_review?.human_feedback||'');
  const [selected,setSelected]=useState(null),[showAll,setShowAll]=useState(false),[anchor,setAnchor]=useState(null);
  const segments=validTimeline(data?.segments,data?.frames),notes=job.quality_review?.notes||[];
  const current=segments.find(s=>s.index===selected)||initialSegment(segments,notes);
  const impact=current?.retry_modes?.includes('generation')?regenerationImpact(current,segments):null;
  const canCompare=job.status==='completed'&&job.result_available&&Boolean(onSeek);
  useEffect(()=>{if(current&&selected!==current.index)setSelected(current.index);},[current?.index]);
  useEffect(()=>{
    function follow(){const index=segmentHashIndex(window.location.hash,job.id,data?.segments||[]);if(index!==null){setSelected(index);setAnchor({index});}}
    follow();window.addEventListener('hashchange',follow);return()=>window.removeEventListener('hashchange',follow);
  },[job.id,data?.segments.length]);
  useEffect(()=>{if(!anchor)return;const target=document.getElementById('long-segment-'+job.id+'-'+anchor.index);if(target){target.focus({preventScroll:true});target.scrollIntoView({block:'start',behavior:'auto'});}},[anchor,selected]);
  const request=useRef(null);
  const retryKey=useRef(null);
  useEffect(()=>{
    let active=true;
    setData(null);setPendingRetry(null);
    async function refresh(){try{
      const r=await fetch(`/api/jobs/${job.id}/long-segments`),d=await r.json();
      if(!r.ok)throw Error(d.detail||'No se pudieron consultar los tramos.');
      if(active){setData(d);setPendingRetry(readLongRetry(sessionStorage,d.parent_id));}
    }catch(e){if(active)onError(e.message);}}
    refresh();const timer=setInterval(refresh,3000);
    return ()=>{active=false;clearInterval(timer);};
  },[job.id]);
  async function post(path,body,create=false){
    setSending(true);
    try{const r=await fetch(`/api/jobs/${job.id}/${path}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      const d=await r.json();if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:'No se pudo completar la petición.');
      if(create){request.current=null;onCreated(d);}else onUpdated(d);
      return true;
    }catch(e){onError(e.message);}finally{setSending(false);}
  }
  function assemble(){if(!request.current)request.current=crypto.randomUUID();post('assemble-approved-segments',{request_key:request.current},true);}
  async function retryAssembly(){if(!retryKey.current)retryKey.current=crypto.randomUUID();if(await post('retry-long-assembly',{request_key:retryKey.current}))retryKey.current=null;}
  async function retryShot(segment,mode,newVersion=false){
    const parentId=data?.parent_id||job.id,prior=readLongRetry(sessionStorage,parentId);
    if(sending||(!prior&&busy))return;
    setSending(true);onError('');
    try{
      const seed=!prior&&newVersion?alternativeSeed(segment.job?.parameters.seed,crypto.getRandomValues(new Uint32Array(1))[0]):undefined;
      const body=prior||longRetryBody(segment,mode,crypto.randomUUID(),seed);
      sessionStorage.setItem(longRetryKey(parentId),JSON.stringify(body));setPendingRetry(body);
      const r=await fetch(`/api/jobs/${parentId}/retry-long-segment`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      const d=await r.json();
      if(!r.ok){const error=Error(typeof d.detail==='string'?d.detail:'No se pudo recuperar el tramo.');error.status=r.status;throw error;}
      sessionStorage.removeItem(longRetryKey(parentId));setPendingRetry(null);onCreated(d);
    }catch(e){
      if(e.status>=400&&e.status<500){sessionStorage.removeItem(longRetryKey(parentId));setPendingRetry(null);}
      onError(e.message);
    }finally{setSending(false);}
  }
  const canRetry=job.parameters.operation==='long_character'&&job.status==='failed'&&!job.long_stop_requested&&data?.segments.length>0&&data.segments.every(s=>s.passthrough||(s.job?.status==='completed'&&s.job.result_available));
  return <section className="face-summary long-video-review"><h3>Vídeo completo · Tramos</h3>{job.parameters.engine==='h3_direct'&&<p>{characterVersionLabel(job.parameters)}</p>}
    <p>Elige un tramo para revisar sus marcas, comparar un momento o preparar otra versión.</p>
    <details className="long-review-info"><summary>Cómo se procesa este vídeo</summary><p>Hasta {job.parameters.direct_chunk_frames?60:job.parameters.direct_scene_cuts?30:15} segundos a 24 fps. Revisa la continuidad del personaje y las uniones de los tramos. El audio se conserva en el vídeo completo.</p>
    {job.parameters.direct_chunk_frames&&<p>División automática en tramos de hasta {job.parameters.direct_chunk_frames/24} segundos, con las mismas referencias e instrucciones.</p>}
    {job.parameters.direct_continuity&&<p>Continuidad guiada por el último fotograma visible del tramo anterior. Rehacer un tramo también recalcula los que dependan de él hasta el siguiente corte real.</p>}
    {job.parameters.direct_scene_cuts&&data&&<p>Cortes detectados: {data.cuts.length?data.cuts.map(f=>(f/24).toFixed(2)+' s').join(' · '):'ninguno'}. Cada plano se genera por separado, sin añadir transiciones.</p>}
    </details>
    {data?.variant_parent_id&&<p>Variante {data.variant_revision}: conserva los otros tramos de la toma anterior. <button className="secondary" onClick={()=>onOpen(data.variant_parent_id)}>Abrir toma anterior</button></p>}
    {pendingRetry&&<div role="status"><p>La recuperación del tramo {pendingRetry.segment_index+1} está pendiente de confirmar. Reconecta la misma solicitud para evitar una copia duplicada.</p><button className="secondary" disabled={sending} onClick={()=>retryShot(null,null)}>Reconectar recuperación del tramo</button></div>}
    {canRetry&&<><p>Los tramos están terminados. Puedes recuperar el vídeo completo reintentando sólo su montaje.</p><button className="secondary" disabled={busy||sending||Boolean(pendingRetry)} onClick={retryAssembly}>Reintentar montaje del vídeo completo</button></>}
    {job.parameters.operation==='long_character'&&['queued','preparing','running'].includes(job.status)&&
      <button className="secondary" disabled={sending||job.long_stop_requested} onClick={()=>post('stop-long-video',{})}>
        {job.long_stop_requested?'Detención solicitada':'Detener después del tramo actual'}</button>}
    {!data?.segments.length&&<p>Preparando la distribución del vídeo.</p>}
    {segments.length>0&&<><SegmentTimeline segments={segments} frames={data.frames} selected={current.index} onSelect={setSelected} notes={notes} cuts={data.cuts} videoId={job.parameters.video_id} startSeconds={data.start_seconds||0} affected={impact?.indices||[]}/><button type="button" className="secondary segment-show-all" aria-expanded={showAll} onClick={()=>setShowAll(value=>!value)}>{showAll?'Mostrar sólo el tramo seleccionado':'Mostrar todos los detalles de los tramos'}</button></>}
    {data?.segments.map(s=><article className="history-item" hidden={segments.length>0&&!showAll&&s.index!==current.index} tabIndex={-1} id={'long-segment-'+job.id+'-'+s.index} key={s.index}>
      {job.status==='completed'&&<UpscalingButton jobId={job.id} segmentIndex={s.index} previewStart={s.start/24}/>}
      <div className="segment-detail-heading"><strong>Tramo {s.index+1} · {(s.start/24).toFixed(2)}–{((s.start+s.keep)/24).toFixed(2)} s</strong><span>Tiempo en el resultado</span></div>
      {data.start_seconds>0&&<p>En el original: {(data.start_seconds+s.start/24).toFixed(2)}–{(data.start_seconds+(s.start+s.keep)/24).toFixed(2)} s.</p>}
      {canCompare&&<div className="segment-review-tools"><button type="button" className="secondary" onClick={()=>onSeek(s.start)}>Comparar inicio del tramo {s.index+1}</button>{s.start>0&&<button type="button" className="secondary" onClick={()=>onSeek(s.start-1)}>{data.cuts.includes(s.start)?'Revisar corte':'Revisar unión'} a {(s.start/24).toFixed(2)} s</button>}</div>}
      <p>{s.passthrough?passthroughText(s):s.job?.stage||'Pendiente de generación'}</p>
      {openNotesForSegment(notes,s).map(n=><div className="segment-quality-note" key={n.id}><p>Marca pendiente · {(n.frame/24).toFixed(2)} s · {categoryLabel(n.category)}{n.subject?' · '+n.subject:''}{n.comment?' · '+n.comment:''}</p>{canCompare&&<button type="button" className="secondary" onClick={()=>onSeek(n.frame)}>Comparar marca en {(n.frame/24).toFixed(2)} s</button>}</div>)}
      {s.analysis_status?.error&&<p role="alert" className="error">{s.analysis_status.error}</p>}
      {!s.prompt_analysis&&s.job?.direct_effective_prompt&&<details><summary>Prompt efectivo para H3 · inglés</summary><pre style={{whiteSpace:'pre-wrap'}}>{s.job.direct_effective_prompt}</pre></details>}
      {s.prompt_analysis?.proposal&&<details><summary>Prompt y análisis de este tramo</summary><p>{s.prompt_analysis.proposal.observations}</p><p>{s.job?.direct_effective_prompt?'Prompt efectivo para H3 · inglés':s.prompt_analysis.language?'Prompt preparado para H3 · inglés':'Prompt registrado'}</p><pre style={{whiteSpace:'pre-wrap'}}>{s.job?.direct_effective_prompt||s.prompt_analysis.proposal.prompt}</pre>{s.prompt_analysis.proposal.prompt_original&&<details><summary>Prompt antes de traducir</summary><pre style={{whiteSpace:'pre-wrap'}}>{s.prompt_analysis.proposal.prompt_original}</pre></details>}{(s.prompt_analysis.proposal.warnings||[]).map((w,i)=><p key={i}>{w}</p>)}</details>}
      {s.approved_geometry&&<p>Ajuste geométrico aprobado para este tramo.</p>}
      {s.job?.progress?.max>0&&<p>{s.job.progress.value} / {s.job.progress.max} · Etapa actual</p>}
      {s.job?.error&&<p role="alert" className="error">{s.job.error}</p>}
      {s.job_id&&<button className="secondary" onClick={()=>onOpen(s.job_id)}>Abrir tramo {s.index+1}</button>}
      {s.approved_geometry&&<button className="secondary" onClick={()=>onOpen(s.approved_geometry.id)}>Ver copia ajustada del tramo {s.index+1}</button>}
      {s.retry_modes?.includes('generation')&&(()=>{const scope=regenerationImpact(s,segments);return scope?<p className="segment-impact">Al regenerar {scope.indices.length===1?'se recalcula':'se recalculan'} {scope.label} · {(scope.frames/24).toFixed(2)} s de vídeo.{scope.indices.length>1?' Los siguientes tramos usan la guía del anterior.':''} La versión actual se conserva.</p>:null;})()}
      {(s.retry_modes||[]).length>0&&<div className="history-actions">
        {s.retry_modes.includes('analysis')&&<button className="secondary" disabled={busy||sending||Boolean(pendingRetry)} onClick={()=>retryShot(s,'analysis')}>{s.job?.status==='completed'?'Volver a analizar este tramo en una nueva variante':'Reintentar análisis en una nueva variante'}</button>}
        {s.retry_modes.includes('generation')&&<button className="secondary" disabled={busy||sending||Boolean(pendingRetry)} onClick={()=>retryShot(s,'generation',s.job?.status==='completed')}>{(s.regeneration_indices||[]).length>1?'Regenerar desde este tramo · '+s.regeneration_indices.length+' tramos':s.job?.status==='completed'?'Probar otra versión de este tramo':'Reintentar este tramo en una nueva variante'}</button>}
        {s.retry_modes.includes('generation')&&s.job?.status==='completed'&&<p>{(s.regeneration_indices||[]).length>1?'Se recalcularán este tramo y los siguientes que usan su guía, hasta el próximo corte real. Se conservarán la versión anterior y los tramos independientes.':'Se generará una alternativa sólo para este tramo. Se conservarán los otros tramos y el vídeo anterior.'}</p>}
      </div>}
    </article>)}
    {job.result_available&&<><p>{job.parameters.engine==='h3_direct'?`${job.long_summary?.approved_geometry_segments||0} tramos con ajuste geométrico en esta copia. Abre un tramo, ajusta su proporción si hace falta y aprueba esa copia. Después vuelve aquí para montar otro vídeo completo conservando el original.`:`${job.long_summary?.approved_detail_segments||0} tramos con detalle facial aprobado en esta copia. Para añadir detalle, abre un tramo, genera y aprueba su cabeza H3 y después su composición. Vuelve aquí para crear otra copia del vídeo completo.`}</p>
      <button className="secondary" disabled={busy||sending} onClick={assemble}>Crear copia con tramos aprobados</button>
      <h3>Revisar el vídeo completo</h3><label>Comentario de la revisión<textarea aria-label="Comentario del vídeo largo" maxLength={1000} value={feedback} onChange={e=>setFeedback(e.target.value)}/></label>
      <div className="history-actions"><button className="secondary" disabled={sending} onClick={()=>post('review-long-video',{approved:true,feedback})}>Aprobar vídeo completo</button>
        <button className="secondary" disabled={sending} onClick={()=>post('review-long-video',{approved:false,feedback})}>Marcar incidencia del vídeo</button></div>
      <p role="status">{job.long_review?.identity_approved?'Vídeo completo aprobado.':job.long_review?.human_review==='rejected'?'Incidencia registrada.':'Vídeo completo pendiente de tu revisión.'}</p>
    </>}
  </section>;
}
import {workspaceSessionStorage as sessionStorage} from './workspaceStorage.js';
