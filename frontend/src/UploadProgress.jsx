import React,{useEffect,useState} from 'react';
import {uploadSummary} from './uploadTransport.js';
import './uploadProgress.css';

export default function UploadProgress({progress,onCancel}){
  const [now,setNow]=useState(Date.now());
  useEffect(()=>{const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer);},[]);
  const state=uploadSummary(progress,now);
  return <section className="upload-progress-panel" aria-label="Carga de archivo">
    <div className="upload-progress-heading"><strong>{state.verifying?'Archivo enviado · Verificando…':state.percent===null?'Preparando el envío…':`Subiendo archivo · ${state.percent}%`}</strong><button type="button" className="secondary" onClick={onCancel}>Cancelar carga</button></div>
    <p className="upload-progress-file">{progress.fileName}</p>
    <progress aria-label={state.verifying?'Verificando archivo':'Progreso de subida'} max="100" {...(state.percent===null?{}:{value:state.percent})}/>
    <p role="status">{state.verifying?'La transferencia terminó. Esperando la confirmación de Gen Studio.':`${state.loadedMB} de ${state.totalMB} MB enviados`}</p>
    {state.stalled&&<p className="upload-progress-stalled" role="status">{state.verifying?'El servidor aún no confirma el archivo.':`Sin avance desde hace ${state.idle} segundos. Mantén Gen Studio abierto y comprueba la conexión.`}</p>}
  </section>;
}
