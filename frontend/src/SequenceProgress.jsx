import SegmentAlerts from './SegmentAlerts.jsx';
import SegmentBackgroundReview, {backgroundVersion} from './SegmentBackgroundReview.jsx';
import {UpscalingButton} from './Upscaling.jsx';
import React,{useEffect,useLayoutEffect,useRef,useState} from 'react';
import './unifiedMethods.css';
import {pointInVideo} from './ninjaState.js';
export default function SequenceProgress(props){
  const parentId=props.job?.parameters?.sequence_parent_id;
  const [parent,setParent]=useState(null);
  const [viewedAttempt,setViewedAttempt]=useState(null);
  const [loadError,setLoadError]=useState('');
  useEffect(()=>{
    if(!parentId)return;
    const controller=new AbortController();
    setParent(null);setViewedAttempt(null);setLoadError('');
    let timer;
    async function readParent(){
      try{
        const response=await fetch('/api/jobs/'+parentId,{signal:controller.signal});
        if(!response.ok)throw Error('No se pudo consultar el vídeo completo.');
        const result=await response.json();
        let attempt=null;
        if(['failed','cancelled'].includes(props.job.status)){
          const detail=await fetch('/api/jobs/'+props.job.id,{signal:controller.signal});
          if(!detail.ok)throw Error('No se pudo cargar el vídeo para revisión.');
          attempt=await detail.json();
        }
        if(controller.signal.aborted)return;
        setParent(result);setViewedAttempt(attempt);setLoadError('');
        if(!['completed','failed','cancelled','submission_unknown'].includes(result.status))timer=setTimeout(readParent,5000);
      }catch(error){if(!controller.signal.aborted){setLoadError(error.message);timer=setTimeout(readParent,5000);}}
    }
    readParent();
    return ()=>{controller.abort();clearTimeout(timer);};
  },[parentId,props.job?.id,props.job?.status]);
  if(parentId){
    if(loadError)return <p role="alert">{loadError}</p>;
    if(!parent||parent.id!==parentId)return null;
    return <SegmentList key={props.job.id} {...props} job={parent} focusIndex={props.job.parameters.sequence_index} viewedAttempt={viewedAttempt||props.job}
      onResume={updated=>{props.onResume?.(updated);props.onOpen?.(parentId);}}/>;
  }
  return <SegmentList key={props.job?.id} {...props}/>;
}

function PartialPreview({jobId,index,attemptId}){
  const [busy,setBusy]=useState(false),[result,setResult]=useState(null),[error,setError]=useState('');
  async function join(){
    setBusy(true);setError('');
    try{
      const query=attemptId?'?attempt_id='+encodeURIComponent(attemptId):'';
      const response=await fetch(`/api/jobs/${jobId}/sequence/${index}/partial-preview${query}`,{method:'POST'});
      const value=await response.json();
      if(!response.ok)throw Error(value.detail||'No se pudo unir el vídeo.');
      setResult(value);
    }catch(e){setError(e.message);}finally{setBusy(false);}
  }
  return <div><button type="button" className="secondary" disabled={busy} onClick={join}>{busy?'Uniendo tramos…':'Unir vídeo hasta este tramo'}</button>
    {error&&<p role="alert">{error}</p>}
    {result&&<><video src={result.url} aria-label={'Montaje hasta el tramo '+(index+1)} preload="metadata" controls playsInline width="100%"/><a href={result.url+'?download=true'}>Descargar montaje · {(result.frames/24).toFixed(2)} s</a></>}
  </div>;
}

