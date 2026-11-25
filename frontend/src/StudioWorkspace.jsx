import React,{createContext,useContext,useEffect,useId,useLayoutEffect,useMemo,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import {useSwapStep} from './ObjectSwapGuide.jsx';

const Workspace=createContext(null);
export const useStudioWorkspace=()=>useContext(Workspace);

// React owns the controls throughout. Moving a section never copies its values,
// handlers or request keys. Native form ownership and ancestor locks are retained.
export function StudioSlot({name,children}){
 const ctx=useContext(Workspace),marker=useRef(null),body=useRef(null),id=useId();
 const [inherited,setInherited]=useState({hidden:false,disabled:false,form:''});
 const step=useSwapStep();
 const inlineInGuide=step&&(name==='direction'||name==='actions');
 const target=!inlineInGuide&&ctx?.active&&ctx.hosts[name];
 useLayoutEffect(()=>{
  if(!target||!marker.current)return;
  const ancestors=[];for(let p=marker.current.parentElement;p;p=p.parentElement)ancestors.push(p);
  function sync(){
   const owner=ancestors.find(p=>p.tagName==='FORM');if(owner&&!owner.id)owner.id='studio-form-'+id.replace(/[^a-z0-9]/gi,'');
   const next={hidden:ancestors.some(p=>p.hidden),disabled:ancestors.some(p=>p.tagName==='FIELDSET'&&p.disabled),form:owner?.id||''};
   setInherited(old=>Object.keys(next).every(k=>next[k]===old[k])?old:next);
  }
  sync();const observer=new MutationObserver(sync);ancestors.forEach(p=>observer.observe(p,{attributes:true,attributeFilter:['hidden','disabled']}));
  return()=>observer.disconnect();
 },[target,id]);
 useLayoutEffect(()=>{
  if(!inherited.form||!body.current)return;
  const sync=()=>body.current.querySelectorAll('input,select,textarea,button').forEach(el=>el.setAttribute('form',inherited.form));
  sync();const observer=new MutationObserver(sync);observer.observe(body.current,{childList:true,subtree:true});return()=>observer.disconnect();
 },[inherited.form,target,children]);
 if(!target)return children;
 return <><span ref={marker} className="hg-slot-marker" aria-hidden="true"/>{createPortal(<fieldset ref={body} className={'hg-slot hg-slot-'+name} hidden={inherited.hidden||(name==='result'&&!ctx.job)} disabled={inherited.disabled}>{children}</fieldset>,target)}</>;
}

export function ModeIcon({name}){return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{name==='character'?<><path d="M4 7h15m-4-4 4 4-4 4M20 17H5m4-4-4 4 4 4"/></>:name==='upscale'?<><path d="M4 10V4h6M14 4h6v6M20 14v6h-6M10 20H4v-6M9 12h6m-3-3v6"/></>:name==='motion'?<path d="m2 13 5-2 3-7 4 16 3-8 5-1"/>:<><rect x="3" y="4" width="18" height="16" rx="3"/><path d="m10 9 5 3-5 3Z"/></>}</svg>;}

export default function StudioWorkspace({active,mode,job,busy,onNew,children}){
 const [hosts,setHosts]=useState({}),[source,setSource]=useState(null),[assistant,setAssistant]=useState(false),[confirm,setConfirm]=useState(false);
 const controls=useRef(null),dialog=useRef(null),newButton=useRef(null);
 const refs=useMemo(()=>Object.fromEntries(['title','result','upscaling','direction','actions','sequence','assistant','recent','bottom'].map(name=>[name,node=>setHosts(h=>h[name]===node?h:{...h,[name]:node})])),[]);
 const value=useMemo(()=>({active,hosts,job}),[active,hosts,job]);
 const upscale=mode==='video_upscale',motion=mode==='motion',imageMode=mode==='image_video',label=upscale?'Video Upscaling':imageMode?'Imagen a vídeo':motion?'Motion Transfer':'Object Swap';
 useEffect(()=>{
  if(!active)return;let timer;
  function refresh(){
   clearTimeout(timer);timer=setTimeout(()=>{
    const media=[...controls.current.querySelectorAll('[data-studio-section="source"] video[src], [data-studio-section="source"] img[src]')].find(el=>el.closest('.swap-guide.is-guided')||!el.closest('[hidden]'));
    const next=media?{src:media.getAttribute('src'),kind:media.tagName==='VIDEO'?'video':'image'}:null;
    setSource(old=>old?.src===next?.src&&old?.kind===next?.kind?old:next);
   },0);
  }
  refresh();const observer=new MutationObserver(refresh);observer.observe(controls.current,{childList:true,subtree:true,attributes:true,attributeFilter:['src','hidden']});
  return()=>{clearTimeout(timer);observer.disconnect();};
 },[active,mode]);
 useEffect(()=>{setAssistant(Boolean(job?.qwen_waiting));},[job?.id,job?.qwen_waiting]);
 useEffect(()=>{if(confirm)dialog.current?.showModal();else if(dialog.current?.open)dialog.current.close();},[confirm]);
 function finishDialog(){setConfirm(false);newButton.current?.focus();}
 function addSource(){const input=[...controls.current.querySelectorAll('input[type="file"]')].find(el=>!el.disabled&&!el.closest('[hidden]')&&(imageMode?el.accept.includes('image'):/(video|mp4)/.test(el.accept)));input?.click();}
 return <Workspace.Provider value={value}><div className={active?'hg-workspace':'hg-outside'}>
  <div className="hg-controls" ref={controls}>
   {active&&<div className="hg-create-heading"><div className="hg-create-top"><span>CREATE</span><button ref={newButton} className="hg-new" disabled={busy} onClick={()=>setConfirm(true)} title="Empezar con un estudio vacío">↻ New</button></div><h1>{upscale?'Más detalle.':imageMode?'Dale vida a tu imagen.':motion?'Conserva el movimiento.':'Conserva la escena.'}<span>{upscale?'El original, intacto.':imageMode?'Imagina lo que sigue.':motion?'Crea otra realidad.':'Cambia el personaje.'}</span></h1></div>}
   {children}
  </div>
  {active&&<div className="hg-canvas-column">
   <div className="hg-canvas-toolbar"><span><i/>Workspace <small>/ {label}</small></span><small>{job?job.status==='completed'?'TOMA GENERADA':'TU TOMA':source?'VISTA PREVIA':'ESPERANDO UNA REFERENCIA'}</small></div>
   <div ref={refs.title} className="hg-title-slot"/>
   {!upscale&&!job?.result_available&&<section className={'hg-stage'+(source?' has-source':'')} aria-label="Vista previa del estudio">
    {source?source.kind==='video'?<video key={source.src} src={source.src} controls playsInline preload="metadata" aria-label="Vista previa del vídeo guía"/>:<img src={source.src} alt="Imagen inicial de la toma"/>:
    <div className="hg-stage-message"><span>SWAP, MOTION, TRANSFORM</span><h2>{imageMode?'Una imagen.':motion?'El mismo movimiento.':'Una escena familiar.'}<em>{imageMode?'Todo por imaginar.':motion?'Un mundo diferente.':'Alguien nuevo en ella.'}</em></h2><p>{imageMode?'Añade una imagen y describe cómo se mueve.':'Añade un vídeo guía y las imágenes de quien aparecerá.'}</p><button onClick={addSource} disabled={busy}><b>＋</b>{imageMode?'Añadir imagen inicial':'Añadir vídeo guía'}</button></div>}
    <small className="hg-stage-corner">GEN STUDIO / {label}</small>
   </section>}
   <div ref={refs.result} className="hg-result-slot"/><div ref={refs.upscaling} className="hg-upscaling-slot"/>
   <div ref={refs.sequence} className="hg-sequence-slot"/>
   {!upscale&&<section className="hg-direction-card" aria-label="Dirige la escena"><div className="hg-direction-heading"><h2><span>✳</span> Dirige la escena</h2><span className="hg-qwen-badge">Qwen automático</span></div><div ref={refs.direction} className="hg-direction-slot"/><div ref={refs.actions} className="hg-actions-slot"/><p className="hg-direction-note">El asistente analiza tus referencias y prepara las indicaciones. Puedes conversar con Qwen desde la toma.</p></section>}
   <div ref={refs.recent} className="hg-recent-slot"/>
   <div ref={refs.bottom} className="hg-bottom-slot"/>
  </div>}
  {active&&<>{!upscale&&<><button className={'hg-assistant-toggle'+(job?.qwen_waiting?' needs-reply':'')} aria-expanded={assistant} aria-controls="hg-assistant" onClick={()=>setAssistant(v=>!v)}>✦ {job?.qwen_waiting?'Qwen necesita tu respuesta':'Asistente Qwen'}</button><aside id="hg-assistant" className="hg-assistant-drawer" hidden={!assistant} aria-label="Asistente de esta toma"><div className="hg-assistant-header"><strong>Tu asistente de creación</strong><button aria-label="Cerrar asistente" onClick={()=>setAssistant(false)}>×</button></div><div ref={refs.assistant}/></aside></>}
  <dialog ref={dialog} className="hg-new-dialog" aria-labelledby="new-studio-title" onCancel={e=>{e.preventDefault();finishDialog();}}><span className="hg-dialog-symbol">↻</span><h2 id="new-studio-title">¿Empezar un vídeo nuevo?</h2><p>El estudio quedará sin vídeo, referencias ni indicaciones. Se mantienen el método y los ajustes de calidad. Tus generaciones siguen en el historial.</p><div><button className="secondary" onClick={finishDialog}>Seguir editando</button><button className="primary" disabled={busy} onClick={()=>{finishDialog();onNew();}}>Empezar nuevo</button></div></dialog></>}
 </div></Workspace.Provider>;
}
