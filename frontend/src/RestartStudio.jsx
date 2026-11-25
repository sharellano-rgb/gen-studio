import React,{useEffect,useRef,useState} from 'react';
import {restartJson,restartOutcome} from './restartStudioState.js';
import './restartStudio.css';
export default function RestartStudio(){
  const [open,setOpen]=useState(false),[checking,setChecking]=useState(false),[status,setStatus]=useState(null),[tracking,setTracking]=useState(null),[message,setMessage]=useState(''),[error,setError]=useState('');
  const submitting=useRef(false),reloaded=useRef(false);
  async function review(){
    setOpen(true);setChecking(true);setError('');setStatus(null);
    try{setStatus(await restartJson('/api/system/restart-status'));}
    catch{setError('No se pudo contactar con Gen Studio. El botón necesita que el servidor responda.');}
    finally{setChecking(false);}
  }
  async function restart(){
    if(submitting.current||!status?.can_restart||!status.boot_id)return;
    submitting.current=true;setChecking(true);setError('');setMessage('Solicitando el reinicio…');
    const trackingState={beforeBoot:status.boot_id,previousOperationId:status.operation?.id,started:Date.now()};
    try{
      const result=await restartJson('/api/system/restart',{method:'POST'});
      setMessage(result.stage||'Reiniciando Gen Studio…');setTracking({...trackingState,id:result.id});
    }catch(e){
      if(e.status){setError(e.message);setMessage('');submitting.current=false;}
      else{setMessage('Esperando la reconexión de Gen Studio…');setTracking({...trackingState,id:null});}
    }finally{setChecking(false);}
  }
  useEffect(()=>{
    if(!tracking)return;
    let live=true,timer;
    async function poll(){
      try{
        const next=await restartJson('/api/system/restart-status');if(!live)return;
        const outcome=restartOutcome(next,tracking.beforeBoot,tracking.id,tracking.previousOperationId);
        if(outcome==='failed'){setError(next.operation.error||'No se pudo completar el reinicio.');setTracking(null);setMessage('');submitting.current=false;return;}
        if(outcome==='completed'){if(!reloaded.current){reloaded.current=true;window.location.reload();}return;}
        setMessage(next.operation?.stage||'Esperando la reconexión de Gen Studio…');
      }catch{if(live)setMessage('Reiniciando los servicios. Esperando la reconexión de Gen Studio…');}
      if(!live)return;
      if(Date.now()-tracking.started>420000){setError('No se pudo confirmar el reinicio. Vuelve a comprobar la conexión antes de intentarlo otra vez.');setMessage('');setTracking(null);submitting.current=false;return;}
      timer=setTimeout(poll,2000);
    }
    poll();return()=>{live=false;clearTimeout(timer);};
  },[tracking]);
  return <section className="restart-studio" aria-label="Recuperar Gen Studio">
    {!open?<button className="secondary" onClick={review}>Reiniciar Gen Studio</button>:<div className="panel">
      <strong>Reiniciar Gen Studio y su motor</strong>
      {!tracking&&<><p>Se detendrá tu generación en curso. Tus archivos, resultados y borradores se conservan.</p>{status?.reason&&<p>{status.reason}</p>}</>}
      {message&&<p role="status">{message}</p>}{error&&<p role="alert">{error}</p>}
      {checking&&!tracking&&<p role="status">Comprobando el estado…</p>}
      {!tracking&&<div className="restart-actions"><button className="primary" disabled={checking||!status?.can_restart} onClick={restart}>Reiniciar ahora</button><button className="secondary" disabled={checking} onClick={()=>setOpen(false)}>Cerrar</button></div>}
    </div>}
  </section>;
}
