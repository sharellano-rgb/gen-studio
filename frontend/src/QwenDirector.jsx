import {useQwenModels} from './QwenModelSelector.jsx';
import {qwenLabel,ORIGINAL_QWEN} from './qwenModels.js';
import React,{useEffect,useRef,useState} from 'react';
import {useStudioWorkspace} from './StudioWorkspace.jsx';
import './qwenDirector.css';

async function api(id,tail='',body){
  const response=await fetch(`/api/jobs/${id}/qwen-director${tail}`,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});
  const data=await response.json();
  if(!response.ok){const e=Error(typeof data.detail==='string'?data.detail:'No se pudo consultar Qwen.');e.status=response.status;throw e;}
  return data;
}
function ExpressionReview({report}){
  if(!report)return null;
  const {warnings=[],supported=[],limited=[]}=report;
  function evidence(row){
    return row.evidence.map(e=>e.seconds.toLocaleString('es-MX',{maximumFractionDigits:3})+' s · '+e.observation).join(' / ');
  }
  return <>
    {warnings.map((w,i)=><p key={i} className="editor-status">
      <strong>{w.actor} · Revisar expresión.</strong> El plan indica «{w.claim}» durante todo el plano. Las muestras del original indican {evidence(w)}.
    </p>)}
    {(supported.length+limited.length>0)&&<details>
      <summary>Expresiones en las muestras del original</summary>
      {limited.map((w,i)=><p key={i}><strong>{w.actor} · Evidencia insuficiente.</strong> Las observaciones disponibles no permiten confirmar «{w.claim}» durante el plano.{w.evidence.length>0&&' Muestra: '+evidence(w)+'.'}</p>)}
      {supported.map((w,i)=><p key={i}><strong>{w.actor} · Coincide en las muestras.</strong> {evidence(w)}.</p>)}
    </details>}
  </>;
}
export default function QwenDirector({job,onCreated,onError,onCancel,onOpenTake,inputMode='video',forcedModelId=null}){
  const workspace=useStudioWorkspace();
  const models=useQwenModels();
  const [data,setData]=useState(null),[message,setMessage]=useState(''),[sending,setSending]=useState(false),[creating,setCreating]=useState(false);
  const modelId=job?(data?data.model:job.qwen_model_binding?.model_id||ORIGINAL_QWEN):forcedModelId||models?.selection?.model_id;
  const [open,setOpen]=useState(Boolean(job?.qwen_waiting)||Boolean(workspace?.active)),[error,setError]=useState('');
  const [remaining,setRemaining]=useState(false),[receipt,setReceipt]=useState('');
  const live=useRef(true),pending=useRef(null),versionKey=useRef(null);
  const readOnly=Boolean(data?.read_only_reason);
  const active=job&&!['completed','failed','cancelled'].includes(job.status),waiting=!readOnly&&Boolean(job?.qwen_waiting||data?.waiting);
  useEffect(()=>{live.current=true;return()=>{live.current=false;};},[]);
  useEffect(()=>{if(waiting)setOpen(true);},[waiting]);
  useEffect(()=>{if(data?.draft_message)setMessage(current=>current||data.draft_message);},[data?.draft_message]);
  useEffect(()=>{
    if(!job?.id)return;let stopped=false,timer;
    async function poll(){try{const value=await api(job.id);if(!stopped){setData(value);if(!value.read_only_reason&&(value.status==='running'||active||waiting))timer=setTimeout(poll,3000);}}
      catch(e){if(!stopped){setError(e.message);if(active)timer=setTimeout(poll,5000);}}}
    poll();return()=>{stopped=true;clearTimeout(timer);};
  },[job?.id,job?.qwen_director_revision,job?.qwen_waiting,active,data?.status==='running']);
  async function send(){
    if(sending||!message.trim())return;setSending(true);setError('');setReceipt('');
    if(!pending.current)pending.current={request_key:crypto.randomUUID(),message:message.trim(),remaining_segments:remaining};
    try{const result=await api(job.id,'',pending.current);if(live.current){setData(result);if(result.direction_saved_from)setReceipt('Corrección guardada desde el tramo '+result.direction_saved_from+'. Se aplicará al reanudar.');setMessage(result.draft_message||'');pending.current=null;}}
    catch(e){if(live.current){setError(e.message);if(e.status&&e.status<500)pending.current=null;}}
    finally{if(live.current)setSending(false);}
  }
  async function create(){if(creating)return;setCreating(true);setError('');
    if(!versionKey.current)versionKey.current=crypto.randomUUID();
    try{const result=await api(job.id,'/version',{request_key:versionKey.current});versionKey.current=null;onCreated(result);}
    catch(e){if(live.current)setError(e.message);}
    finally{if(live.current)setCreating(false);}}
  async function retryIntel(){
    if(sending||!data?.intel_retry)return;
    setSending(true);setError('');
    try{
      const value=await api(job.id,'/retry-intel?attempt_id='+encodeURIComponent(data.intel_retry.id),{});
      if(live.current)setData(value);
    }catch(e){if(live.current)setError(e.message);}
    finally{if(live.current)setSending(false);}
  }
  const parentId=job?.parameters?.sequence_parent_id;
  const answering=sending||data?.status==='running';
  const canChat=Boolean(data?.plan)&&(!active||waiting)&&!answering&&!job?.cancel_requested;
  if(job&&data?.enabled===false)return null;
  return <section className="panel qwen-director">
    <details open={open} onToggle={e=>setOpen(e.currentTarget.open)}>
      <summary><strong>Asistente {qwenLabel(modelId)}</strong><span>{data?.intel_retry?'Conexión con Intel en pausa':waiting?'Necesita tu respuesta':job?.qwen_director_revision?'Indicaciones de esta toma':'Automatización del flujo'}</span></summary>
      {!readOnly&&<p>Analiza {inputMode==='image'||['pika_h3_flex','h3_i2va'].includes(job?.parameters.engine)?'la imagen inicial':'el vídeo y las referencias'} para preparar las indicaciones de este método. Puedes aclarar dudas y corregirlo aquí. Las conversaciones son privadas y se guardan por toma.</p>}
      {job&&<small className="qwen-bound-model">Modelo de esta toma: {qwenLabel(modelId)}</small>}
      {data?.expression_review?.revision===data?.revision&&<ExpressionReview report={data?.expression_review}/>}
      {readOnly&&<p role="status"><strong>Historial de solo lectura. </strong>{data.read_only_reason}</p>}
      {!job&&<p>Al crear una toma, Qwen preparará las indicaciones automáticamente. Si necesita una aclaración, esperará tu respuesta antes de generar.</p>}
      {data?.intel_retry&&<div role="status"><p>{data.intel_retry.error}</p><p>El análisis visual del tramo está guardado.</p>
        <button type="button" className="secondary" onClick={retryIntel} disabled={sending}>{sending?'Enviando…':'Reintentar plan en Intel'}</button></div>}
      {job&&!data?.plan&&!data?.intel_retry&&<p role="status">{readOnly?'Esta toma no tiene un plan guardado.':job.qwen_director_version?job.stage:'Esta toma es anterior a la automatización común. Usa sus ajustes en una toma nueva para activar el asistente.'}</p>}
      {data?.messages?.length>0&&<div className="qwen-messages" aria-label="Conversación de esta toma">{data.messages.map((m,i)=><div className={'qwen-message '+m.role} key={i}><strong>{m.role==='user'?'Tú':'Qwen'}</strong><p>{m.content}</p></div>)}</div>}
      {data?.plan?.questions?.length>0&&<div role="status"><strong>{readOnly?'Preguntas guardadas':'Para continuar:'}</strong><ul>{data.plan.questions.map((q,i)=><li key={i}>{q}</li>)}</ul></div>}
      {data?.plan&&<>{!readOnly&&<><label>Mensaje para Qwen<textarea rows={3} maxLength={2500} value={message} disabled={!canChat||Boolean(pending.current)} onChange={e=>setMessage(e.target.value)} placeholder="Por ejemplo: el apoyo inicial es el pie izquierdo; conserva la mano que sujeta el objeto."/></label>
        {(job?.video_sequence_version||job?.parameters?.sequence_parent_id)&&!active&&<label><input type="checkbox" checked={remaining} disabled={answering||Boolean(pending.current)} onChange={e=>setRemaining(e.target.checked)}/>Aplicar la corrección a los tramos pendientes</label>}
        {receipt&&<p role="status">{receipt}</p>}
        <div className="qwen-actions"><button type="button" className="secondary" disabled={!canChat||!message.trim()} onClick={send}>{answering?'Qwen está respondiendo…':pending.current?'Recuperar mensaje':waiting?'Responder y continuar':remaining?'Guardar corrección para los tramos pendientes':'Enviar corrección o pregunta'}</button>
        {waiting&&onCancel&&<button type="button" className="secondary" onClick={()=>onCancel(job.id)}>Cancelar esta toma</button>}
        {!active&&data.plan.ready&&data.revision!==job.qwen_applied_revision&&<button type="button" className="primary" disabled={answering||creating} onClick={create}>{creating?'Creando…':parentId?'Generar otra versión del vídeo completo':'Generar otra versión con estas indicaciones'}</button>}</div></>}
        {parentId&&onOpenTake&&<button type="button" className="secondary" onClick={()=>onOpenTake(parentId)}>Abrir tramos para continuar este vídeo</button>}
        {active&&!waiting&&!readOnly&&<p>El motor está trabajando. Podrás conversar al terminar o cuando Qwen solicite una aclaración.</p>}
        <details><summary>Ver indicaciones {data.revision!==data.applied_revision?'propuestas':'aplicadas'}</summary>{['pose_notes','motion_notes','identity_notes','special_notes'].map(k=>data.plan[k]&&<p key={k} className="qwen-notes">{data.plan[k]}</p>)}</details>
        {data.applied_plan&&data.revision!==data.applied_revision&&<details><summary>Indicaciones usadas en el resultado original</summary>{['pose_notes','motion_notes','identity_notes','special_notes'].map(k=>data.applied_plan[k]&&<p key={k} className="qwen-notes">{data.applied_plan[k]}</p>)}</details>}
      </>}
      {(error||data?.error)&&<p role="alert">{error||data.error}</p>}
      <small>Qwen puede interpretar mal poses o partes ocultas. Las correcciones posteriores crean otra versión del resultado.</small>
    </details>
  </section>;
}
