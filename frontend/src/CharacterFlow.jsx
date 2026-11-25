import React from 'react';
import {flowState} from './guidedDraft';

export default function CharacterFlow({job,jobs=[],onSelectJob}) {
  const state=flowState(job,jobs);
  if(!state)return null;
  const steps=['Genera la escena','Detalle H3','Revisa el rostro','Compón la toma'];
  const sceneReview=job?.parameters?.operation==='identity_anchor'&&job.parameters.anchor_method==='guided_composition'&&job.result_available;
  return <section className="character-flow" aria-label="Recorrido de Character Replacement">
    <ol>{steps.map((label,index)=>{
      const done=index<state.stage||state.complete;
      return <li key={label} className={done?'done':index===state.stage?'current':''} aria-current={!state.complete&&index===state.stage?'step':undefined}>
        <span>{done?'✓':index+1}</span><strong>{label}</strong></li>;
    })}</ol>
    <p>{state.complete?'Esta composición está revisada y lista para descargar.':sceneReview?'Revisa la escena completa y guarda su aprobación o incidencia.':state.stage===0?'Carga el vídeo y la referencia del personaje para crear la escena base.':state.stage===1?'La escena conserva el movimiento. El siguiente paso genera el detalle facial con tu foto real.':state.stage===2?'Revisa el detalle completo antes de aprobar rostro, piel y accesorios.':'Usa el detalle aprobado para crear una copia estable de su escena.'}</p>
    {state.detailJobId&&onSelectJob&&<button className="secondary" onClick={()=>onSelectJob(state.detailJobId)}>
      {state.stage===2?'Revisar el detalle H3':state.stage===3?'Ver detalle aprobado':'Ver el detalle H3'}</button>}
    {job?.parameters?.operation==='guided_head'&&['failed','cancelled'].includes(job.status)&&job.parameters.head_parent_id&&onSelectJob&&
      <button className="secondary" onClick={()=>onSelectJob(job.parameters.head_parent_id)}>Volver a la escena y ajustar la zona</button>}
  </section>;
}
