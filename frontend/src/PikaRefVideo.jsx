import {PIKA_DETAILED,PIKA_DETAIL_LABEL,pikaRefMethod,pikaRefLabel,pikaRefEndpoint} from './pikaRefMethods.js';
import {StudioSlot} from './StudioWorkspace.jsx';
import React,{useEffect,useRef,useState,useId} from 'react';
import StudioStep from './StudioStep.jsx';
import ObjectSwapGuide,{SwapStage} from './ObjectSwapGuide.jsx';
import {sourceWindowStep,referenceStep} from './objectSwapGuide.js';
import ResultViewer from './ResultViewer.jsx';
import JobCancelButton from './JobCancelButton.jsx';
import JobElapsed from './JobElapsed.jsx';
import {workspaceLocalStorage as storage,activeWorkspaceId} from './workspaceStorage.js';
import {keys,pendingRequest,rememberRequest,acknowledge} from './pikaRefState.js';
import {PROFILE,profileTier,MAX_FRAMES,remainingFrames} from './pikaRefState.js';
import './unifiedMethods.css';
import './pikaVideo.css';
const terminal=new Set(['completed','failed','cancelled','submission_unknown']);
async function api(path,options){const r=await fetch('/api'+path,options);if(r.headers.get('X-Gen-Studio-Workspace')&&r.headers.get('X-Gen-Studio-Workspace')!==activeWorkspaceId()){window.location.reload();throw Error('Actualizando tu espacio.');}const data=await r.json();if(!r.ok){const e=Error(typeof data.detail==='string'?data.detail:'Revisa los datos de la toma.');e.status=r.status;throw e;}return data;}
function initial(mode,job,selection,method){
 const defaults={video_id:selection.video||'',reference_id:selection.image||'',prompt:mode==='swap'?'Reemplaza al personaje principal conservando el movimiento y el escenario original.':'',target:'main foreground performer',frames:120,start_seconds:0,format:'portrait',seed:42,profile:PROFILE};
 let saved={};try{saved=pendingRequest(storage,mode,method)||job?.parameters?.pika_ref2va_request||JSON.parse(storage.getItem(keys(mode,method).draft))||{};}catch{}
 return Object.fromEntries(Object.keys(defaults).map(k=>[k,saved[k]??defaults[k]]));
}
export default function PikaRefVideo({mode='swap',method='pika_ref2va',assets,selection={},job,jobs=[],busy,available=true,uploading,onUpload,onCreated,onCancel,onOpen,onError,onReviewSaved}){
 const [p,setP]=useState(()=>initial(mode,job,selection,method)),[cap,setCap]=useState(null),[sending,setSending]=useState(false),[error,setError]=useState('');
 const pending=useRef(null),live=useRef(true),id=useId();let recoveryError='';try{pending.current=pendingRequest(storage,mode,method);}catch(e){recoveryError=e.message;}
 const active=Boolean(job&&!terminal.has(job.status)),disabled=active||sending||Boolean(pending.current);
 const video=assets.find(a=>a.id===p.video_id),image=assets.find(a=>a.id===p.reference_id);
 const maxFrames=Math.min(cap?.max_frames||MAX_FRAMES,MAX_FRAMES);
 const remaining=remainingFrames(video?.duration,p.start_seconds,maxFrames);
 const label=mode==='swap'?'Object Swap':'Motion Transfer';
 const profiles=cap?.profiles||[{id:PROFILE,label:'480p · Estándar',tier:'480p',formats:cap?.formats||[{id:'portrait',width:480,height:832}]}];
 const selectedProfile=profiles.find(item=>item.id===p.profile);
 const formats=selectedProfile?.formats||[];
 useEffect(()=>{live.current=true;api('/engines/'+pikaRefEndpoint(method)+'/capabilities').then(v=>{if(live.current)setCap(v);}).catch(e=>{if(live.current)setError(e.message);});return()=>{live.current=false;};},[]);
 useEffect(()=>{storage.setItem(keys(mode,method).draft,JSON.stringify(p));},[p,mode,method]);
 const update=(key,value)=>setP(current=>({...current,[key]:value}));
 async function upload(e,kind){const input=e.currentTarget,file=input.files?.[0];if(!file)return;try{const a=await onUpload(file,kind,{select:false});if(live.current)setP(current=>({...current,[kind==='video'?'video_id':'reference_id']:a.id,...(kind==='video'?{start_seconds:0,frames:Math.min(120,Math.max(5,Math.round((a.duration||5)*24)))}:{})}));}catch(e){onError(e.message);}finally{input.value='';}}
 async function submit(e){e.preventDefault();if(sending)return;setSending(true);setError('');try{pending.current=rememberRequest(storage,mode,p,crypto.randomUUID(),method);const result=await api('/'+pikaRefEndpoint(method)+'/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(pending.current)});acknowledge(storage,mode,pending.current,result,method);pending.current=null;onCreated(result);}catch(e){if(live.current){setError(e.message);if(e.status&&e.status<500){storage.removeItem(keys(mode,method).pending);pending.current=null;}}}finally{if(live.current)setSending(false);}}
 const guideSteps=[
  sourceWindowStep(video,p.start_seconds,p.frames,remaining),
  {id:'cast',title:'Persona que vas a reemplazar',help:'Describe a la persona del vídeo original. Si hay varias, indica su ropa o su posición.',summary:p.target,issue:!p.target.trim()&&'Describe a la persona que quieres reemplazar.'},
  referenceStep(image),
  {id:'scene',summary:p.prompt,issue:p.prompt.trim().length<3&&'Escribe al menos tres caracteres para describir el cambio.'},
  {id:'quality',title:'Calidad y continuidad',help:'Elige los ajustes con los que se generará el intervalo.',
    summary:profileTier(p.profile)+' · '+(p.format==='portrait'?'Vertical':'Horizontal'),
    issue:!Number.isSafeInteger(p.seed)||p.seed<0?'Ajusta la semilla a un entero válido.':cap?.ok&&(!selectedProfile||!formats.some(f=>f.id===p.format))?'Elige una resolución y un formato disponibles.':''},
  {id:'review',issue:busy?'Espera a que termine el trabajo en curso.':!available?'Consulta el estado del motor en Sistema.':''}
 ];
 const previous=jobs.filter(j=>j.id!==job?.id&&pikaRefMethod(j)===method&&j.parameters.mode===mode&&!j.parameters.sequence_parent_id);
 return <div className="unified-method-layout pika-studio">
  <ObjectSwapGuide enabled={mode==='swap'&&!job} recovery={Boolean(pending.current||recoveryError)} scope={method} sourceId={p.video_id} steps={guideSteps} busy={sending||Boolean(uploading)}>
  <form className="panel bunny-form pika-composer" data-studio-pane="edit" onSubmit={submit}>
   <div className="composer-heading"><div><h2>{label} con Pika</h2><p>{method===PIKA_DETAILED?PIKA_DETAIL_LABEL:'Pika H3 Flex · Experimental'}</p></div><span className="local-pill">LOCAL</span></div>
   <StudioStep guideStep="source" number={1} title="Vídeo original" summary={video?.name||'La guía de movimiento'} defaultOpen>
    <label htmlFor={id+'video'}>Subir vídeo<input id={id+'video'} type="file" accept="video/*" disabled={disabled||Boolean(uploading)} onChange={e=>upload(e,'video')}/></label>
    <label>Vídeo de mi biblioteca<select value={p.video_id} disabled={disabled} onChange={e=>update('video_id',e.target.value)}><option value="">Selecciona un vídeo</option>{assets.filter(a=>a.kind==='video').map(a=><option key={a.id} value={a.id}>{a.name||a.id.slice(0,8)}</option>)}</select></label>
    {p.video_id&&<video controls playsInline preload="metadata" src={'/api/assets/'+p.video_id} aria-label="Vídeo original seleccionado"/>}
    <div className="parameter-grid"><label>Inicio (segundos)<input type="number" min="0" step="any" value={p.start_seconds} disabled={disabled} onChange={e=>update('start_seconds',Number(e.target.value))}/></label><label>Duración (segundos)<input type="number" min={Number((5/24).toFixed(4))} max={Number((remaining/24).toFixed(4))} step="any" value={Number((p.frames/24).toFixed(4))} disabled={disabled} onChange={e=>update('frames',Math.round(Number(e.target.value)*24))}/></label></div>
    <button className="secondary" type="button" disabled={disabled||!video||remaining<5} onClick={()=>update('frames',remaining)}>{p.start_seconds===0?'Usar vídeo completo':'Usar todo el intervalo disponible'}</button>
    <p className="composer-hint">Hasta {maxFrames/24} segundos por toma, procesados en tramos de hasta 5 segundos.</p>
   </StudioStep>
   <StudioStep guideStep="references" number={2} title="Personaje de referencia" summary={image?.name||'Una imagen para la identidad'} defaultOpen>
    <label htmlFor={id+'image'}>Subir imagen<input id={id+'image'} type="file" accept="image/png,image/jpeg,image/webp" disabled={disabled||Boolean(uploading)} onChange={e=>upload(e,'image')}/></label>
    <label>Imagen de mi biblioteca<select value={p.reference_id} disabled={disabled} onChange={e=>update('reference_id',e.target.value)}><option value="">Selecciona una imagen</option>{assets.filter(a=>a.kind==='image').map(a=><option key={a.id} value={a.id}>{a.name||a.id.slice(0,8)}</option>)}</select></label>
    {p.reference_id&&<img className="bunny-reference" src={'/api/assets/'+p.reference_id} alt="Personaje de referencia"/>}
   </StudioStep>
   <StudioStep guideStep={['cast','scene']} number={3} title={mode==='swap'?'Indicaciones':'Escenario e indicaciones'} summary={method===PIKA_DETAILED?'Encuadre completo + detalles del mismo instante':'Qwen analiza el vídeo completo y la postura'} defaultOpen>
    <SwapStage step="cast"><label>Personaje del vídeo que quieres reemplazar<input value={p.target} maxLength={300} disabled={disabled} onChange={e=>update('target',e.target.value)} placeholder="Por ejemplo: el perro con pechera"/></label>
    </SwapStage><SwapStage step="scene"><label>{mode==='swap'?'¿Qué quieres conservar o corregir?':'Describe la escena de destino'}<textarea rows={5} maxLength={3000} value={p.prompt} disabled={disabled} onChange={e=>update('prompt',e.target.value)} placeholder={mode==='swap'?'Conserva el fondo, el giro y los apoyos del original.':'Por ejemplo: el personaje surfea sobre una nube dorada en un cielo azul.'}/></label>
    <p className="composer-hint">{method===PIKA_DETAILED?'Qwen combina el encuadre completo con detalles del personaje en distintos instantes para describir poses, apoyos y sombras. Pika recibe el vídeo completo. El análisis tarda más; puedes corregirlo en el chat.':'Qwen prepara las indicaciones de postura, movimiento y sombras. Puedes responder sus dudas o corregirlo en el chat de la toma.'}</p></SwapStage>
   </StudioStep>
   <StudioStep guideStep="quality" number={4} title="Calidad y continuidad" summary={profileTier(p.profile)+" · Tramos de hasta 5 segundos"} defaultOpen>
    <div className="parameter-grid pika-quality-grid"><label>Resolución<select value={p.profile} disabled={disabled} onChange={e=>update('profile',e.target.value)}>{profiles.map(item=><option key={item.id} value={item.id}>{item.label}</option>)}</select></label><label>Formato<select value={p.format} disabled={disabled} onChange={e=>update('format',e.target.value)}>{formats.map(f=><option key={f.id} value={f.id}>{f.id==='portrait'?'Vertical':'Horizontal'} · {f.width} × {f.height}</option>)}</select></label><label>Semilla<input type="number" min="0" max="9007199254740991" step="1" value={p.seed} disabled={disabled} onChange={e=>update('seed',Number(e.target.value))}/></label></div>
    <p className="composer-hint">La resolución se genera de forma nativa. 768p requiere más tiempo y memoria. Cada tramo usa el último fotograma anterior como guía. La guía se reinicia al detectar un cambio de plano. Se conserva el audio original y se ajusta el encuadre sin estirarlo.</p>
    <p className="method-info">{mode==='motion'?'Experimental: pueden reaparecer el fondo o los objetos del vídeo original. Revisa también los apoyos y la continuidad.':'Revisa rostro, apoyos, fondo y uniones: el reemplazo generativo puede cambiar detalles.'}</p>
   </StudioStep>
   {!cap?.ok&&<p role="status">{cap?.errors?.join(' · ')||'Comprobando disponibilidad…'}</p>}{(error||recoveryError)&&<p role="alert">{error||recoveryError}</p>}
   <SwapStage step="review"><StudioSlot name="actions"><button className="primary" type="submit" disabled={sending||Boolean(recoveryError)||(!pending.current&&(disabled||busy||uploading||!available||!cap?.ok||!selectedProfile||!formats.some(f=>f.id===p.format)||!p.video_id||!p.reference_id||p.prompt.trim().length<3||!p.target.trim()||p.frames<5||p.frames>remaining||!Number.isSafeInteger(p.seed)||p.seed<0))}>{sending?'Enviando…':pending.current?'Recuperar envío':job?'Generar otra toma':method===PIKA_DETAILED?'Generar con Pika detallado':'Generar con Pika'}</button></StudioSlot></SwapStage>
  </form></ObjectSwapGuide>
  <StudioSlot name="result"><section className="panel bunny-result pika-result" data-studio-pane="result" aria-label={'Resultado Pika '+label}>
   <div className="panel-title"><h2>{job?'Tu toma':'Vista del resultado'}</h2>{job&&<JobElapsed job={job}/>}</div>
   {job?<><p role="status">{job.stage}</p>{job.progress?.max>0&&<progress max={job.progress.max} value={job.progress.value}/>}<JobCancelButton job={job} onCancel={onCancel}/>{job.error&&<p role="alert">{job.error}</p>}{job.result_available&&<ResultViewer job={job} assets={assets} method={pikaRefLabel(job)+' · '+label} onReviewSaved={onReviewSaved}/>}<details><summary>Datos de generación</summary><p>{pikaRefLabel(job)} · {job.parameters.resolution} · Semilla {job.parameters.seed} · 6 pasos · Tramos de 5 s</p>{job.pika_effective_prompt&&<p className="qwen-notes">{job.pika_effective_prompt}</p>}</details></>:<div className="pika-empty"><strong>Tu nueva toma aparecerá aquí</strong><p>Elige el vídeo, la referencia y las indicaciones.</p></div>}
   {previous.length>0&&<div className="pika-recent"><h3>Tomas recientes</h3>{previous.slice(0,5).map(j=><button className="secondary" key={j.id} onClick={()=>onOpen(j.id)}>{new Date(j.created_at).toLocaleString('es-MX')} · {j.id.slice(0,8)}</button>)}</div>}
  </section></StudioSlot>
 </div>;
}