function SegmentSheet({visible,trigger,index,onClose,children}){
  const ref=useRef(null);
  useLayoutEffect(()=>{
    if(!visible)return;
    const dialog=ref.current;
    const anchor=trigger.current;
    const viewport=window.visualViewport;
    const content=dialog.firstElementChild;
    function place(){
      const rect=anchor.getBoundingClientRect();
      const left=(viewport?.offsetLeft||0)+8;
      const top=(viewport?.offsetTop||0)+8;
      const right=left+(viewport?.width??window.innerWidth)-16;
      const bottom=top+(viewport?.height??window.innerHeight)-16;
      const width=Math.min(340,right-left);
      dialog.style.width=width+'px';
      const height=content.offsetHeight+2;
      const below=bottom-rect.bottom-6;
      const above=rect.top-top-6;
      const opensAbove=height>below&&above>below;
      const limit=Math.max(1,Math.min(bottom-top,Math.max(above,below)));
      const y=opensAbove?rect.top-6-Math.min(height,limit):rect.bottom+6;
      dialog.style.maxHeight=limit+'px';
      dialog.style.top=Math.max(top,Math.min(y,bottom-Math.min(height,limit)))+'px';
      dialog.style.left=Math.max(left,Math.min(rect.right-width,right-width))+'px';
    }
    dialog.showModal();
    place();
    const observer=new ResizeObserver(place);
    observer.observe(content);
    window.addEventListener('resize',place);
    window.addEventListener('scroll',place,true);
    viewport?.addEventListener('resize',place);
    viewport?.addEventListener('scroll',place);
    return ()=>{
      observer.disconnect();
      window.removeEventListener('resize',place);
      window.removeEventListener('scroll',place,true);
      viewport?.removeEventListener('resize',place);
      viewport?.removeEventListener('scroll',place);
      dialog.querySelectorAll('video').forEach(video=>video.pause());
      dialog.close();
    };
  },[visible,trigger]);
  return <dialog ref={ref} className="segment-sheet" aria-label={`Acciones del tramo ${index+1}`}
    onCancel={event=>{event.preventDefault();onClose();}}
    onClick={event=>{if(event.target===ref.current)onClose();}}>
    <div className="segment-sheet-inner">
      <header><strong>Acciones · Tramo {index+1}</strong><button type="button" className="secondary" aria-label="Cerrar acciones" onClick={onClose}>×</button></header>
      <div className="segment-action-list">{children}</div>
    </div>
  </dialog>;
}

function segmentTime(frame){
  const seconds=frame/24;
  const minutes=Math.floor(seconds/60);
  return String(minutes).padStart(2,'0')+':'+(seconds-minutes*60).toFixed(2).replace(/\.00$/,'').padStart(seconds%60<10&&frame%24?5:2,'0');
}

