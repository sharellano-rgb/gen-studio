import React,{useEffect,useRef,useState} from 'react';
import {canUpscale,createUpscaleRequest,upscaleMethodLabel,upscaleMethods} from './upscaleState.js';

async function api(path,body){
  const response=await fetch('/api'+path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:undefined);
  const data=await response.json();
  if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'No se pudo preparar el escalado.');
  return data;
}

export default function Upscale1080({job,busy,onCreated,onOpen,onError}){
  const derived=job.parameters.operation==='upscale_1080';
  const [loaded,setLoaded]=useState(null),[error,setError]=useState(''),[attempt,setAttempt]=useState(0),[sending,setSending]=useState(false);
  const request=useRef(null),inFlight=useRef(false);
  if(!request.current)request.current=createUpscaleRequest();
  const options=loaded?.jobId===job.id?loaded.data:null;
  const natural=upscaleMethods(options)[0];
  useEffect(()=>{request.current.select(job.id);},[job.id]);
  useEffect(()=>{
    if(derived)return;
    let live=true;setLoaded(null);setError('');
    api(`/jobs/${job.id}/upscale-options`).then(data=>{
      if(live)setLoaded({jobId:job.id,data});
    }).catch(e=>{if(live)setError(e.message);});
    return()=>{live=false;};
  },[job.id,derived,attempt]);
  async function submit(){
    if(inFlight.current||!canUpscale(options,'natural',{busy,sending}))return;
    inFlight.current=true;setSending(true);
    try{
      const body=request.current.body(job.id);
      const result=await api(`/jobs/${job.id}/upscale-1080`,body);
      request.current.complete(body.request_key);onCreated(result);
    }catch(e){onError(e.message+' Revisa el Historial antes de repetir el envío.');}
    finally{inFlight.current=false;setSending(false);}
  }
  if(derived)return <section className="face-summary upscale-summary"><h3>{job.status==='completed'?'Copia a 1080p':'Escalado a 1080p'}</h3><p>{job.parameters.resolution?.replace('x',' × ')} píxeles · 24 fps · {upscaleMethodLabel(job.parameters.upscale_method)}</p><p>El resultado original se conserva. Esta copia aumenta la resolución sin aplicar refinamiento facial. Revisa el rostro y las texturas en la comparación.</p><div className="history-actions"><button type="button" className="secondary" onClick={()=>onOpen(job.parameters.upscale_parent_id)}>Ver resultado original</button><a className="download" href={`/api/jobs/${job.id}/result?variant=original&download=true`}>Descargar resultado original ↓</a></div></section>;
  return <section className="face-summary upscale-summary" aria-label="Escalar resultado a 1080p"><h3>Escalar a 1080p</h3><p>Crea una copia con más resolución y conserva el original, su audio y sus proporciones. El escalado no corrige la identidad facial ni vuelve a generar la escena con H3.</p>
    <p><strong>Natural · Lanczos.</strong> Conserva la apariencia y las texturas originales. El resultado es más suave.</p>
    {options?.enabled&&natural&&!natural.enabled&&<p role="status">{natural.reason||'El escalado natural no está disponible en este momento.'}</p>}
    {options?.enabled&&<p>{options.source_width} × {options.source_height} → {options.width} × {options.height} píxeles<br/>24 fps · {options.duration.toFixed(2)} s. Puede añadir bordes estrechos para conservar el encuadre.</p>}
    {!options&&!error&&<p role="status">Comprobando el escalado disponible…</p>}{options&&!options.enabled&&<p role="status">{options.reason||'El escalado no está disponible para este resultado.'}</p>}
    {error&&<><p role="alert">{error}</p><button type="button" className="secondary" onClick={()=>setAttempt(value=>value+1)}>Volver a comprobar</button></>}
    <button type="button" className="primary" disabled={!canUpscale(options,'natural',{busy,sending})} onClick={submit}>{sending?'Preparando copia…':'Crear copia a 1080p'}</button>{busy&&<p role="status">Espera a que termine el trabajo actual.</p>}
  </section>;
}
