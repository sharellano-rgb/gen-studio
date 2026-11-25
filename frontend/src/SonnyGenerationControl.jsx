import React,{useEffect,useRef,useState} from 'react';
import {canManageSonny,changeSonnyGenerationLimit,sonnyGenerationRequest} from './sonnyGenerationControl.js';
import './sonnyGenerationControl.css';

export default function SonnyGenerationControl({workspaceId,targetId='sonny',targetName='Sonny'}){
  return canManageSonny(workspaceId)?<OwnerSonnyGenerationControl key={workspaceId+targetId} targetId={targetId} targetName={targetName}/>:null;
}

function OwnerSonnyGenerationControl({targetId,targetName}){
  const titleId=targetId+'-generation-title',stateId=targetId+'-generation-state',descriptionId=targetId+'-generation-description';
  const [policy,setPolicy]=useState(null),[pending,setPending]=useState(true),[error,setError]=useState('');
  const request=useRef(null);
  async function refresh(){
    if(request.current)return;
    const controller=new AbortController();request.current=controller;
    setPending(true);setError('');setPolicy(null);
    try{const value=await sonnyGenerationRequest(undefined,{signal:controller.signal,targetId});if(!controller.signal.aborted)setPolicy(value);}
    catch(error){if(!controller.signal.aborted)setError(error.message||'No se pudo consultar el límite de generación.');}
    finally{if(request.current===controller)request.current=null;if(!controller.signal.aborted)setPending(false);}
  }
  async function toggle(){
    if(request.current||!policy)return;
    const controller=new AbortController();request.current=controller;
    setPending(true);setError('');
    try{
      const result=await changeSonnyGenerationLimit(!policy.enabled,{signal:controller.signal,targetId});
      if(!controller.signal.aborted){setPolicy(result.policy);setError(result.error);}
    }catch(error){
      if(!controller.signal.aborted){setPolicy(null);setError(error.message||'No se pudo confirmar el cambio.');}
    }finally{if(request.current===controller)request.current=null;if(!controller.signal.aborted)setPending(false);}
  }
  useEffect(()=>{refresh();return()=>{request.current?.abort();request.current=null;};},[]);
  const state=pending?(policy?'Guardando…':'Consultando…'):policy?(policy.enabled?'Activado · 1 vídeo cada 3 horas':'Desactivado · Sin límite horario'):'Estado sin confirmar';
  return <section className="panel sonny-generation-control" aria-labelledby={titleId}>
    <div className="sonny-generation-heading">
      <div><span className="sonny-generation-owner">Sólo tú</span><h2 id={titleId}>Restringir a {targetName}</h2></div>
      {policy&&<button type="button" className="sonny-generation-switch" role="switch" aria-label={'Restringir a '+targetName} aria-checked={policy.enabled} aria-describedby={descriptionId+' '+stateId} disabled={pending} onClick={toggle}><span aria-hidden="true"/></button>}
    </div>
    <p id={stateId} className="sonny-generation-state" role="status" aria-live="polite">{state}</p>
    <p id={descriptionId}>Al activarlo, {targetName} puede iniciar un vídeo cada 3 horas. Puede seguir poniendo trabajos en cola. Al desactivarlo, sus vídeos se procesan según la disponibilidad del motor.</p>
    <p className="sonny-generation-note">El cambio es inmediato y se conserva al reiniciar Gen Studio. No interrumpe un vídeo que ya se está generando.</p>
    {error&&<p className="sonny-generation-error" role="alert">{error}</p>}
    {!policy&&!pending&&<button type="button" className="secondary" onClick={refresh}>Comprobar estado</button>}
  </section>;
}
