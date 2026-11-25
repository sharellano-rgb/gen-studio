import {UpscalingButton,UpscalingSegments} from './Upscaling.jsx';
import React,{useEffect,useRef,useState} from 'react';
import ResultComparison from './ResultComparison.jsx';
import CinemaPresentation from './CinemaPresentation.jsx';
import JobElapsed from './JobElapsed.jsx';
import {comparisonSpec} from './sceneResults.js';
import {sceneDuration,sceneResolution} from './historyLibrary.js';
import {resultUrl} from './resultVersion.js';
import {resultReferences} from './resultPresentation.js';
import './h3Result.css';

export default function ResultViewer({job,assets=[],onReviewSaved,seekRequest,method}){
  const [mode,setMode]=useState('final'),[visited,setVisited]=useState(false),[failed,setFailed]=useState(false),[cinema,setCinema]=useState(false);
  const finalVideo=useRef(null),finalTime=useRef(0),container=useRef(null);
  useEffect(()=>{if(!seekRequest||!Number.isInteger(seekRequest.frame))return;changeMode('compare');requestAnimationFrame(()=>{container.current?.focus({preventScroll:true});container.current?.scrollIntoView({block:'start',behavior:'auto'});});},[seekRequest?.token]);
  const p=job.parameters,canCompare=Boolean(comparisonSpec(job));
  const references=resultReferences(job).map(ref=>({...ref,name:assets.find(asset=>asset.id===ref.id)?.name||ref.label}));
  function changeMode(next){
    if(next===mode)return;
    if(finalVideo.current){finalTime.current=finalVideo.current.currentTime;finalVideo.current.pause();}
    if(next==='compare')setVisited(true);
    setMode(next);
  }
  return <section ref={container} tabIndex={-1} className="h3-result-review" aria-label={"Revisar resultado "+method}>
    {mode==='final'&&<><video className="result-player-video" ref={finalVideo} controls playsInline preload="metadata" src={resultUrl(job)} aria-label={"Vídeo final de "+method} onLoadedMetadata={event=>{setFailed(false);const video=event.currentTarget;if(finalTime.current>0&&Number.isFinite(video.duration))video.currentTime=Math.min(finalTime.current,video.duration);}} onError={()=>setFailed(true)}/>{failed&&<div role="alert" className="h3-result-media-error"><p>No se pudo cargar el vídeo. Puedes volver a intentarlo o descargarlo.</p><button className="secondary" onClick={()=>{setFailed(false);finalVideo.current?.load();}}>Volver a cargar el vídeo</button></div>}</>}
    <div className="result-player-toolbar">{job.status==='completed'&&job.result_available&&<UpscalingButton jobId={job.id}/>}
    <button type="button" className="cinema-launch" onClick={()=>{const current=finalVideo.current||container.current?.querySelector('.result-comparison [data-comparison-video="result"]');if(current?.readyState>=1)finalTime.current=current.currentTime;current?.pause();setCinema(true);}}>Ver en Cine <span aria-hidden="true">↗</span></button>
    <div className="h3-result-modes" role="group" aria-label={method==='H3 directo'?'Vista del resultado H3':'Vista del resultado '+method}>
      <button type="button" aria-pressed={mode==='final'} onClick={()=>changeMode('final')}>Vídeo final</button>
      {canCompare&&<button type="button" aria-pressed={mode==='compare'} onClick={()=>changeMode('compare')}>Comparar con original</button>}
    </div>
      <a className="h3-result-download" href={resultUrl(job,{download:true})}>Descargar vídeo ↓</a>
    </div>
    <div className="h3-result-summary"><span className="h3-result-ready">{job.status==='completed'?'Generado':'Copia disponible'}</span><span>{sceneDuration(job)}</span>{sceneResolution(job)&&<span>{sceneResolution(job)} px</span>}<span>24 fps</span></div>
    <UpscalingSegments job={job}/>{/* Keep an unfinished note and the comparison frame when switching views. */}
    {visited&&<ResultComparison job={job} seekRequest={seekRequest} active={mode==='compare'&&!cinema} onReviewSaved={onReviewSaved}/>}
    <details className="h3-result-details"><summary>Información de la toma</summary><p>{job.stage}</p><JobElapsed job={job}/>{references.length>0&&<><h3>Referencias de esta toma</h3><div className="h3-result-references">{references.map(reference=><figure key={reference.id}><img src={'/api/assets/'+reference.id} alt={'Referencia: '+reference.name} loading="lazy"/><figcaption>{reference.label}<br/>{reference.name!==reference.label&&reference.name}</figcaption></figure>)}</div></>}</details>
    {cinema&&<CinemaPresentation key={job.id} job={job} assets={assets} method={method} initialTime={finalTime.current} onClose={()=>setCinema(false)}/>}
  </section>;
}
