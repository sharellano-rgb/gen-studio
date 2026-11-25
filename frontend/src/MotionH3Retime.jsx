import React,{useEffect,useRef,useState} from 'react';
export default function MotionH3Retime({job,busy,onCreated,onError}){
  const [offset,setOffset]=useState(job.parameters.timing_offset_frames||0),[sending,setSending]=useState(false),key=useRef(null);
  useEffect(()=>{key.current=null;},[offset]);
  async function adjust(){setSending(true);onError('');if(!key.current)key.current=crypto.randomUUID();
    try{const r=await fetch('/api/jobs/'+job.id+'/motion-h3-retime',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_key:key.current,timing_offset_frames:offset})}),d=await r.json();if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:'No se pudo ajustar la copia.');key.current=null;onCreated(d);}
    catch(e){onError(e.message+' Revisa Historial antes de repetir.');}finally{setSending(false);}
  }
  return <details className="advanced-controls"><summary>Ajustar sincronización de esta toma <span>▾</span></summary><p>{job.parameters.motion_h3_segmented?'Crea otra copia desde los tramos originales, sin volver a ejecutar H3. El ajuste adelanta cada tramo antes de unirlo; se conservan la duración y el audio original.':'Crea otra copia desde la generación original, sin volver a ejecutar H3. Adelantar el vídeo no cambia el audio ni la duración; puede mantener el último fotograma al final.'}</p><label>Adelantar (fotogramas a 24 fps)<input aria-label="Adelantar toma Motion H3" type="number" min="0" max="18" step="1" value={offset} onChange={e=>setOffset(Number(e.target.value))}/></label><button className="secondary" onClick={adjust} disabled={sending||busy||!Number.isInteger(offset)||offset<0||offset>18||offset===job.parameters.timing_offset_frames}>Crear copia con ajuste temporal</button>{!job.parameters.motion_h3_segmented&&job.motion_timing?.tail_hold_frames>0&&<p>Esta copia mantiene el último fotograma durante {job.motion_timing.tail_hold_frames} fotogramas.</p>}</details>;
}
