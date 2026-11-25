import React, { useEffect, useState } from 'react';

function reviewLabel(trial) {
  if (trial.identity_approved && trial.accessory_approved) return 'Identidad y gorra revisadas';
  if (trial.accessory_approved) return 'Gorra revisada · Identidad pendiente';
  return trial.identity_approved ? 'Identidad revisada' : 'Pendiente de revisión';
}

export default function IdentityReview({jobId}) {
  const [trials, setTrials] = useState([]);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    setTrials([]); setError('');
    if (jobId) fetch(`/api/jobs/${jobId}/identity-review`).then(async response => {
      if (!response.ok) throw Error('No se pudo consultar la prueba de revisión.');
      return response.json();
    }).then(data => {if (active) setTrials(data.trials);}).catch(e => {if (active) setError(e.message);});
    return () => {active = false;};
  }, [jobId]);
  return <div className="identity-review">{error && <p role="alert">{error}</p>}{trials.map(trial => <section key={trial.id}>
    <h3>{trial.name || 'Prueba de identidad facial'}</h3>
    <p>{trial.description || 'Corrección experimental sobre una copia. Revisa el parecido y la estabilidad del rostro en movimiento.'}</p>
    <span className="outline-tag">{reviewLabel(trial)}</span>
    <video src={trial.video_url} controls playsInline preload="metadata" aria-label={trial.name || 'Vídeo de revisión'}/>
    <details><summary>Comparar fotogramas y referencia</summary>
      <img className="identity-reference" src={trial.reference_url} alt="Primer plano del personaje de referencia"/>
      <img className="identity-comparison" src={trial.comparison_url} alt="Fotogramas: antes arriba, prueba experimental abajo"/>
      <p>Las ampliaciones ayudan a comparar detalles. Revisa también el vídeo en movimiento.</p>
    </details>
    <a className="download" href={`${trial.video_url}?download=true`}>Descargar prueba ↓</a>
  </section>)}</div>;
}
