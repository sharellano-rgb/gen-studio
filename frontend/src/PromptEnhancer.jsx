import React,{useEffect,useState} from 'react';
import {REFERENCE_ROLES,proposalMatches,validProposalPrompt} from './promptEnhancer';

async function api(options){
  const response=await fetch('/api/prompt-enhancer',options);
  const data=await response.json();
  if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'No se pudo iniciar el análisis. Revisa las referencias y el tramo.');
  return data;
}
export default function PromptEnhancer({videoId,referenceIds,settings,valid,busy,onApply,onActivity}){
  const storageKey=JSON.stringify(['gen-enhancer',videoId,referenceIds]);
  const [intent,setIntent]=useState(''),[roles,setRoles]=useState([]);
  const [analysis,setAnalysis]=useState(null),[active,setActive]=useState(false),[sending,setSending]=useState(false);
  const [error,setError]=useState(''),[draft,setDraft]=useState(''),[undo,setUndo]=useState(null),[notice,setNotice]=useState('');
  const [loadedKey,setLoadedKey]=useState('');
  const selectedRoles=referenceIds.map((_,i)=>settings.direct_binding_mode==='assigned'?'Identidad y apariencia':roles[i]||(i===0?'Identidad del personaje':'Vestuario y cuerpo'));
  const request={video_id:videoId,reference_ids:referenceIds,frames:settings.frames,start_seconds:settings.direct_start_seconds,
    intent:settings.direct_binding_mode==='assigned'?['Reemplazar por completo a TODOS los sujetos asignados, incluida su ropa.',...referenceIds.map((_,i)=>`Picture ${i+1}: ${settings.direct_targets?.[i]||'sin asignar'}`),settings.direct_direction,intent].filter(Boolean).join('\n'):intent,reference_roles:selectedRoles,reference_type:settings.direct_reference_type||'auto',current_prompt:settings.prompt};
  useEffect(()=>{
    let saved={};try{saved=JSON.parse(localStorage.getItem(storageKey))||{};}catch{}
    setIntent(saved.intent||'');setRoles(saved.roles||[]);setLoadedKey(storageKey);setUndo(null);setNotice('');
  },[storageKey]);
  useEffect(()=>{if(loadedKey===storageKey)localStorage.setItem(storageKey,JSON.stringify({intent,roles}));},[intent,roles,loadedKey,storageKey]);
  useEffect(()=>{
    let alive=true;
    async function refresh(){try{const data=await api();if(alive){setAnalysis(data.analysis);setActive(data.active);}}catch(e){if(alive)setError(e.message);}}
    refresh();const timer=setInterval(refresh,2000);return()=>{alive=false;clearInterval(timer);};
  },[]);
  useEffect(()=>{onActivity(active||sending);return()=>onActivity(false);},[active,sending,onActivity]);
  useEffect(()=>{if(analysis?.status==='completed')setDraft(analysis.proposal?.prompt||'');},[analysis?.id,analysis?.status]);
  async function analyze(){
    setSending(true);setError('');setNotice('');
    try{setAnalysis(await api({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request)}));setActive(true);}
    catch(e){setError(e.message);}finally{setSending(false);}
  }
  const matches=proposalMatches(analysis,request),proposal=analysis?.proposal;
  const running=active||sending;
  const applicable=matches&&analysis?.status==='completed'&&!running&&!proposal?.needs_clarification&&validProposalPrompt(draft,referenceIds.length);
  function apply(){if(!applicable)return;setUndo({before:settings.prompt,applied:draft});onApply(draft);setNotice('Propuesta aplicada. Puedes editar la dirección antes de generar.');}
  return <section className="prompt-enhancer" aria-label="Asistente de prompt local">
    <h3>Asistente de prompt · Qwen local</h3>
    <p>Analiza cinco fotogramas del tramo y tus imágenes. Revisa la propuesta antes de aplicarla; la fidelidad final depende de H3.</p>
    <label>¿Qué quieres cambiar o conservar?<textarea aria-label="Intención del reemplazo" maxLength="1500" placeholder="Por ejemplo: cambia sólo la cabeza por la del gato de Picture 1 y conserva el cuerpo y la ropa." value={intent} onChange={e=>setIntent(e.target.value)} disabled={running}/></label>
    <div className="parameter-grid">{referenceIds.map((id,i)=><label key={i}>Función de Picture {i+1}<select aria-label={`Función de Picture ${i+1}`} value={selectedRoles[i]} disabled={running||settings.direct_binding_mode==='assigned'} onChange={e=>setRoles(current=>referenceIds.map((_,n)=>n===i?e.target.value:current[n]||(n===0?'Identidad del personaje':'Vestuario y cuerpo')))}>{REFERENCE_ROLES.filter(r=>i>0||['Identidad del personaje','Identidad facial','Identidad y apariencia','Hoja de personaje'].includes(r)).map(r=><option key={r}>{r}</option>)}</select></label>)}</div>
    <button className="secondary" onClick={analyze} disabled={!valid||busy||running||loadedKey!==storageKey}>{running?'Analizando con Qwen…':'Analizar y mejorar prompt'}</button>
    {!valid&&<p className="muted">Selecciona vídeo, imágenes y un tramo válido para analizar.</p>}
    {busy&&<p className="muted">Disponible cuando termine la carga o generación actual.</p>}
    {running&&<p role="status">{analysis?.stage||'Iniciando…'} · La generación esperará a que Qwen libere la GPU.</p>}
    {error&&<p className="error" role="alert">{error}</p>}
    {!running&&analysis?.status==='failed'&&<p className="error" role="alert">{analysis.error}</p>}
    {!running&&proposal&&<div className="enhancer-proposal">
      {!matches&&<p className="error" role="status">Cambiaste las referencias, la intención o el tramo. Vuelve a analizar para aplicar una propuesta actualizada.</p>}
      <h4>Lo que detectó Qwen</h4><p>{proposal.observations}</p><p><strong>Personaje a reemplazar:</strong> {proposal.target}</p>
      <ul>{proposal.references.map((item,i)=><li key={i}>{item}</li>)}</ul>
      {proposal.warnings.length>0&&<ul className="enhancer-warnings">{proposal.warnings.map((item,i)=><li key={i}>{item}</li>)}</ul>}
      {proposal.needs_clarification&&<p role="status">Aclara el personaje o las referencias en el campo de intención y vuelve a analizar.</p>}
      <label>Propuesta editable<textarea aria-label="Prompt propuesto por Qwen" maxLength="4000" value={draft} onChange={e=>setDraft(e.target.value)}/></label>
      <div className="history-actions"><button className="secondary" disabled={!applicable} onClick={apply}>Aplicar al prompt</button><button className="secondary" onClick={()=>{setDraft('');setNotice('Propuesta descartada. La dirección actual se conserva.');}}>Descartar propuesta</button></div>
      {!validProposalPrompt(draft,referenceIds.length)&&draft&&<p className="error">Conserva las etiquetas de Video 1 y de todas las imágenes seleccionadas.</p>}
    </div>}
    {undo&&settings.prompt===undo.applied&&<button className="secondary" onClick={()=>{onApply(undo.before);setUndo(null);setNotice('Se restauró el prompt anterior.');}}>Restaurar prompt anterior</button>}
    {notice&&<p role="status" className="validation success">{notice}</p>}
  </section>;
}

import {workspaceLocalStorage as localStorage} from './workspaceStorage.js';
