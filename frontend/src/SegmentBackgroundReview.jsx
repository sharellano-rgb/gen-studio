import React, {useEffect, useState} from 'react';

export function backgroundVersion(job) {
  if (job?.background_review_sha256) return job.background_review_sha256;
  const p = job?.parameters;
  if (p?.engine === 'h3_direct' && p.sequence_parent_id && !p.operation && p.direct_edit_policy?.background === 'source') {
    return job.rejected_preview_sha256 || (job.result_available && job.direct_result_sha256);
  }
  return null;
}

export default function SegmentBackgroundReview({jobId, sha}) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setReport(null);
    setError('');
    async function inspect() {
      try {
        const response = await fetch(`/api/jobs/${jobId}/segment-background-review?sha256=${encodeURIComponent(sha)}`,
          {method: 'POST', signal: controller.signal});
        if (!response.ok) throw Error(response.status === 409 ? 'No se pudo verificar esta versión del tramo.' : 'Análisis de fondo no disponible.');
        const data = await response.json();
        if (data.sha256 !== sha) throw Error('El análisis corresponde a otra versión.');
        if (!controller.signal.aborted) setReport(data);
      } catch (e) {
        if (!controller.signal.aborted) setError(e.message);
      }
    }
    inspect();
    return () => controller.abort();
  }, [jobId, sha, attempt]);

  let message = 'Comprobando el fondo…';
  if (report?.flagged_frames) {
    const time = (report.first_flagged_frame / 24).toLocaleString('es-MX', {maximumFractionDigits: 2});
    message = `Posible oscurecimiento del fondo desde ${time} s del tramo. Revisa esa parte del vídeo.`;
  } else if (report && report.insufficient_frames === report.frames) {
    message = 'Las zonas visibles o iluminadas son insuficientes para evaluar el fondo.';
  } else if (report) {
    message = 'Sin indicios de oscurecimiento en las zonas evaluadas.';
  }
  return <div aria-label="Revisión de fondo">
    <p role="status">{error || message}</p>
    {report?.insufficient_frames > 0 && report.insufficient_frames < report.frames &&
      <small>Fondo sin evaluar en {report.insufficient_frames} de {report.frames} fotogramas.</small>}
    {error && <button type="button" className="secondary" onClick={() => setAttempt(n => n + 1)}>Reintentar análisis de fondo</button>}
  </div>;
}
