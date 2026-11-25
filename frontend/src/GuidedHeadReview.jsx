import React,{useState} from 'react';

export default function GuidedHeadReview({job,onUpdated,onError}) {
  const [sending,setSending]=useState(false);
  async function review(approved) {
    setSending(true);
    try {
      const response=await fetch(`/api/jobs/${job.id}/review-guided-head`,{method:'POST',
        headers:{'Content-Type':'application/json'},body:JSON.stringify({approved})});
      const result=await response.json();
      if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'No se pudo guardar la revisión.');
      onUpdated(result);
    } catch(error){onError(error.message);} finally{setSending(false);}
  }
  return <section className="face-summary guided-head-review"><h3>Revisar el detalle generado</h3>
    <p>Comprueba identidad, piel, orientación del rostro y accesorios durante toda la toma. Aprobar habilita este detalle para la composición estable de su escena.</p>
    <details><summary>Ver foto real y comparar fotogramas</summary>
      <img className="guided-head-reference" src={`/api/assets/${job.parameters.face_reference_id}`} alt="Foto real para revisar la identidad facial"/>
      <a className="download" href={`/api/assets/${job.parameters.face_reference_id}`} target="_blank" rel="noopener noreferrer">Abrir foto real para ampliar ↗</a>
      <img className="guided-head-comparison" src={`/api/identity-tests/${job.guided_candidate}/comparison`} alt="Referencia de movimiento arriba y detalle H3 generado abajo"/>
      <a className="download" href={`/api/identity-tests/${job.guided_candidate}/comparison`} target="_blank" rel="noopener noreferrer">Abrir fotogramas para ampliar ↗</a>
    </details>
    <p>{job.head_review?.identity_approved?'Detalle aprobado para componer.':'Detalle pendiente de aprobación.'}</p>
    <div className="history-actions"><button className="secondary" disabled={sending} onClick={()=>review(true)}>Aprobar rostro y accesorios</button>
      <button className="secondary" disabled={sending} onClick={()=>review(false)}>Rechazar detalle</button></div>
  </section>;
}
