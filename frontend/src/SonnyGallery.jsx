import React,{useEffect,useRef,useState} from 'react';
import {canReadSonnyGallery,sonnyGalleryMedia,requestSonnyGallery,mergeSonnyGalleryItems} from './sonnyGallery.js';
import './sonnyGallery.css';

function GalleryVideo({item,targetName='Sonny'}){
 const [playing,setPlaying]=useState(false);
 const [failed,setFailed]=useState(false);
 const container=useRef(null),[visible,setVisible]=useState(false);
 useEffect(()=>{
  if(typeof IntersectionObserver==='undefined'){setVisible(true);return;}
  const observer=new IntersectionObserver(entries=>{
   if(entries.some(entry=>entry.isIntersecting)){setVisible(true);observer.disconnect();}
  },{rootMargin:'150px'});
  if(container.current)observer.observe(container.current);
  return()=>observer.disconnect();
 },[]);
 return <div ref={container} className="sonny-video-preview">{playing
  ?<video controls autoPlay playsInline preload="none" src={item.url} aria-label={'Vídeo de '+targetName+' · '+item.name}/>
  :<button type="button" className="sonny-play-preview" onClick={()=>setPlaying(true)} aria-label={'Reproducir vídeo de '+targetName+' · '+item.name}>
    {visible&&!failed&&<img src={item.url.replace('/result','/thumbnail')} alt="" decoding="async" onError={()=>setFailed(true)}/>}
    <span className="sonny-play-label">▶ Reproducir</span>
   </button>}</div>;
}

export function SonnyGalleryCards({items,media,targetName='Sonny'}){
 const images=media==='images';
 return <div className="sonny-gallery-grid">{items.map(item=><article className={'panel sonny-video'+(images?' sonny-image':'')} key={item.id}>
  {images?<a className="sonny-image-preview" href={item.url} target="_blank" rel="noopener noreferrer" aria-label={'Ver imagen completa · '+item.name}><img src={item.url.replace('/result','/thumbnail')} alt={item.name} loading="lazy" decoding="async"/></a>:<GalleryVideo item={item} targetName={targetName}/>}
  <div className="sonny-video-info"><h2>{item.name}</h2><p>{!images&&item.seconds!=null?item.seconds+' s · ':''}{item.resolution?item.resolution.replace('x',' × ')+' px · ':''}{item.method}</p><time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString('es-MX',{dateStyle:'medium',timeStyle:'short'})}</time><div className="sonny-result-actions">{images&&<a className="secondary" href={item.url} target="_blank" rel="noopener noreferrer">Ver imagen completa ↗</a>}<a className="secondary" href={item.url+'?download=true'}>Descargar {images?'imagen':'vídeo'} ↓</a></div></div>
 </article>)}</div>;
}

function OwnerGallery({workspaceId,media,targetId,targetName}){
 const images=media==='images',label=images?'imágenes':'vídeos';
 const [items,setItems]=useState([]),[next,setNext]=useState(null),[error,setError]=useState(''),[loading,setLoading]=useState(true),[request,setRequest]=useState({before:null,revision:0});
 const lock=useRef(true);
 useEffect(()=>{
  const controller=new AbortController();lock.current=true;setLoading(true);setError('');
  requestSonnyGallery(media,{workspaceId,targetId,before:request.before,signal:controller.signal})
   .then(data=>{if(controller.signal.aborted)return;setItems(current=>mergeSonnyGalleryItems(current,data.items,Boolean(request.before)));setNext(data.next_before);})
   .catch(e=>{if(!controller.signal.aborted)setError(e.name==='AbortError'?'Gen Studio no respondió a tiempo. Vuelve a intentar.':e.message);})
   .finally(()=>{if(!controller.signal.aborted){lock.current=false;setLoading(false);}});
  return()=>controller.abort();
 },[workspaceId,media,targetId,request]);
 function load(before=null){if(lock.current)return;lock.current=true;setLoading(true);setRequest(current=>({before,revision:current.revision+1}));}
 return <>
  <div className="sonny-gallery-heading"><div><p className="tiny">GEN STUDIO</p><h1>{images?'Imágenes':'Vídeos'} de {targetName}</h1><p>Resultados de su espacio de trabajo. Acceso de sólo lectura.</p></div><button className="secondary" disabled={loading} onClick={()=>load()}>Actualizar</button></div>
  <nav className="sonny-gallery-tabs" aria-label={'Resultados de '+targetName}><a href={'/?view='+targetId} aria-current={!images?'page':undefined}>Vídeos</a><a href={'/?view='+targetId+'&media=images'} aria-current={images?'page':undefined}>Imágenes</a></nav>
  {error&&<div className="notice error" role="alert"><p>{error}</p><button className="secondary" disabled={loading} onClick={()=>load(request.before)}>Reintentar</button></div>}
  {loading&&<p role="status">Cargando {label}…</p>}
  {!loading&&!error&&!items.length&&<section className="panel sonny-gallery-empty"><h2>Aún no hay {label} {images?'generadas':'generados'}</h2><p>Sus resultados aparecerán aquí cuando estén disponibles.</p></section>}
  <SonnyGalleryCards items={items} media={media} targetName={targetName}/>
  {next&&!error&&<button className="secondary sonny-load-more" disabled={loading} onClick={()=>load(next)}>Cargar más {label}</button>}
 </>;
}

export default function SonnyGallery({workspace,media:requestedMedia,targetId='sonny',targetName='Sonny'}){
 const media=sonnyGalleryMedia(requestedMedia),owner=canReadSonnyGallery(workspace?.id);
 return <div className="app sonny-gallery"><main><header><a href={media==='images'?'/?page=image-history':'/?page=history'}>← Volver a mi espacio</a><span className="badge">SOLO LECTURA</span></header><div className="sonny-gallery-content">
  {owner?<OwnerGallery key={targetId+':'+media} workspaceId={workspace.id} media={media} targetId={targetId} targetName={targetName}/>:<p className="notice" role="alert">Este acceso está reservado para la cuenta del propietario.</p>}
 </div></main></div>;
}
