import React,{useEffect,useRef,useState} from 'react';
import {NOTE_CATEGORIES,categoryLabel,segmentAtFrame,noteBody} from './qualityNotes.js';

async function request(path,options){
  const r=await fetch(path,options),d=await r.json();
  if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:'No se pudo guardar la marca.');
  return d;
}
export default function QualityNotes({job,frame,onSeek,onSaved}){
  const [data,setData]=useState(null),[error,setError]=useState(''),[saving,setSaving]=useState(false);
  const [draft,setDraft]=useState(null),[resolved,setResolved]=useState(false),[notice,setNotice]=useState('');
  const generation=useRef(0),base='/api/jobs/'+job.id+'/quality-notes';
  useEffect(()=>{
    const token=++generation.current;
    request(base).then(d=>{if(generation.current===token)setData(d);})
      .catch(e=>{if(generation.current===token)setError(e.message);});
    return()=>{generation.current++;};
  },[base]);
  async function refresh(){
    setError('');setSaving(true);
    try{const d=await request(base);setData(d);onSaved?.(d.review);}
    catch(e){setError(e.message);}finally{setSaving(false);}
  }
  function mark(){
    onSeek(frame);setNotice('');
    setDraft({id:crypto.randomUUID(),frame,category:'face',subject:'',comment:'',status:'open'});
  }
  async function save(note){
    setError('');setSaving(true);
    try{
      const d=await request(base+'/'+note.id,{method:'PUT',headers:{'Content-Type':'application/json'},
        body:JSON.stringify(noteBody(data.review,note))});
      setData(d);onSaved?.(d.review);setNotice(note.status==='resolved'?'Marca resuelta.':'Marca guardada en esta versión.');
      if(draft?.id===note.id)setDraft(null);
    }catch(e){setError(e.message);}finally{setSaving(false);}
  }
  const notes=data?.review.notes||[],open=notes.filter(n=>n.status==='open');
  return <section className="quality-notes" aria-label="Marcas de revisión">
    <h4>Revisar detalles del vídeo</h4>
    <p>Marca el momento exacto donde falla el rostro, la ropa, las manos, el fondo o el movimiento. Las marcas se guardan sólo en esta versión. Los tiempos se cuentan desde el inicio del resultado.</p>
    <button type="button" className="secondary" disabled={!data||saving||Boolean(draft)} onClick={mark}>Marcar este momento · {(frame/24).toFixed(2)} s</button>
    {draft&&<form onSubmit={e=>{e.preventDefault();save(draft);}} className="quality-note-editor">
      <strong>Marca en {(draft.frame/24).toFixed(2)} s · Fotograma {draft.frame+1}</strong>
      <label>Qué falla<select aria-label="Tipo de detalle a revisar" disabled={saving} value={draft.category} onChange={e=>setDraft(d=>({...d,category:e.target.value}))}>{NOTE_CATEGORIES.map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>
      <label>Persona o elemento<input aria-label="Persona o elemento de la marca" maxLength={180} disabled={saving} value={draft.subject} placeholder="Ej.: hombre de camiseta turquesa" onChange={e=>setDraft(d=>({...d,subject:e.target.value}))}/></label>
      <label>Qué observaste<textarea aria-label="Observación de este momento" maxLength={1000} disabled={saving} value={draft.comment} placeholder="Ej.: pierde identidad al ponerse de perfil" onChange={e=>setDraft(d=>({...d,comment:e.target.value}))}/></label>
      <div className="history-actions"><button className="primary" disabled={saving} type="submit">{saving?'Guardando…':'Guardar marca'}</button><button className="secondary" disabled={saving} type="button" onClick={()=>setDraft(null)}>Descartar marca sin guardar</button></div>
    </form>}
    {data&&<p role="status">{open.length?open.length+' marca'+(open.length===1?' pendiente':'s pendientes'):'Sin marcas pendientes.'}</p>}
    {notes.some(n=>n.status==='resolved')&&<label className="quality-resolved"><input type="checkbox" checked={resolved} onChange={e=>setResolved(e.target.checked)}/>Mostrar marcas resueltas</label>}
    <div className="quality-note-list">{notes.filter(n=>resolved||n.status==='open').map(n=>{
      const segment=segmentAtFrame(data?.segments,n.frame);
      return <article key={n.id} className={'quality-note '+(n.status==='resolved'?'quality-note-resolved':'')}>
        <button type="button" className="secondary" onClick={()=>onSeek(n.frame)}>Ir a {(n.frame/24).toFixed(2)} s · {categoryLabel(n.category)}</button>
        {n.subject&&<strong>{n.subject}</strong>}{n.comment&&<p>{n.comment}</p>}
        {segment&&<p>{segment.passthrough?'Tramo original sin generación':'Tramo '+(segment.index+1)}{!segment.passthrough&&<> · <a href={'#long-segment-'+job.id+'-'+segment.index}>Revisar y rehacer este tramo</a></>}</p>}
        <button type="button" className="secondary" disabled={saving||Boolean(draft)} onClick={()=>{onSeek(n.frame);setDraft({...n});setNotice('');}}>Editar marca</button>
        <button type="button" className="secondary" disabled={saving} onClick={()=>save({...n,status:n.status==='resolved'?'open':'resolved'})}>{n.status==='resolved'?'Volver a marcar pendiente':'Marcar como resuelta'}</button>
      </article>;
    })}</div>
    <p className="muted">Las marcas no corrigen ni regeneran el vídeo automáticamente. Resuelve una marca cuando hayas comprobado ese detalle.</p>
    {notice&&<p role="status">{notice}</p>}{error&&<p role="alert">{error}</p>}
    {(error||data)&&<button type="button" className="secondary" disabled={saving} onClick={refresh}>Actualizar marcas</button>}
  </section>;
}
