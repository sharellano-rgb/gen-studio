import React,{useEffect,useId,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import ResultComparison from './ResultComparison.jsx';
import {comparisonSpec} from './sceneResults.js';
import {sceneName,sceneDuration,sceneResolution} from './historyLibrary.js';
import {resultUrl} from './resultVersion.js';
import {resultReferences,sceneContextName} from './resultPresentation.js';
import './cinemaPresentation.css';

/** A presentation of this exact take. Generation and review remain in the editor. */
export default function CinemaPresentation({job,assets=[],method,onClose,initialTime=0}){
 const dialog=useRef(null),video=useRef(null),titleId=useId(),closeButton=useRef(null);
 const spec=comparisonSpec(job),[mode,setMode]=useState(spec?'compare':'final'),[context,setContext]=useState(null),[failed,setFailed]=useState(false);
 const position=useRef(initialTime);
 const [seekRequest,setSeekRequest]=useState(()=>({frame:Math.max(0,Math.round((initialTime-(spec?.resultOffset||0))*24)),token:'cinema-open'}));
 const fallback=sceneName({id:job.id,root:job,jobs:[job]},assets);
 const title=sceneContextName(context,fallback);
 const references=resultReferences(job).map(ref=>({...ref,name:assets.find(asset=>asset.id===ref.id)?.name||ref.label}));
 useEffect(()=>{
  const controller=new AbortController();
  fetch('/api/jobs/'+job.id+'/scene-context',{signal:controller.signal}).then(response=>response.ok?response.json():null).then(value=>{if(!controller.signal.aborted)setContext(value);}).catch(()=>{});
  return()=>controller.abort();
 },[job.id]);
 useEffect(()=>{
  const element=dialog.current,previous=document.activeElement,overflow=document.body.style.overflow;
  document.querySelectorAll('video').forEach(media=>{if(!element.contains(media))media.pause();});
  document.body.style.overflow='hidden';element.showModal();closeButton.current?.focus({preventScroll:true});
  return()=>{element.querySelectorAll('video').forEach(media=>media.pause());element.close();document.body.style.overflow=overflow;if(previous?.isConnected)previous.focus({preventScroll:true});};
 },[]);
 function changeMode(next){
  if(next===mode)return;
  if(video.current){position.current=video.current.currentTime;video.current.pause();}
  if(next==='final'){
   const current=dialog.current?.querySelector('.cinema-comparison-result');
   if(current?.readyState>=1)position.current=current.currentTime;
  }else setSeekRequest({frame:Math.max(0,Math.round((position.current-(spec?.resultOffset||0))*24)),token:crypto.randomUUID()});
  setFailed(false);setMode(next);
 }
 return createPortal(<dialog ref={dialog} className="cinema-dialog" aria-labelledby={titleId} onCancel={event=>{event.preventDefault();onClose();}}>
  <div className="cinema-shell">
   <header className="cinema-header"><div className="cinema-brand"><span className="cinema-brand-mark" aria-hidden="true">G</span><span>GEN STUDIO <span className="cinema-brand-divider">/</span> <strong>CINE</strong></span></div><button type="button" ref={closeButton} className="cinema-close" onClick={onClose}>Volver <span aria-hidden="true">×</span></button></header>
   <div className="cinema-title"><p>Tu escena, en primer plano</p><h1 id={titleId}>{title}</h1></div>
   <div className="cinema-layout">
    <div className="cinema-screen">
     <div className="cinema-view-modes" role="group" aria-label="Vista de Cine">{spec&&<button type="button" aria-pressed={mode==='compare'} onClick={()=>changeMode('compare')}>Antes / después</button>}<button type="button" aria-pressed={mode==='final'} onClick={()=>changeMode('final')}>Sólo resultado</button></div>
     {spec&&<ResultComparison job={job} presentation active={mode==='compare'} seekRequest={seekRequest}/>}
     {mode==='final'&&<div className="cinema-final"><video ref={video} src={resultUrl(job)} controls playsInline preload="metadata" aria-label="Resultado en Cine" onLoadedMetadata={event=>{const media=event.currentTarget;setFailed(false);if(Number.isFinite(media.duration))media.currentTime=Math.max(0,Math.min(position.current,media.duration));}} onError={()=>setFailed(true)}/>{failed&&<div className="cinema-media-error" role="alert"><p>No se pudo cargar el resultado.</p><button type="button" onClick={()=>{setFailed(false);video.current?.load();}}>Volver a cargar</button></div>}</div>}
    </div>
    <aside className="cinema-info" aria-label="Acerca de esta toma"><span className="cinema-eyebrow">Hecho en Gen Studio</span><h2>{method}</h2><div className="cinema-specs">{sceneDuration(job)&&<span>{sceneDuration(job)}</span>}{sceneResolution(job)&&<span>{sceneResolution(job)} px</span>}<span>24 fps</span></div>
     {references.length>0&&<div className="cinema-references"><h3>El punto de partida</h3>{references.map(reference=><figure key={reference.id}><img src={'/api/assets/'+reference.id} alt={reference.label+': '+reference.name} loading="lazy" onError={event=>{event.currentTarget.hidden=true;}}/><figcaption>{reference.label}<small>{reference.name!==reference.label?reference.name:''}</small></figcaption></figure>)}</div>}
     <a className="cinema-download" href={resultUrl(job,{download:true})}>Descargar vídeo <span aria-hidden="true">↓</span></a>
    </aside>
   </div>
   <footer className="cinema-footer"><span>GEN STUDIO</span><span>Imagina el cambio. Mira el resultado.</span></footer>
  </div>
 </dialog>,document.body);
}
