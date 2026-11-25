const params = new URLSearchParams(window.location.search);
const validTrial = value => typeof value === 'string' && /^[a-z0-9-]{1,80}$/.test(value);
const trial = params.get('trial');
const comparison = params.get('compare');
const video = document.querySelector('#video');
const status = document.querySelector('#status');
const play = document.querySelector('#play');
const open = document.querySelector('#open');
const download = document.querySelector('#download');
const candidateButton = document.querySelector('#candidate');
const comparisonButton = document.querySelector('#comparison');
const description = document.querySelector('#description');

function selectVideo(id, isComparison = false) {
  // Only fixed GEN STUDIO artifacts: query parameters never supply URLs or paths.
  const url = `/api/identity-tests/${id}/video`;
  video.src = url;
  open.href = url;
  download.href = `${url}?download=true`;
  open.hidden = false;
  download.hidden = false;
  play.disabled = false;
  candidateButton.setAttribute('aria-pressed', String(!isComparison));
  comparisonButton.setAttribute('aria-pressed', String(isComparison));
  description.textContent = isComparison
    ? 'Comparación: vídeo fuente a la izquierda y resultado a la derecha.'
    : 'Resultado generado. Puedes reproducir o descargar esta copia.';
  status.textContent = 'Cargando vídeo…';
}

if (!validTrial(trial)) {
  status.textContent = 'El enlace de revisión no es válido. Vuelve a GEN STUDIO o utiliza el enlace completo de la prueba.';
} else {
  selectVideo(trial);
  if (validTrial(comparison)) {
    document.querySelector('nav').hidden = false;
    candidateButton.addEventListener('click', () => selectVideo(trial));
    comparisonButton.addEventListener('click', () => selectVideo(comparison, true));
  }
}

play.addEventListener('click', async () => {
  try {
    await video.play();
  } catch {
    status.textContent = 'No se pudo iniciar la reproducción. Prueba Abrir MP4 o Descargar MP4 y comprueba que Tailscale está activo.';
  }
});
video.addEventListener('loadedmetadata', () => {
  status.textContent = `Listo · ${video.duration.toFixed(2)} segundos. Pulsa Reproducir.`;
});
video.addEventListener('playing', () => { status.textContent = 'Reproduciendo…'; });
video.addEventListener('waiting', () => { status.textContent = 'Cargando vídeo…'; });
video.addEventListener('ended', () => { status.textContent = 'Reproducción completa. Puedes volver a pulsar Reproducir.'; });
video.addEventListener('error', () => {
  status.textContent = 'No se pudo cargar el vídeo. Prueba Abrir MP4 o Descargar MP4 y comprueba que Tailscale está activo.';
});
