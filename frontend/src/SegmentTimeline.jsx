import React,{useEffect,useRef,useState} from 'react';
import {segmentStatus,segmentThumbnailUrl} from './segmentTimeline.js';
import {openNotesForSegment} from './qualityNotes.js';
import './segmentTimeline.css';
function Thumbnail({url,index}){
 const holder=useRef(null),[visible,setVisible]=useState(false),[failed,setFailed]=useState(false),[loaded,setLoaded]=useState(false);
 useEffect(()=>{
  if(!url||visible)return;
  if(typeof IntersectionObserver==='undefined'){setVisible(true);return;}
  const observer=new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting)){setVisible(true);observer.disconnect();}},{root:holder.current?.closest('.segment-strip'),rootMargin:'0px 80px'});
  if(holder.current)observer.observe(holder.current);return()=>observer.disconnect();
 },[url,visible]);
 return <span className="segment-thumbnail" ref={holder}>{visible&&url&&!failed&&<img src={url} alt="" loading="lazy" decoding="async" onLoad={()=>setLoaded(true)} onError={()=>setFailed(true)}/>}<span className="segment-thumbnail-placeholder" hidden={loaded&&!failed}>{failed||!url?'Sin miniatura':visible?'Cargando…':'Tramo '+(index+1)}</span></span>;
}
export default function SegmentTimeline({segments,frames,selected,onSelect,notes=[],cuts=[],videoId,startSeconds=0,affected=[]}){
 const cards=useRef(new Map()),position=segments.findIndex(s=>s.index===selected);
 useEffect(()=>{const card=cards.current.get(selected),strip=card?.closest('.segment-strip');if(!card||!strip)return;const c=card.getBoundingClientRect(),r=strip.getBoundingClientRect();if(c.left<r.left||c.right>r.right)strip.scrollTo({left:strip.scrollLeft+c.left-r.left-(r.width-c.width)/2,behavior:'auto'});},[selected]);
 const open=notes.filter(n=>n.status==='open'&&Number.isInteger(n.frame)&&n.frame>=0&&n.frame<frames);
 function choose(index){onSelect(index);cards.current.get(index)?.scrollIntoView({behavior:'auto',block:'nearest',inline:'center'});}
 return <section className="segment-timeline" aria-label="Línea de tiempo de tramos">
  <div className="segment-timeline-heading"><h4>Línea de tiempo</h4><span>{segments.length} tramos · {(frames/24).toFixed(2)} s{open.length?' · '+open.length+' marcas pendientes':''}</span></div>
  <p>Miniaturas del vídeo original. Los tiempos se cuentan desde el inicio de este resultado.</p>
  <div className="segment-rail" aria-hidden="true">{segments.map(s=><span key={s.index} className={'segment-rail-block '+segmentStatus(s).kind} data-selected={s.index===selected} data-affected={affected.includes(s.index)} style={{width:100*s.keep/frames+'%'}}/>)}{open.map(n=><i key={n.id} className="segment-rail-note" style={{left:100*n.frame/frames+'%'}}/>)}{cuts.filter(f=>f>0&&f<frames).map(f=><i key={f} className="segment-rail-cut" style={{left:100*f/frames+'%'}}/>)}</div>
  <div className="segment-rail-times" aria-hidden="true"><span>0.00 s</span><span>{(frames/24).toFixed(2)} s</span></div>
  <div className="segment-strip" role="group" aria-label="Seleccionar tramo">{segments.map(s=>{
   const status=segmentStatus(s),count=openNotesForSegment(open,s).length,url=segmentThumbnailUrl(videoId,s,startSeconds);
   return <button type="button" key={s.index} ref={node=>{if(node)cards.current.set(s.index,node);else cards.current.delete(s.index);}} className={'segment-card '+status.kind} data-affected={affected.includes(s.index)} aria-pressed={s.index===selected} aria-label={'Seleccionar tramo '+(s.index+1)+' · '+(s.start/24).toFixed(2)+' a '+((s.start+s.keep)/24).toFixed(2)+' segundos · '+status.label+(count?' · '+count+(count===1?' marca pendiente':' marcas pendientes'):'')} onClick={()=>choose(s.index)}>
    <Thumbnail key={url||s.index} url={url} index={s.index}/><strong>Tramo {s.index+1}</strong><span>{(s.start/24).toFixed(2)}–{((s.start+s.keep)/24).toFixed(2)} s</span><span className="segment-card-status">{status.label}</span>{count>0&&<span className="segment-card-notes">{count} {count===1?'marca':'marcas'}</span>}{s.start>0&&<span className="segment-card-boundary">{cuts.includes(s.start)?'Corte al inicio':'Unión al inicio'}</span>}{affected.includes(s.index)&&<span className="segment-card-impact">Al regenerar</span>}
   </button>;
  })}</div>
  <div className="segment-navigation"><button type="button" className="secondary" disabled={position<=0} onClick={()=>choose(segments[position-1].index)}>← Anterior</button><label>Ir al tramo<select aria-label="Tramo seleccionado" value={selected} onChange={e=>choose(Number(e.target.value))}>{segments.map(s=><option key={s.index} value={s.index}>Tramo {s.index+1} de {segments.length}</option>)}</select></label><button type="button" className="secondary" disabled={position<0||position>=segments.length-1} onClick={()=>choose(segments[position+1].index)}>Siguiente →</button></div>
 </section>;
}
