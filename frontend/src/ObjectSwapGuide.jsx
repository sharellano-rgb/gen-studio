import React, {createContext, useContext, useLayoutEffect, useRef, useState} from 'react';
import {workspaceLocalStorage} from './workspaceStorage.js';
import {guideStorageKey, readGuide, reachableStep} from './objectSwapGuide.js';
import './objectSwapGuide.css';

const Guide = createContext(undefined);
export const useSwapStep = () => useContext(Guide);
export const matchesSwapStep = (current, steps) => !current || !steps || (Array.isArray(steps) ? steps.includes(current) : steps === current);

const labels = {
  source: ['Vídeo original', 'Sube el vídeo que quieres transformar o elígelo de tu biblioteca.'],
  cast: ['Personajes que vas a reemplazar', 'Elige una ficha guardada o nombra al reemplazo. Describe a quién sustituye en el vídeo.'],
  references: ['Imágenes de referencia', 'Elige o sube la imagen que define la apariencia del reemplazo.'],
  scene: ['Cambios en la escena', 'Indica qué debe cambiar y qué quieres conservar del original.'],
  quality: ['Duración y calidad', 'Revisa el intervalo y los ajustes de generación.'],
  targets: ['Personas y planos', 'Comprueba a quién corresponde cada referencia en el vídeo original.'],
  review: ['Revisar y generar', 'Comprueba tu selección antes de enviar el vídeo a generación.'],
};

export function SwapStage({step, children}) {
  const current = useSwapStep();
  if (current === undefined) return children;
  return <div className="swap-stage" hidden={!matchesSwapStep(current, step)}>{children}</div>;
}

export default function ObjectSwapGuide({enabled, children, ...props}) {
  if (!enabled) return children;
  return <GuidedEditor key={props.scope} {...props}>{children}</GuidedEditor>;
}

function OriginalPreview({sourceId}) {
  const [open, setOpen] = useState(false);
  return <details className="swap-guide-original" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>Ver vídeo original</summary>
    {open && <video src={'/api/assets/' + sourceId} controls playsInline preload="metadata" aria-label="Vídeo original para consultar"/>}
  </details>;
}

function GuidedEditor({scope, sourceId, steps, busy, recovery, storage = workspaceLocalStorage, children}) {
  const [position, setPosition] = useState(() => readGuide(storage, scope, sourceId));
  const [saveError, setSaveError] = useState(false);
  const heading = useRef(null), moveFocus = useRef(false), previousSource = useRef(sourceId);
  const guided = !position.manual && !recovery;
  const index = reachableStep(steps, position.step), current = steps[index];
  const titleOf = step => step.title || labels[step.id][0];

  function remember(step, manual) {
    setPosition({step, manual});
    try {
      storage.setItem(guideStorageKey(scope), JSON.stringify({sourceId, step, manual}));
      setSaveError(false);
    } catch { setSaveError(true); }
  }
  function navigate(step, manual = false) {
    moveFocus.current = !manual;
    remember(step, manual);
  }
  useLayoutEffect(() => {
    if (previousSource.current !== sourceId) {
      previousSource.current = sourceId;
      remember('source', position.manual);
    }
  }, [sourceId]);
  useLayoutEffect(() => {
    if (guided && moveFocus.current && heading.current) {
      moveFocus.current = false;
      heading.current.focus({preventScroll: true});
      heading.current.scrollIntoView({block: 'nearest'});
    }
  }, [index, guided]);
  function guardSubmit(event) {
    if (guided && current.id !== 'review') {
      event.preventDefault();
      event.stopPropagation();
    }
  }
  return <Guide.Provider value={guided ? current.id : null}>
    <div className={'swap-guide' + (guided ? ' is-guided' : '')} onSubmitCapture={guardSubmit}
      onChangeCapture={() => { if (guided && current.id !== position.step) remember(current.id, false); }}>
      {!recovery && <div className="swap-guide-bar">
        <span>{guided ? 'Paso ' + (index + 1) + ' de ' + steps.length : 'Object Swap'}</span>
        <button type="button" disabled={busy} onClick={() => navigate(current.id, guided)}>{guided ? 'Ver editor completo' : 'Abrir guía paso a paso'}</button>
      </div>}
      {guided && <div className="swap-guide-heading">
        <progress aria-label="Progreso de la guía de Object Swap" max={steps.length} value={index + 1}/>
        <h2 ref={heading} tabIndex={-1}>{titleOf(current)}</h2>
        <p>{current.help || labels[current.id][1]}</p>
      </div>}
      {guided && sourceId && ['cast','references','targets','scene'].includes(current.id) &&
        <OriginalPreview key={current.id + sourceId} sourceId={sourceId}/>}
      {guided && current.id === 'review' && <dl className="swap-guide-summary">
        {steps.filter(step => step.summary).map(step => <div key={step.id}>
          <dt>{titleOf(step)}</dt><dd>{[].concat(step.summary).map((text, i) => <span key={i}>{text}</span>)}</dd>
          <button type="button" disabled={busy} aria-label={'Editar ' + titleOf(step)} onClick={() => navigate(step.id)}>Editar</button>
        </div>)}
      </dl>}
      {children}
      {guided && <div className="swap-guide-footer">
        {current.issue && (current.id !== 'source' || sourceId) && <p role="status">{current.issue}</p>}
        <nav aria-label="Pasos de Object Swap">
          {index > 0 && <button type="button" className="secondary" disabled={busy} onClick={() => navigate(steps[index - 1].id)}>Atrás</button>}
          {index < steps.length - 1 && <button type="button" className="primary" disabled={busy || Boolean(current.issue)} onClick={() => navigate(steps[index + 1].id)}>{steps[index + 1].id === 'review' ? 'Revisar toma' : 'Continuar'}</button>}
        </nav>
      </div>}
      {saveError && <p role="status">El navegador no pudo guardar el paso. Puedes seguir en esta pestaña.</p>}
    </div>
  </Guide.Provider>;
}
