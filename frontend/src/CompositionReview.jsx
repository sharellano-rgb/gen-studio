import React,{useState} from 'react';
import {reviewText} from './guidedDraft';

export default function CompositionReview({job,onUpdated,onError}) {
  const [feedback,setFeedback]=useState(reviewText(job.face_review));
  const [sending,setSending]=useState(false);
  async function review(approved) {
    setSending(true);
    try {
      const response=await fetch(`/api/jobs/${job.id}/review-guided-composition`,{method:'POST',
        headers:{'Content-Type':'application/json'},body:JSON.stringify({approved,feedback})});
      const result=await response.json();
      if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'No se pudo guardar la revisión de la escena.');
      onUpdated(result);
    } catch(error){onError(error.message);} finally{setSending(false);}
  }
  return <section className="face-summary composition-review"><h3>Revisar la escena completa</h3>
    <p>Comprueba rostro, piel, gorra o sombrero y la unión con el cuerpo durante toda la toma. Revisa también los cambios de tamaño en los giros.</p>
    <label>Comentario de la revisión<textarea aria-label="Comentario de la revisión de escena" maxLength={1000} value={feedback} onChange={event=>setFeedback(event.target.value)}/></label>
    <div className="history-actions"><button className="secondary" disabled={sending} onClick={()=>review(true)}>Aprobar escena completa</button>
      <button className="secondary" disabled={sending} onClick={()=>review(false)}>Marcar incidencia</button></div>
    <p role="status">{job.face_review?.identity_approved?'Escena aprobada. Revisión guardada.':job.face_review?.human_review==='rejected'?'Incidencia registrada. La copia se conserva para revisar.':'Escena pendiente de tu revisión.'}</p>
  </section>;
}
