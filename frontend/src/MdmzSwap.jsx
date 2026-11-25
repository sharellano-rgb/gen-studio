import React,{useEffect,useRef,useState} from 'react';
import {workspaceLocalStorage as storage,activeWorkspaceId} from './workspaceStorage.js';
import {mdmzForm,mdmzFullFrames,mdmzValid,mdmzRequest,mdmzAcknowledges,mdmzAnalysisRequest,mdmzAnalysisMatches,mdmzPromptValid} from './mdmzState.js';
import ResultViewer from './ResultViewer.jsx';
import JobCancelButton from './JobCancelButton.jsx';
import JobElapsed from './JobElapsed.jsx';
import './mdmzSwap.css';

const DRAFT='gen-mdmz-draft-v1',PENDING='gen-mdmz-pending-v1';
function read(key){try{return JSON.parse(storage.getItem(key)||'null');}catch{return null;}}
async function api(path,options){const response=await fetch('/api/mdmz'+path,options);if(response.headers.get('X-Gen-Studio-Workspace')!==activeWorkspaceId())throw Error('El espacio cambió. Recarga Gen Studio.');const data=await response.json();if(!response.ok){const error=Error(typeof data.detail==='string'?data.detail:'No se pudo completar MDMZ.');error.status=response.status;throw error;}return data;}