function SegmentList({job,onError,onOpen,onResume,focusIndex,viewedAttempt}){
  const stopped=['failed','cancelled'].includes(job?.status);
  const [open,setOpen]=useState(Number.isInteger(focusIndex)||stopped),[data,setData]=useState(null),[error,setError]=useState(''),[point,setPoint]=useState(null),[sending,setSending]=useState(false);
  useEffect(()=>{setPoint(null);},[job?.id,job?.sequence_index]);
  useEffect(()=>{if(stopped||job?.sequence_waiting)setOpen(true);},[stopped,job?.sequence_waiting]);
  useEffect(()=>{if(!open||!job?.video_sequence_version)return;let live=true,timer;const abort=new AbortController();
    async function poll(){try{const r=await fetch('/api/jobs/'+job.id+'/sequence',{signal:abort.signal});if(!r.ok)throw Error('No se pudo actualizar el plan de tramos.');const value=await r.json();if(live){setData(value);setError('');}}
      catch(e){if(live&&e.name!=='AbortError')setError(e.message);}if(live&&!['completed','failed','cancelled','submission_unknown'].includes(job.status))timer=setTimeout(poll,5000);}
    poll();return()=>{live=false;abort.abort();clearTimeout(timer);};
  },[open,job?.id,job?.status,job?.sequence_waiting,job?.sequence_done]);
  const sheetTrigger=useRef(null);
  const [sheet,setSheet]=useState(null);
  const [selected,setSelected]=useState(null);
  const [previewId,setPreviewId]=useState(null);
  const [rejectedId,setRejectedId]=useState(null);
  const [approvalId,setApprovalId]=useState(null);
  useEffect(()=>{setSelected(null);setApprovalId(null);},[job?.id,job?.status]);
  if(!job?.video_sequence_version)return null;
  async function resumeSegment(s){
    if(sending||!stopped)return;
    setSending(true);setError('');
    try{
      const response=await fetch(`/api/jobs/${job.id}/sequence/${s.index}/resume?attempt_id=${s.attempt_id}`,{method:'POST'});
      const result=await response.json();
      if(!response.ok)throw Error(result.detail||'No se pudo reanudar la secuencia.');
      setSheet(null);setSelected(null);onResume?.(result);
    }catch(e){setError(e.message);}finally{setSending(false);}
  }
  async function regenerate(s,repeat=false){
    if(sending)return;
    setSending(true);setError('');
    try{
      const key=(repeat?'segment-repeat-':'segment-regenerate-')+s.attempt_id;
      const token=sessionStorage.getItem(key)||crypto.randomUUID();
      sessionStorage.setItem(key,token);
      const url=repeat?`/api/jobs/${s.job.recorded_source_id}/repeat-recorded?request_id=${token}`:
        `/api/jobs/${job.id}/sequence/${s.index}/regenerate?attempt_id=${s.attempt_id}&request_id=${token}`;
      const response=await fetch(url,{method:'POST'});
      if(!response.ok)throw Error((await response.json()).detail||'No se pudo regenerar el tramo.');
      const result=await response.json();sessionStorage.removeItem(key);setSheet(null);onOpen?.(result.id);
    }catch(e){setError(e.message);}finally{setSending(false);}
  }
  async function approveSegment(s){
    if(sending||!stopped)return;
    setSending(true);setError('');
    try{
      const query=new URLSearchParams({attempt_id:s.job.id,sha256:s.job.rejected_preview_sha256});
      const response=await fetch(`/api/jobs/${job.id}/sequence/${s.index}/approve?${query}`,{method:'POST'});
      const result=await response.json();
      if(!response.ok)throw Error(result.detail||'No se pudo guardar la aprobación.');
      setSheet(null);setApprovalId(null);setPreviewId(null);onResume?.(result);
    }catch(e){setError(e.message);}finally{setSending(false);}
  }
  async function rejectSegment(s){
    if(sending)return;
    setSending(true);setError('');
    try{
      const query=new URLSearchParams({attempt_id:s.job.id,sha256:s.job.rejected_preview_sha256});
      const response=await fetch(`/api/jobs/${job.id}/sequence/${s.index}/reject-preview?${query}`,{method:'POST'});
      if(!response.ok)throw Error((await response.json()).detail||'No se pudo guardar el rechazo.');
      setSheet(null);onOpen?.(job.id);setRejectedId(s.job.id);
    }catch(e){setError(e.message);}finally{setSending(false);}
  }
  async function choose(s){if(!point||sending)return;setSending(true);
    try{const r=await fetch(`/api/jobs/${job.id}/sequence/${s.index}/selection`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(point)});const value=await r.json();if(!r.ok)throw Error(value.detail||'No se pudo guardar la selección.');setError('');}catch(e){onError?.(e.message);setError(e.message);}finally{setSending(false);}}
  const groups=new Map();
  for(const segment of data?.segments||[]){
    if(focusIndex!=null&&segment.index!==focusIndex)continue;
    if(!groups.has(segment.shot))groups.set(segment.shot,[]);
    groups.get(segment.shot).push(segment);
  }
  function showSheet(s,button,review=false){
    sheetTrigger.current=button;
    setSelected(null);setApprovalId(null);setError('');
    setPreviewId(review?s.job.id:null);setSheet(s.index);
  }
  return <section className="panel sequence-progress"><details open={open} onToggle={e=>{if(e.target===e.currentTarget)setOpen(e.currentTarget.open);}}>
    <summary><strong>Tramos y cambios de plano</strong><span>{job.sequence_total?`${job.sequence_done||0} de ${job.sequence_total} tramos aprobados · ${job.sequence_shots} ${job.sequence_shots===1?'plano':'planos'}`:'Analizando el vídeo'}</span></summary>
    {job.sequence_total>0&&<progress className="segment-meter" aria-label="Tramos aprobados" value={job.sequence_done||0} max={job.sequence_total}/>}
    <div className="segment-tools"><SegmentAlerts/>
    <details><summary>Cómo se unen los tramos</summary><p>{job.video_sequence_version==='viggle-remake-hop17-1'?'Ventanas nativas de Viggle. Las continuaciones del mismo plano usan 17 fotogramas de contexto':<>Hasta {(job.parameters?.direct_chunk_frames||120)/24} segundos por tramo. {job.video_sequence_version==='visual-faithful-sequence-5s-v1'?'Las continuaciones del mismo plano usan 5 fotogramas de contexto':'El último fotograma enlaza las continuaciones del mismo plano'}</>}; un corte comienza con su propia imagen. El montaje conserva la duración y el audio del vídeo.</p></details></div>
    {job.sequence_waiting&&<p role="status">En este nuevo plano, toca al personaje que quieres conservar para continuar.</p>}
    {focusIndex!=null&&<button type="button" className="secondary" onClick={()=>onOpen?.(job.id)}>Ver vídeo completo</button>}
    {error&&<p role="alert">{error}</p>}
    {Array.from(groups,([shot,segments])=><section className="segment-group" key={shot}><h3>Plano {shot+1}</h3><div className="sequence-cards">{segments.map(segment=>{
      const s=viewedAttempt?{...segment,job:{...(viewedAttempt.id===segment.job?.id?segment.job:{}),...viewedAttempt}}:segment;
      const currentAttempt=s.job?.id===segment.attempt_id;
      const canApprove=stopped&&(currentAttempt||s.job?.parameters?.standalone_segment_attempt===segment.attempt_id)&&!s.job?.sequence_rejected_sha256&&rejectedId!==s.job?.id&&s.index===(job.sequence_done||0)&&s.job?.rejected_preview_sha256;
      const justApproved=stopped&&currentAttempt&&job.sequence_approved_index===s.index&&job.sequence_done===s.index+1;
      const next=data.segments[s.index+1];
      const backgroundSha=backgroundVersion(s.job);
      return <article key={s.index} className={'sequence-card'+(s.starts_at_cut?' is-cut':'')}>
      {job.sequence_waiting&&job.sequence_index===s.index?<><div className="sequence-selector" role="button" tabIndex={0} aria-label="Marca el personaje con un toque o con las flechas" style={{aspectRatio:data.width+'/'+data.height}}
        onPointerUp={e=>{const p=pointInVideo(e.clientX,e.clientY,e.currentTarget.getBoundingClientRect(),data.width,data.height);if(p&&!sending)setPoint(p);}}
        onKeyDown={e=>{if(!['Enter',' ','ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)||sending)return;e.preventDefault();setPoint(p=>({x:Math.max(0,Math.min(1,(p?.x??.5)+(e.key==='ArrowLeft'?-.01:e.key==='ArrowRight'?.01:0))),y:Math.max(0,Math.min(1,(p?.y??.5)+(e.key==='ArrowUp'?-.01:e.key==='ArrowDown'?.01:0)))}));}}>
        <img src={s.thumbnail_url} alt={`Inicio del plano ${s.shot+1}`}/>{point&&<span className="ninja-marker" style={{left:point.x*100+'%',top:point.y*100+'%'}}/>}</div><button className="primary" disabled={!point||sending} onClick={()=>choose(s)}>{sending?'Guardando…':'Confirmar personaje y continuar'}</button></>:<img loading="lazy" src={s.thumbnail_url} alt={`Plano ${s.shot+1}, inicio del tramo ${s.index+1}`}/>}
      <div className="segment-info"><strong>Tramo {s.index+1}</strong>
        <span>{segmentTime(s.start)}–{segmentTime(s.start+s.keep)}</span>
        <small className={s.index<(job.sequence_done||0)&&!viewedAttempt?'segment-approved':''}>
          {canApprove?'Pendiente de revisión':s.index<(job.sequence_done||0)&&!viewedAttempt?'✓ Aprobado':s.job?.stage||'Pendiente'}
        </small>
        {canApprove&&s.job?.rejected_preview_url&&<button type="button" className="primary segment-review" onClick={e=>showSheet(s,e.currentTarget,true)}>Revisar tramo</button>}
      </div>
      {(s.job||s.index<(job.sequence_done||0)||(stopped&&s.attempt_id&&s.index<=(job.sequence_done||0)))&&<>
        <button type="button" className="secondary segment-more" aria-label={`Acciones del tramo ${s.index+1}`} aria-haspopup="dialog" onClick={e=>showSheet(s,e.currentTarget)}>⋯</button>
        <SegmentSheet trigger={sheetTrigger} index={s.index} visible={sheet===s.index} onClose={()=>setSheet(null)}>
        {error&&sheet===s.index&&<p role="alert">{error}</p>}
      {s.job?.rejected_preview_url&&<>
        <button type="button" className="secondary" aria-expanded={previewId===s.job.id}
          onClick={()=>setPreviewId(previewId===s.job.id?null:s.job.id)}>
          {previewId===s.job.id?'Ocultar vídeo':s.job.human_segment_review?'Revisar vídeo generado':'Ver tramo rechazado'}
        </button>
        {previewId===s.job.id&&<video controls playsInline preload="metadata" width="100%"
          src={s.job.rejected_preview_url+(s.job.rejected_preview_sha256?'?sha256='+s.job.rejected_preview_sha256:'')} aria-label={`Vídeo rechazado del tramo ${s.index+1}`}/>}
      </>}
      {sheet===s.index&&backgroundSha&&<SegmentBackgroundReview key={s.job.id+backgroundSha} jobId={s.job.id} sha={backgroundSha}/>}
      {canApprove&&(approvalId===s.attempt_id?<div>
        <p>Se guardará este vídeo como resultado del tramo {s.index+1}. Tú decides cuándo continuar.</p>
        <button type="button" className="primary" disabled={sending} onClick={()=>approveSegment(s)}>Confirmar aprobación</button>
        <button type="button" className="secondary" disabled={sending} onClick={()=>setApprovalId(null)}>Volver</button>
      </div>:<button type="button" className="primary" disabled={sending}
        onClick={()=>{setSelected(null);setApprovalId(s.attempt_id);}}>Aprobar tramo</button>)}
      {canApprove&&s.job?.human_segment_review&&<button type="button" className="secondary" disabled={sending} onClick={()=>rejectSegment(s)}>Rechazar tramo</button>}
      {s.job&&focusIndex==null&&<button type="button" className="secondary" onClick={()=>{setSheet(null);onOpen?.(s.job.id);}}>Ver tramo</button>}
      {(s.job?.result_available||s.job?.rejected_preview_url||s.index<(job.sequence_done||0))&&<PartialPreview key={job.id+'-'+s.index+'-'+(s.job?.id||'original')} jobId={job.id} index={s.index} attemptId={s.job?.id}/>}
      {justApproved&&<button type="button" className="primary" disabled={sending} onClick={()=>resumeSegment(next||{index:s.index+1,attempt_id:s.attempt_id})}>
        {next?`Continuar con el tramo ${next.index+1}`:'Montar vídeo completo'}
      </button>}
      {currentAttempt&&['completed','failed','cancelled'].includes(s.job?.status)&&<button type="button" className="secondary" disabled={sending} onClick={()=>regenerate(s)}>Regenerar sólo este tramo</button>}
      {currentAttempt&&s.job?.recorded_source_id&&<button type="button" className="secondary" disabled={sending} onClick={()=>regenerate(s,true)}>Repetir con la misma receta</button>}
      {stopped&&s.attempt_id&&(selected===s.index?<div>
        <p>Se conservarán los tramos anteriores y se generará el vídeo desde el tramo {s.index+1} hasta el final.</p>
        <button type="button" className="primary" disabled={sending} onClick={()=>resumeSegment(s)}>Reanudar desde el tramo {s.index+1}</button>
        <button type="button" className="secondary" disabled={sending} onClick={()=>setSelected(null)}>Volver</button>
      </div>:<button type="button" className="secondary" disabled={sending||s.index>(job.sequence_done||0)} onClick={()=>{setApprovalId(null);setSelected(s.index);}}>{justApproved?'Regenerar desde este tramo':'Continuar desde aquí'}</button>)}
      {s.job?.status==='completed'&&s.job.result_available&&<UpscalingButton jobId={job.id} segmentIndex={s.index} previewJobId={job.result_available?null:s.job.id} previewStart={(job.result_available?s.start:s.context_frames)/24}/>}
      </SegmentSheet></>}
    </article>;})}</div></section>)}
  </details></section>;
}
