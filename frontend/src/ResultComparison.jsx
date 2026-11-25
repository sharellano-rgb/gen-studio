import React,{useEffect,useId,useRef,useState} from 'react';
import {comparisonSpec,comparisonFrame} from './sceneResults.js';
import QualityNotes from './QualityNotes.jsx';
import './sceneResults.css';
import './cinemaComparison.css';

export default function ResultComparison({job,onReviewSaved,active=true,seekRequest,presentation=false,specOverride=null}) {
  const spec=specOverride||comparisonSpec(job);
  if(!spec)return null;
  return <ComparisonPlayer key={[job.id,spec.source,spec.offset,spec.resultOffset,spec.frames,presentation].join(':')} job={job} spec={spec} active={active} seekRequest={seekRequest} onReviewSaved={onReviewSaved} presentation={presentation}/>;
}
function ComparisonPlayer({job,spec,onReviewSaved,active,seekRequest,presentation}) {
  const source=useRef(null),result=useRef(null),playRequest=useRef(0),isPlaying=useRef(false),initialized=useRef(false);
  const activeRef=useRef(active),playIntent=useRef(false),revealAnimation=useRef(null);
  const instanceId=useId();
  activeRef.current=active;
  const [frame,setFrame]=useState(0),[playing,setPlaying]=useState(false),[ready,setReady]=useState(false),[error,setError]=useState('');
  const [mediaError,setMediaError]=useState(false),[reload,setReload]=useState(0);
  const [zoom,setZoom]=useState(1),[center,setCenter]=useState({x:50,y:50});
  const [aspectRatio,setAspectRatio]=useState(16/9);
  const [division,setDivision]=useState(50),[revealing,setRevealing]=useState(false),[muted,setMuted]=useState(false);
  const maxFrame=Math.max(0,spec.frames-1);
  function pause(){
    playRequest.current++;isPlaying.current=false;playIntent.current=false;
    source.current?.pause();result.current?.pause();setPlaying(false);
  }
  function stopReveal(){
    if(revealAnimation.current!==null)cancelAnimationFrame(revealAnimation.current);
    revealAnimation.current=null;setRevealing(false);
  }
  useEffect(()=>{
    if(!active){pause();stopReveal();initialized.current=false;setReady(false);}
  },[active]);
  function seek(value){
    pause();const t=comparisonFrame(value,spec.frames,spec.offset,spec.resultOffset||0);setFrame(t.frame);
    if(source.current?.readyState>=1)source.current.currentTime=t.sourceTime;
    if(result.current?.readyState>=1)result.current.currentTime=t.resultTime;
  }
  useEffect(()=>{if(Number.isInteger(seekRequest?.frame))seek(seekRequest.frame);},[seekRequest?.token]);
  function loaded(){
    if(activeRef.current&&!initialized.current&&source.current?.readyState>=2&&result.current?.readyState>=2){initialized.current=true;setReady(true);seek(frame);}
  }
  async function toggle(){
    if(playing||playIntent.current){pause();return;}
    if(!activeRef.current||!ready||mediaError||!source.current||!result.current)return;
    if(frame>=maxFrame)seek(0);
    const token=++playRequest.current;
    playIntent.current=true;setError('');
    try{
      await Promise.all([source.current.play(),result.current.play()]);
      if(token===playRequest.current&&activeRef.current&&playIntent.current){
        isPlaying.current=true;setPlaying(true);
      }
    }catch{
      if(token===playRequest.current){pause();setError('No se pudo iniciar la reproducción. Inténtalo de nuevo.');}
    }
  }
  useEffect(()=>{
    if(!playing||!active)return;let id;
    const sync=()=>{
      const s=source.current,r=result.current;if(!s||!r||!activeRef.current)return;
      const current=Math.round((r.currentTime-(spec.resultOffset||0))*24);setFrame(Math.max(0,Math.min(maxFrame,current)));
      if(current>=spec.frames){seek(maxFrame);return;}
      if(!r.seeking&&!s.seeking&&Math.abs(s.currentTime-r.currentTime-spec.offset+(spec.resultOffset||0))>1/24)s.currentTime=r.currentTime+spec.offset-(spec.resultOffset||0);
      id=requestAnimationFrame(sync);
    };
    id=requestAnimationFrame(sync);return()=>cancelAnimationFrame(id);
  },[playing,active,spec.source,spec.offset,spec.resultOffset,maxFrame]);
  useEffect(()=>{
    const sourceVideo=source.current,resultVideo=result.current;
    const onVisibility=()=>{if(document.hidden){pause();stopReveal();}};
    document.addEventListener('visibilitychange',onVisibility);
    return()=>{
      document.removeEventListener('visibilitychange',onVisibility);
      playRequest.current++;isPlaying.current=false;playIntent.current=false;
      sourceVideo?.pause();resultVideo?.pause();
      if(revealAnimation.current!==null)cancelAnimationFrame(revealAnimation.current);
    };
  },[reload]);
  function pick(event){
    const box=event.currentTarget.getBoundingClientRect();
    setCenter({x:100*(event.clientX-box.left)/box.width,y:100*(event.clientY-box.top)/box.height});
  }
  function retry(){pause();stopReveal();initialized.current=false;setReady(false);setMediaError(false);setError('');setReload(value=>value+1);}
  function videoProps(ref,url,label,sourceVideo){
    return {
      ref,src:active?url:undefined,muted:sourceVideo||(presentation&&muted),playsInline:true,preload:active?'auto':'none',
      onLoadedMetadata:event=>{
        const video=event.currentTarget;
        if(!sourceVideo&&video.videoWidth>0&&video.videoHeight>0)setAspectRatio(video.videoWidth/video.videoHeight);
      },
      onLoadedData:loaded,onCanPlay:loaded,onEnded:pause,
      onPlay:event=>{if(!activeRef.current||!playIntent.current)event.currentTarget.pause();},
      onPause:()=>{if(isPlaying.current)pause();},onWaiting:()=>{if(isPlaying.current)pause();},
      onError:()=>{
        if(!activeRef.current)return;
        pause();setMediaError(true);setError('No se pudo cargar uno de los vídeos de la comparación.');
      },
      'aria-label':label,'data-comparison-video':sourceVideo?'source':'result',
    };
  }
  function adjustDivision(value){
    stopReveal();setDivision(Math.max(0,Math.min(100,value)));
  }
  function dragDivision(event){
    const box=event.currentTarget.getBoundingClientRect();
    if(box.width)adjustDivision(100*(event.clientX-box.left)/box.width);
  }
  function reveal(){
    if(!activeRef.current||!ready||mediaError)return;
    stopReveal();
    if(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches){setDivision(0);return;}
    setDivision(100);setRevealing(true);
    let started;
    const step=now=>{
      if(!activeRef.current){revealAnimation.current=null;setRevealing(false);return;}
      if(started===undefined)started=now;
      const progress=Math.min(1,(now-started)/1400);
      const eased=progress*progress*(3-2*progress);
      setDivision(100*(1-eased));
      if(progress<1)revealAnimation.current=requestAnimationFrame(step);
      else{revealAnimation.current=null;setRevealing(false);}
    };
    revealAnimation.current=requestAnimationFrame(step);
  }
  const style={transform:`scale(${zoom})`,transformOrigin:`${center.x}% ${center.y}%`};
  if(presentation)return <section className="cinema-comparison" style={{'--comparison-aspect':aspectRatio,'--cinema-division':division+'%'}} hidden={!active} id={'cinema-comparison-'+job.id+'-'+instanceId} aria-label="Antes y después">
    <div className="cinema-comparison-stage">
      <div className="cinema-comparison-frame"
        onPointerDown={event=>{if(event.button!==0)return;event.currentTarget.setPointerCapture(event.pointerId);dragDivision(event);}}
        onPointerMove={event=>{if(event.currentTarget.hasPointerCapture(event.pointerId))dragDivision(event);}}
        onPointerUp={event=>{if(event.currentTarget.hasPointerCapture(event.pointerId))event.currentTarget.releasePointerCapture(event.pointerId);}}>
        <video key={'result-'+reload} className="cinema-comparison-result" {...videoProps(result,spec.result,spec.resultLabel||'Resultado',false)}/>
        <video key={'source-'+reload} className="cinema-comparison-original" {...videoProps(source,spec.source,spec.sourceLabel,true)}/>
        <span className="cinema-comparison-badge cinema-comparison-before" hidden={division<5}>Original</span>
        <span className="cinema-comparison-badge cinema-comparison-after" hidden={division>95}>Resultado</span>
        <span className="cinema-comparison-divider" aria-hidden="true"><span>‹<i/>›</span></span>
      </div>
    </div>
    <div className="cinema-comparison-toolbar">
      <button type="button" className="cinema-comparison-play" disabled={!ready||mediaError} onClick={toggle}>{playing?'Pausar':'Reproducir'}</button>
      <button type="button" className="cinema-comparison-reveal" disabled={!ready||mediaError||revealing} onClick={reveal}>{revealing?'Revelando…':'Revelar cambio'}</button>
      <button type="button" className="cinema-comparison-sound" aria-pressed={muted} onClick={()=>setMuted(value=>!value)}>{muted?'Activar sonido':'Silenciar'}</button>
    </div>
    <label className="cinema-comparison-timeline">
      <span><span>Posición</span><output>{(frame/24).toFixed(2)} / {(maxFrame/24).toFixed(2)} s</output></span>
      <input aria-label="Posición de reproducción en segundos" aria-valuetext={(frame/24).toFixed(2)+' segundos'} type="range" min="0" max={maxFrame/24} step={1/24} value={frame/24} disabled={!ready||mediaError} onChange={event=>seek(Number(event.target.value)*24)}/>
    </label>
    <label className="cinema-comparison-split">
      <span><span>Original</span><span>División · {Math.round(division)}%</span><span>Resultado</span></span>
      <input aria-label="División entre original y resultado" aria-valuetext={Math.round(division)+'% original y '+Math.round(100-division)+'% resultado'} type="range" min="0" max="100" step="1" value={division} onChange={event=>adjustDivision(Number(event.target.value))}/>
    </label>
    <p className="cinema-comparison-hint">Arrastra la cortina para comparar. El sonido pertenece al resultado.</p>
    {error&&<p className="cinema-comparison-message" role="alert">{error}</p>}
    {mediaError&&<button type="button" className="cinema-comparison-retry" onClick={retry}>Volver a cargar la comparación</button>}
    {!ready&&!error&&<p className="cinema-comparison-message" role="status">Cargando los dos vídeos…</p>}
  </section>;
  return <section className="result-comparison" style={{'--comparison-aspect':aspectRatio}} hidden={!active} id={'comparison-'+job.id} aria-label="Comparar original y resultado">
    <h3>Comparar original y resultado</h3>
    <div className="comparison-pair">{[[source,spec.source,spec.sourceLabel,true],[result,spec.result,spec.resultLabel||'Resultado',false]].map(([ref,url,label,muted])=><figure key={label}><figcaption>{label}</figcaption><div className="comparison-window" onClick={pick} title="Toca la zona que quieres ampliar"><video key={reload} {...videoProps(ref,url,label,muted)} style={style}/></div></figure>)}</div>
    <div className="comparison-controls"><button className="secondary" disabled={!ready||mediaError} onClick={()=>seek(frame-1)} aria-label="Fotograma anterior">−1 fotograma</button><button className="primary" disabled={!ready||mediaError} onClick={toggle}>{playing?'Pausar':'Reproducir ambos'}</button><button className="secondary" disabled={!ready||mediaError} onClick={()=>seek(frame+1)} aria-label="Fotograma siguiente">+1 fotograma</button></div>
    <label className="comparison-timeline">Fotograma {frame+1} / {spec.frames} · {(frame/24).toFixed(2)} s<input aria-label="Fotograma de comparación" type="range" min="0" max={maxFrame} step="1" value={frame} disabled={!ready||mediaError} onChange={e=>seek(Number(e.target.value))}/></label>
    {spec.boundaries?.length>0&&<details className="comparison-joins"><summary>Revisar uniones de tramos</summary><p>Cada unión se abre en el último fotograma del tramo anterior. Avanza uno para ver el comienzo del siguiente.</p><div>{spec.boundaries.map(boundary=><button type="button" key={boundary} className="secondary" disabled={!ready||mediaError} onClick={()=>seek(boundary-1)}>Unión a {(boundary/24).toFixed(2)} s</button>)}</div></details>}
    <label className="comparison-zoom">Ampliación<select value={zoom} onChange={e=>setZoom(Number(e.target.value))}><option value="1">Vista completa</option><option value="2">2×</option><option value="4">4×</option></select></label><p className="muted">Toca la zona del rostro en cualquiera de las vistas para centrar la ampliación. El audio se reproduce desde el resultado.</p>
    {error&&<p role="alert">{error}</p>}{mediaError&&<button className="secondary" onClick={retry}>Volver a cargar la comparación</button>}{!ready&&!error&&<p role="status">Cargando los dos vídeos…</p>}
    <QualityNotes key={job.id} job={job} frame={frame} onSeek={seek} onSaved={onReviewSaved}/>
  </section>;
}