export default function MdmzSwap({assets,job,draftParams,busy,available,uploading,onUpload,onCreated,onError,onCancel,onReuse}){
 const [form,setForm]=useState(()=>mdmzForm(job?.parameters||draftParams||read(DRAFT)));
 const [options,setOptions]=useState(null),[retry,setRetry]=useState(0),[pending,setPending]=useState(()=>read(PENDING));
 const [sending,setSending]=useState(false),[analysis,setAnalysis]=useState(null),[analyzing,setAnalyzing]=useState(false),[proposal,setProposal]=useState('');
 const [localError,setLocalError]=useState('');
 const lock=useRef(false),live=useRef(true);
 const video=assets.find(a=>a.id===form.video_id&&a.kind==='video'),reference=assets.find(a=>a.id===form.reference_id&&a.kind==='image');
 const fullFrames=mdmzFullFrames(video,form.start_seconds);
 const blocked=busy||uploading||sending||analyzing,terminal=['completed','failed','cancelled','submission_unknown'].includes(job?.status);
 const valid=mdmzValid(form,video,reference),ready=options?.presets?.[form.speed]?.ok;
 useEffect(()=>{live.current=true;return()=>{live.current=false;};},[]);
 useEffect(()=>{if(!job)try{storage.setItem(DRAFT,JSON.stringify(form));}catch{}},[form,job]);
 useEffect(()=>{let current=true;api('/options').then(data=>{if(current){setOptions(data);setForm(f=>f.prompt?f:{...f,prompt:data.default_prompt});setLocalError('');}}).catch(error=>{if(current)setLocalError(error.message);});return()=>{current=false;};},[retry]);
 useEffect(()=>{let current=true,inFlight=false;async function check(){if(inFlight)return;inFlight=true;try{const data=await api('/analysis');if(current){setAnalysis(data.analysis);setAnalyzing(data.active);if(data.analysis?.status==='completed')setProposal(data.analysis.proposal?.prompt||'');}}catch(error){if(current)setLocalError(error.message);}finally{inFlight=false;}}check();const timer=analyzing?setInterval(check,2000):null;return()=>{current=false;if(timer)clearInterval(timer);};},[analyzing]);
 function update(key,value){setForm(f=>({...f,[key]:value}));}
 function chooseVideo(id){const source=assets.find(a=>a.id===id&&a.kind==='video');setForm(f=>({...f,video_id:id,start_seconds:0,frames:mdmzFullFrames(source)||107}));}
 async function upload(event,kind){const file=event.target.files?.[0];event.target.value='';if(!file)return;try{const asset=await onUpload(file,kind);if(live.current){if(kind==='video')setForm(f=>({...f,video_id:asset.id,start_seconds:0,frames:mdmzFullFrames(asset)||107}));else update('reference_id',asset.id);}}catch(error){onError(error.message);}}
 async function analyze(){if(blocked||!video||!reference)return;setAnalyzing(true);setLocalError('');try{const data=await api('/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(mdmzAnalysisRequest(form))});if(live.current)setAnalysis(data);}catch(error){if(live.current){setAnalyzing(false);setLocalError(error.message);}}}
 async function send(diagnostics=false){
  if(lock.current||blocked||!pending&&(!valid||!ready||!available))return;
  lock.current=true;setSending(true);setLocalError('');onError('');
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),30000);
  try{const body=pending||{...mdmzRequest(form,crypto.randomUUID()),...(diagnostics?{diagnostics:true}:{})};storage.setItem(PENDING,JSON.stringify(body));setPending(body);
   const result=await api('/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:controller.signal});
   if(!mdmzAcknowledges(result,body))throw Error('El servidor no confirmó los ajustes MDMZ. Conserva esta solicitud para verificarla.');
   storage.removeItem(PENDING);if(live.current){setPending(null);onCreated(result);}
  }catch(error){if(error.status>=400&&error.status<500){storage.removeItem(PENDING);if(live.current)setPending(null);}if(live.current)setLocalError(error.name==='AbortError'?'Envío sin confirmar. Reintenta la misma solicitud para evitar duplicados.':error.message);}
  finally{clearTimeout(timer);lock.current=false;if(live.current)setSending(false);}
 }
 const matches=mdmzAnalysisMatches(analysis,form);
 return <div className="mdmz-studio">
  <section className="panel mdmz-composer" aria-label="MDMZ Character Swap">
   <div className="panel-title"><div><span className="eyebrow">NUEVO MÉTODO · LOCAL</span><h2>MDMZ Character Swap</h2></div><span className="tag">H3 + SAM3</span></div>
   <p>Una imagen define al personaje; el vídeo aporta su movimiento. Revisa el resultado antes de usarlo.</p>
   <fieldset disabled={blocked||Boolean(pending)}>
    <div className="mdmz-fields"><label>Vídeo fuente<select value={form.video_id} onChange={e=>chooseVideo(e.target.value)}><option value="">Selecciona un vídeo</option>{assets.filter(a=>a.kind==='video').map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select><input type="file" accept="video/*" aria-label="Subir vídeo para MDMZ" onChange={e=>upload(e,'video')}/></label>
     <label>Imagen del personaje<select value={form.reference_id} onChange={e=>update('reference_id',e.target.value)}><option value="">Selecciona una imagen</option>{assets.filter(a=>a.kind==='image').map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select><input type="file" accept="image/*" aria-label="Subir personaje para MDMZ" onChange={e=>upload(e,'image')}/></label></div>
    {reference&&<img className="mdmz-reference" src={'/api/assets/'+reference.id} alt="Referencia del personaje"/>}
    <div className="mdmz-fields"><label>Inicio · segundos<input type="number" min="0" max="3600" step="0.041667" value={form.start_seconds} onChange={e=>update('start_seconds',Number(e.target.value))}/></label>
     <label>Duración<select value={form.frames} onChange={e=>update('frames',Number(e.target.value))}>{fullFrames&&<option value={fullFrames}>{form.start_seconds?'Hasta el final':'Vídeo completo'} · {(fullFrames/24).toFixed(2)} s</option>}{(options?.frame_choices||[22,56,107,124,158,209,243,294,345,362]).filter(frames=>frames!==fullFrames).map(frames=><option key={frames} value={frames} disabled={video&&Math.round(form.start_seconds*24)+frames>Math.round(video.duration*24)+1}>{(frames/24).toFixed(2)} s · {frames} fotogramas</option>)}</select></label>
     <label>Modo<select value={form.speed} onChange={e=>update('speed',e.target.value)}><option value="normal">Normal · 20 pasos</option><option value="turbo">Turbo · 8 pasos</option></select></label>
     <label>Fondo e iluminación<select value={form.background} onChange={e=>update('background',e.target.value)}><option value="video">Conservar del vídeo</option><option value="image">Tomar de la imagen</option></select></label>
     <label>Tamaño<select value={form.megapixels} onChange={e=>update('megapixels',Number(e.target.value))}><option value="0.5">Vista previa · 0,5 MP</option><option value="1">Calidad completa · 1 MP</option></select></label>
     <label>Semilla<input type="number" min="0" max="9007199254740991" value={form.seed} onChange={e=>update('seed',Number(e.target.value))}/></label></div>
    <label>Quién se reemplaza · SAM3<input maxLength="300" value={form.target} onChange={e=>update('target',e.target.value)} placeholder="person, woman in red…"/></label>
    <label className="mdmz-checkbox"><input type="checkbox" checked={form.audio} onChange={e=>update('audio',e.target.checked)}/>Usar el sonido del vídeo para guiar H3</label>
    <small>El resultado conserva la pista original cuando existe. La sincronización labial requiere revisión.</small>
    <details><summary>Guía de movimiento</summary><label>Preparación de la fuente<select value={form.preparation} onChange={e=>update('preparation',e.target.value)}><option value="native">Original · máscara antes de reducir</option><option value="legacy">Anterior · reducir primero</option></select></label><div className="mdmz-fields"><label>Resolución de la guía · MP<input type="number" min="0.1" max="0.4" step="0.05" value={form.guide_megapixels} onChange={e=>update('guide_megapixels',Number(e.target.value))}/></label><label>Umbral SAM3<input type="number" min="0.05" max="0.95" step="0.05" value={form.threshold} onChange={e=>update('threshold',Number(e.target.value))}/></label></div></details>
    <label>Prompt MDMZ · editable<textarea rows="12" maxLength="12000" value={form.prompt} onChange={e=>update('prompt',e.target.value)}/></label>
   </fieldset>
   <section className="mdmz-qwen" aria-label="Prompt con Qwen local"><h3>Qwen3.8 27B · Director del prompt</h3><p>Analiza muestras temporales del tramo y la referencia. Registra la orientación de torso y cabeza, incluidos los giros. No escucha el audio.</p>
    <button type="button" className="secondary" disabled={blocked||!video||!reference||!Number.isInteger(form.frames)||form.start_seconds+form.frames/24>(video?.duration||0)+1/24} onClick={analyze}>{analyzing?'Analizando con Qwen…':'Analizar y proponer prompt'}</button>
    {analysis&&<p role="status">{analysis.stage}{analysis.error?' · '+analysis.error:''}</p>}
    {analysis?.status==='completed'&&matches&&<><p>{analysis.proposal?.observations}</p>{analysis.proposal?.motion_timeline&&<details><summary>Orientación observada por muestra</summary><ol>{analysis.proposal.motion_timeline.map(entry=><li key={entry.sample_index}>{((analysis.sample_times?.[entry.sample_index]||0)-(analysis.request?.start_seconds||0)).toFixed(2)} s · Torso: {entry.torso} · Cabeza: {entry.head}<br/>{entry.action}</li>)}</ol></details>}{analysis.proposal?.warnings?.map((warning,i)=><p className="image-hint" key={i}>{warning}</p>)}<label>Propuesta para revisar<textarea rows="10" maxLength="12000" value={proposal} onChange={e=>setProposal(e.target.value)}/></label><button type="button" className="secondary" disabled={analyzing||!analysis.vram_released||analysis.proposal?.needs_clarification||!mdmzPromptValid(proposal)} onClick={()=>update('prompt',proposal)}>Aplicar propuesta</button></>}
    {analysis?.status==='completed'&&!matches&&<p>Los archivos o ajustes cambiaron. Analiza el tramo actual para aplicar una propuesta.</p>}
   </section>
   {!ready&&options&&<p className="notice error">{options.presets?.[form.speed]?.errors?.join(' · ')}</p>}
   {localError&&<p className="notice error" role="alert">{localError}</p>}
   {!options&&<button type="button" className="secondary" onClick={()=>setRetry(v=>v+1)}>Comprobar MDMZ</button>}
   <button type="button" className="secondary" disabled={blocked||!valid||!ready||!available||Boolean(pending)} onClick={()=>send(true)}>Revisar máscara y guía</button>
   <button type="button" className="primary" disabled={blocked||!pending&&(!valid||!ready||!available)} onClick={()=>send(false)}>{sending?'Enviando…':pending?'Confirmar solicitud pendiente':'Generar con MDMZ'}<span aria-hidden="true">↗</span></button>
   {job&&terminal&&onReuse&&<button type="button" className="secondary" disabled={blocked} onClick={onReuse}>Usar ajustes en otra toma</button>}
  </section>
  <section className="panel mdmz-result" aria-label="Resultado MDMZ">{job?.mdmz_diagnostics&&<details open={job.parameters.operation==='mdmz_diagnostics'}><summary>Fuente · Máscara SAM3 · Guía invertida</summary><p>Izquierda: fuente. Centro: máscara blanca del sujeto. Derecha: guía que recibe H3. Revisa el tramo del giro.</p><video controls playsInline preload="metadata" style={{width:'100%'}} src={'/api/mdmz/jobs/'+job.id+'/diagnostics'}/></details>}{job?.mdmz_effective_prompt&&<details><summary>Prompt final enviado a H3</summary><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{job.mdmz_effective_prompt}</pre></details>}{job?.result_available?<ResultViewer job={job} assets={assets} method="MDMZ Character Swap"/>:<div className="mdmz-empty"><h2>{job?job.stage:'Tu personaje, en movimiento.'}</h2><p>{job?'La toma se conserva en tu historial.':'El resultado aparecerá aquí y en tu historial de vídeos.'}</p></div>}{job&&<><JobElapsed job={job}/><JobCancelButton job={job} onCancel={onCancel}/>{job.error&&<p className="notice error" role="alert">{job.error}</p>}</>}</section>
 </div>;
}
