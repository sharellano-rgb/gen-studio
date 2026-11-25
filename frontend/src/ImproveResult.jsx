import React,{useState} from 'react';
import H3FaceRefine from './H3FaceRefine.jsx';
import GeometryCopy from './GeometryCopy.jsx';
import Upscale1080 from './Upscale1080.jsx';
import {refinementStatus} from './sceneResults.js';
export default function ImproveResult({job,assets,busy,onRefine,onCreated,onUpdated,onOpen,onError,onAsset}) {
  const p=job.parameters,refs=p.direct_reference_ids||[];
  const [reference,setReference]=useState(p.h3_face_reference_id||refs[0]||p.face_reference_id||''),[sending,setSending]=useState(false);
  async function refine(){setSending(true);try{await onRefine(job,reference);}catch(e){onError(e.message);}finally{setSending(false);}}
  const target=(p.direct_characters||[]).find(c=>c.reference_ids?.includes(reference))?.target||p.direct_targets?.[refs.indexOf(reference)];
  const segmented=['long_character','long_assembly'].includes(p.operation);
  if(p.operation==='upscale_1080')return <Upscale1080 job={job} busy={busy} onCreated={onCreated} onOpen={onOpen} onError={onError}/>;
  return <details className="improve-result"><summary>Mejorar este resultado</summary><p>{refinementStatus(job)}. Cada mejora crea una copia.</p>
    {[undefined,'long_character','long_assembly','h3_face_refine','geometry_copy'].includes(p.operation)&&<Upscale1080 key={job.id+'-upscale'} job={job} busy={busy} onCreated={onCreated} onOpen={onOpen} onError={onError}/>}
    {segmented&&<p>Abre el plano que quieras corregir en la lista de tramos y usa «Mejorar este resultado». El refinamiento trabaja sobre una toma continua, sin cruzar cortes.</p>}
    {[undefined,'h3_face_refine'].includes(p.operation)&&<><label>Referencia para el rostro<select value={reference} onChange={e=>setReference(e.target.value)} disabled={sending}><option value="">Selecciona una foto del rostro</option>{assets.filter(a=>a.kind==='image').map(a=><option key={a.id} value={a.id}>{a.name}{refs.includes(a.id)?' · Usada en esta toma':''}</option>)}</select></label>{target&&<p>Persona asignada: {target}</p>}<p>Usa Refinamiento Facial con la Receta 2. La referencia se conserva; marca a la persona y revisa su máscara antes de generar.</p><button className="primary" disabled={!reference||busy||sending} onClick={refine}>{sending?'Preparando la copia…':'Preparar refinamiento facial'}</button>{busy&&<p role="status">Espera a que termine el trabajo actual.</p>}</>}
    {([undefined,'h3_face_refine'].includes(p.operation))&&<details className="advanced-controls"><summary>Otro método facial · Experimental</summary><H3FaceRefine key={job.id+'-h3-face'} job={job} assets={assets} busy={busy} onAsset={onAsset} onError={onError} onOpen={onOpen} onCreated={onCreated}/></details>}
    {[undefined,'geometry_copy'].includes(p.operation)&&<details className="advanced-controls"><summary>Ajustar proporción de la cabeza</summary><GeometryCopy key={job.id+'-geometry'} job={job} busy={busy} onError={onError} onOpen={onOpen} onUpdated={onUpdated} onCreated={onCreated}/></details>}
  </details>;
}
