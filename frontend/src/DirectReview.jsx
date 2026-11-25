import {characterVersionLabel} from './characterVersion.js';
import React,{useState} from 'react';
export default function DirectReview({job,onUpdated,onError}){
 const [feedback,setFeedback]=useState(job.direct_review?.human_feedback||''),[sending,setSending]=useState(false);
 async function review(approved){setSending(true);try{
   const r=await fetch(`/api/jobs/${job.id}/review-direct`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({approved,feedback})});
   const d=await r.json();if(!r.ok)throw Error(d.detail||'No se pudo guardar la revisión.');onUpdated(d);
 }catch(e){onError(e.message);}finally{setSending(false);}}
 return <section className="face-summary"><h3>Revisar esta toma</h3><p>Comprueba identidad, giros, ropa, manos, fondo y movimiento. Esta toma se generó sin refinamiento facial posterior.</p><details><summary>Detalles del motor</summary><p>{characterVersionLabel(job.parameters)}</p></details>
 {job.direct_effective_prompt&&<details><summary>Prompt enviado a H3 · inglés</summary><pre style={{whiteSpace:'pre-wrap'}}>{job.direct_effective_prompt}</pre><details><summary>Instrucciones originales</summary><pre style={{whiteSpace:'pre-wrap'}}>{job.parameters.prompt}</pre></details></details>}
 {!job.direct_effective_prompt&&<details><summary>Prompt registrado</summary><pre style={{whiteSpace:'pre-wrap'}}>{job.parameters.prompt}</pre></details>}
 <label>Comentario de la toma directa<textarea aria-label="Comentario H3 directo" maxLength="1000" value={feedback} onChange={e=>setFeedback(e.target.value)}/></label>
 <div className="history-actions"><button className="secondary" disabled={sending} onClick={()=>review(true)}>Aprobar toma directa</button><button className="secondary" disabled={sending} onClick={()=>review(false)}>Marcar incidencia H3 directo</button></div>
 <p>{job.direct_review?.human_review==='approved'?'Toma directa aprobada.':job.direct_review?.human_review==='rejected'?'Incidencia registrada.':'Identidad pendiente de revisión.'}</p></section>;
}
