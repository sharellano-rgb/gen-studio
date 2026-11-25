export const guideStorageKey = scope => 'object-swap-guide-v1:' + scope;

export function readGuide(storage, scope, sourceId) {
  try {
    const saved = JSON.parse(storage.getItem(guideStorageKey(scope)));
    if (saved && saved.sourceId === sourceId && typeof saved.step === 'string') {
      return {step: saved.step, manual: saved.manual === true};
    }
  } catch {}
  return {step: 'source', manual: false};
}

// Recheck earlier inputs when returning from the full editor or reopening a draft.
export function reachableStep(steps, requested) {
  const wanted = Math.max(0, steps.findIndex(step => step.id === requested || step.previousIds?.includes(requested)));
  const missing = steps.findIndex(step => step.issue);
  return missing < 0 ? wanted : Math.min(wanted, missing);
}

export function sourceStep(video, intervalIssue = '') {
  return {id: 'source', summary: video?.name, issue: video ? intervalIssue : 'Selecciona o sube el vídeo original para continuar.'};
}

export function referenceStep(image, extra = {}) {
  return {id: 'references', summary: image?.name, ...extra,
    issue: image ? extra.issue : 'Añade una imagen del personaje que aparecerá en el vídeo.'};
}

export function intervalSummary(start, frames) {
  const seconds = value => Number(value).toLocaleString('es-MX', {maximumFractionDigits: 2});
  return seconds(start) + '–' + seconds(Number(start) + frames / 24) + ' s';
}

export function sourceWindowStep(video, start, frames, remaining) {
  const issue = !Number.isFinite(start) || start < 0 || !Number.isInteger(frames) || frames < 5 || frames > remaining
    ? 'Ajusta el inicio y la duración al vídeo, o usa todo el intervalo disponible.' : '';
  return {...sourceStep(video, issue), help: 'Elige el vídeo original y la parte que quieres transformar.',
    summary: video && video.name + ' · ' + intervalSummary(start, frames)};
}
