import {ENGINE,LABEL,isViggle,MAX_SEED,valid,validCharacters,requestBody} from './viggleState.js';
import {StudioSlot} from './StudioWorkspace.jsx';
import React,{useEffect,useRef,useState,useId} from 'react';
import StudioStep from './StudioStep.jsx';
import ObjectSwapGuide,{SwapStage} from './ObjectSwapGuide.jsx';
import {sourceWindowStep,referenceStep} from './objectSwapGuide.js';
import ResultViewer from './ResultViewer.jsx';
import JobCancelButton from './JobCancelButton.jsx';
import JobElapsed from './JobElapsed.jsx';
import {workspaceLocalStorage as storage,activeWorkspaceId} from './workspaceStorage.js';
import {keys,pendingRequest,rememberRequest,acknowledge} from './viggleState.js';
import {MAX_FRAMES,remainingFrames} from './viggleState.js';
import './unifiedMethods.css';
import './pikaVideo.css';
import './viggleMulti.css';
const terminal=new Set(['completed','failed','cancelled','submission_unknown']);
async function api(path,options){const r=await fetch('/api'+path,options);if(r.headers.get('X-Gen-Studio-Workspace')&&r.headers.get('X-Gen-Studio-Workspace')!==activeWorkspaceId()){window.location.reload();throw Error('Actualizando tu espacio.');}const data=await r.json();if(!r.ok){const e=Error(typeof data.detail==='string'?data.detail:'Revisa los datos de la toma.');e.status=r.status;throw e;}return data;}
function initial(mode,job,selection,method){
 const defaults={video_id:selection.video||'',reference_id:selection.image||'',prompt:'Reemplaza al personaje principal por la referencia conservando el movimiento y el escenario original.',target:'main foreground performer',frames:120,start_seconds:0,format:'landscape',seed:42,short_edge:736,reference_mode:'appearance',pin_frame:null,additional_characters:[],auto_keyframes:false,auto_detail_corrections:false};
 let saved={};try{saved=pendingRequest(storage,mode,method)||job?.parameters?.viggle_request||JSON.parse(storage.getItem(keys(mode,method).draft))||{};}catch{}
 return Object.fromEntries(Object.keys(defaults).map(k=>[k,saved[k]??defaults[k]]));
}
export default function ViggleVideo({mode='swap',method=ENGINE,assets,selection={},job,jobs=[],busy,available=true,uploading,onUpload,onCreated,onCancel,onOpen,onError,onReviewSaved}){
 const [p,setP]=useState(()=>initial(mode,job,selection,method)),[cap,setCap]=useState(null),[sending,setSending]=useState(false),[error,setError]=useState('');
 const pending=useRef(null),live=useRef(true),id=useId();let recoveryError='';try{pending.current=pendingRequest(storage,mode,method);}catch(e){recoveryError=e.message;}
 const active=Boolean(job&&!terminal.has(job.status)),disabled=active||sending||Boolean(pending.current);
 const video=assets.find(a=>a.id===p.video_id),image=assets.find(a=>a.id===p.reference_id);
 const maxFrames=Math.min(cap?.max_frames||MAX_FRAMES,MAX_FRAMES);
 const remaining=remainingFrames(video?.duration,p.start_seconds,maxFrames);
 const label=mode==='swap'?'Object Swap':'Motion Transfer';
 const formats=cap?.formats||[];
 const extras=p.additional_characters||[],multi=extras.length>0;
 const updateCharacter=(index,key,value)=>setP(current=>({...current,additional_characters:current.additional_characters.map((c,i)=>i===index?{...c,[key]:value}:c)}));
 useEffect(()=>{live.current=true;api('/engines/viggle-remake/capabilities').then(v=>{if(live.current)setCap(v);}).catch(e=>{if(live.current)setError(e.message);});return()=>{live.current=false;};},[]);
 useEffect(()=>{storage.setItem(keys(mode,method).draft,JSON.stringify(p));},[p,mode,method]);
 const update=(key,value)=>setP(current=>({...current,[key]:value}));
 async function upload(e,kind,index=-1){const input=e.currentTarget,file=input.files?.[0];if(!file)return;try{const a=await onUpload(file,kind,{select:false});if(live.current){if(index>=0)updateCharacter(index,'reference_id',a.id);else setP(current=>({...current,[kind==='video'?'video_id':'reference_id']:a.id,...(kind==='video'?{start_seconds:0,frames:Math.min(120,Math.max(5,Math.round((a.duration||5)*24)))}:{})}));}}catch(e){onError(e.message);}finally{input.value='';}}
 async function submit(e){e.preventDefault();if(sending)return;setSending(true);setError('');try{pending.current=rememberRequest(storage,mode,p,crypto.randomUUID(),method);const result=await api('/viggle-remake/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(pending.current)});acknowledge(storage,mode,pending.current,result,method);pending.current=null;onCreated(result);}catch(e){if(live.current){setError(e.message);if(e.status&&e.status<500){storage.removeItem(keys(mode,method).pending);pending.current=null;}}}finally{if(live.current)setSending(false);}}
 const guideSteps=[
  sourceWindowStep(video,p.start_seconds,p.frames,remaining),
  referenceStep(image,{title:'Personajes y referencias',help:'Describe a cada persona del original y asígnale su propia referencia.',
    summary:[{target:p.target,reference_id:p.reference_id},...extras].map(c=>c.target+' → '+(assets.find(a=>a.id===c.reference_id)?.name||'Sin imagen')),
    issue:!p.target.trim()||/[<>]/.test(p.target)||!validCharacters(p)||extras.some(c=>!assets.some(a=>a.id===c.reference_id&&a.kind==='image'))?'Revisa la descripción y la imagen de cada persona. Cada asignación necesita una descripción distinta.':
      p.reference_mode==='painted_frame'&&(!Number.isInteger(p.pin_frame)||p.pin_frame<0||p.pin_frame>=p.frames)?'El fotograma editado debe estar dentro del intervalo.':''}),
  {id:'scene',summary:p.prompt,issue:p.prompt.trim().length<3||/[<>]/.test(p.prompt)?'Escribe indicaciones de al menos tres caracteres, sin los signos < y >.':''},
  {id:'quality',title:'Calidad y continuidad',help:'Elige los ajustes con los que se generará el intervalo.',
    summary:'736p · '+(p.auto_keyframes?'Proporción original':p.format==='portrait'?'Vertical':'Horizontal'),
    issue:!Number.isSafeInteger(p.seed)||p.seed<0||p.seed>MAX_SEED?'Ajusta la semilla a un entero válido.':cap?.ok&&!formats.some(f=>f.id===p.format)?'Elige un formato disponible.':''},
  {id:'review',issue:busy?'Espera a que termine el trabajo en curso.':!available?'Consulta el estado del motor en Sistema.':''}
 ];
 const previous=jobs.filter(j=>j.id!==job?.id&&isViggle(j)&&j.parameters.mode===mode&&!j.parameters.sequence_parent_id);
 return <div className="unified-method-layout pika-studio">
  <ObjectSwapGuide enabled={mode==='swap'&&!job} recovery={Boolean(pending.current||recoveryError)} scope={method} sourceId={p.video_id} steps={guideSteps} busy={sending||Boolean(uploading)}>
  <form className="panel bunny-form pika-composer" data-studio-pane="edit" onSubmit={submit}>
   <div className="composer-heading"><div><h2>{label} con Viggle</h2><p>{LABEL}</p></div><span className="local-pill">LOCAL</span></div>
   <StudioStep guideStep="source" number={1} title="Vídeo original" summary={video?.name||'La guía de movimiento'} defaultOpen>
    <label htmlFor={id+'video'}>Subir vídeo<input id={id+'video'} type="file" accept="video/*" disabled={disabled||Boolean(uploading)} onChange={e=>upload(e,'video')}/></label>
    <label>Vídeo de mi biblioteca<select value={p.video_id} disabled={disabled} onChange={e=>update('video_id',e.target.value)}><option value="">Selecciona un vídeo</option>{assets.filter(a=>a.kind==='video').map(a=><option key={a.id} value={a.id}>{a.name||a.id.slice(0,8)}</option>)}</select></label>
    {p.video_id&&<video controls playsInline preload="metadata" src={'/api/assets/'+p.video_id} aria-label="Vídeo original seleccionado"/>}
    <div className="parameter-grid"><label>Inicio (segundos)<input type="number" min="0" step="any" value={p.start_seconds} disabled={disabled} onChange={e=>update('start_seconds',Number(e.target.value))}/></label><label>Duración (segundos)<input type="number" min={Number((5/24).toFixed(4))} max={Number((remaining/24).toFixed(4))} step="any" value={Number((p.frames/24).toFixed(4))} disabled={disabled} onChange={e=>update('frames',Math.round(Number(e.target.value)*24))}/></label></div>
    <button className="secondary" type="button" disabled={disabled||!video||remaining<5} onClick={()=>update('frames',remaining)}>{p.start_seconds===0?'Usar vídeo completo':'Usar todo el intervalo disponible'}</button>
    <p className="composer-hint">Hasta {maxFrames/24} segundos por toma, procesados en ventanas nativas con continuidad entre ellas.</p>
   </StudioStep>
   <StudioStep guideStep="references" number={2} title={multi?"Personajes de referencia":"Personaje de referencia"} summary={multi?`${extras.length+1} asignaciones de personaje`:image?.name||'Una imagen para la identidad'} defaultOpen>
    <label>Personaje original 1<input value={p.target} maxLength={300} disabled={disabled} onChange={e=>update('target',e.target.value)} placeholder="Por ejemplo: la persona con camiseta roja, inicialmente a la izquierda"/></label>
    <label htmlFor={id+'image'}>Subir imagen<input id={id+'image'} type="file" accept="image/png,image/jpeg,image/webp" disabled={disabled||Boolean(uploading)} onChange={e=>upload(e,'image')}/></label>
    <label>Imagen de mi biblioteca<select value={p.reference_id} disabled={disabled} onChange={e=>update('reference_id',e.target.value)}><option value="">Selecciona una imagen</option>{assets.filter(a=>a.kind==='image').map(a=><option key={a.id} value={a.id}>{a.name||a.id.slice(0,8)}</option>)}</select></label>
    {p.reference_id&&<img className="bunny-reference" src={'/api/assets/'+p.reference_id} alt="Personaje de referencia"/>}
    <label>Tipo de referencia<select value={p.reference_mode} disabled={disabled||multi} onChange={e=>setP(current=>({...current,reference_mode:e.target.value,pin_frame:e.target.value==='painted_frame'?0:null,auto_detail_corrections:e.target.value==='painted_frame'?false:current.auto_detail_corrections,auto_keyframes:e.target.value==='painted_frame'?false:current.auto_keyframes}))}><option value="appearance">Apariencia del personaje</option><option value="painted_frame">Fotograma del vídeo ya editado</option></select></label>
    {p.reference_mode==='painted_frame'&&<label>Momento del fotograma editado (segundos desde el inicio seleccionado)<input type="number" min="0" max={Number(((p.frames-1)/24).toFixed(4))} step="any" value={p.pin_frame===null?'':Number((p.pin_frame/24).toFixed(4))} disabled={disabled} onChange={e=>update('pin_frame',Math.round(Number(e.target.value)*24))}/></label>}
    <p className="composer-hint">Una imagen de apariencia define la identidad, sin imponer su pose. Usa «Fotograma ya editado» sólo si la imagen corresponde a ese instante del vídeo.</p>
    {extras.map((character,index)=><fieldset key={index} disabled={disabled} className="viggle-character">
     <legend>Personaje {index+2}</legend>
     <label>Personaje original que reemplazas<input value={character.target} maxLength={300} onChange={e=>updateCharacter(index,'target',e.target.value)} placeholder="Describe ropa y posición inicial para distinguirlo"/></label>
     <label>Subir imagen de reemplazo<input type="file" accept="image/png,image/jpeg,image/webp" disabled={Boolean(uploading)} onChange={e=>upload(e,'image',index)}/></label>
     <label>Imagen de mi biblioteca<select value={character.reference_id} onChange={e=>updateCharacter(index,'reference_id',e.target.value)}><option value="">Selecciona una imagen</option>{assets.filter(a=>a.kind==='image').map(a=><option key={a.id} value={a.id}>{a.name||a.id.slice(0,8)}</option>)}</select></label>
     {character.reference_id&&<img className="bunny-reference" src={'/api/assets/'+character.reference_id} alt={'Reemplazo del personaje '+(index+2)}/>}
     <button type="button" className="secondary" disabled={Boolean(uploading)} onClick={()=>update('additional_characters',extras.filter((_,i)=>i!==index))}>Quitar personaje {index+2}</button>
    </fieldset>)}
    {cap?.multi_character?.enabled&&<button type="button" className="secondary" disabled={disabled||Boolean(uploading)||extras.length>=3||p.reference_mode!=='appearance'} onClick={()=>update('additional_characters',[...extras,{target:'',reference_id:''}])}>+ Añadir personaje</button>}
    {multi&&<p className="composer-hint">Reemplazo múltiple experimental: una imagen de apariencia por asignación, hasta cuatro. Describe cada personaje original de forma distinta; su identidad debe conservarse aunque se crucen o intercambien de posición. Qwen revisará cada asignación. Con fotogramas automáticos, Qwen Image Edit prepara la composición y Qwen3.8 27B revisa cada personaje antes de continuar.</p>}
   </StudioStep>
   <StudioStep guideStep="scene" number={3} title="Indicaciones" summary="Qwen analiza el vídeo completo y sus detalles" defaultOpen>
    <label>¿Qué quieres conservar o corregir?<textarea rows={5} maxLength={3000} value={p.prompt} disabled={disabled} onChange={e=>update('prompt',e.target.value)} placeholder="Conserva el giro, los apoyos y el escenario original."/></label>
    <p className="composer-hint">Qwen analiza primero el vídeo completo y combina encuadre general y detalles para preparar indicaciones de postura, movimiento y sombras. Puedes responder sus dudas en el chat de la toma.</p>
   </StudioStep>
   <StudioStep guideStep="quality" number={4} title="Calidad y continuidad" summary="736p · Continuidad nativa" defaultOpen>
    {cap?.painted_keyframes?.enabled&&<label className="check"><input type="checkbox" checked={Boolean(p.auto_keyframes)} disabled={disabled||p.reference_mode!=='appearance'} onChange={e=>setP(current=>({...current,auto_keyframes:e.target.checked,auto_detail_corrections:e.target.checked&&current.auto_detail_corrections}))}/>Preparar y validar fotogramas con Qwen</label>}
    {p.auto_keyframes&&cap?.detail_corrections?.enabled&&<label className="check"><input type="checkbox" checked={Boolean(p.auto_detail_corrections)} disabled={disabled} onChange={e=>update('auto_detail_corrections',e.target.checked)}/>Corregir detalles con Qwen · Experimental</label>}
    {p.auto_keyframes&&p.auto_detail_corrections&&<p className="composer-hint">Qwen compara los gestos del plano, dirige cambios locales con Qwen Image y revisa cada resultado. Conserva el estilo de cada referencia. Usa el chat de la toma para indicar correcciones; una versión nueva las vuelve a evaluar. Máximo dos intentos por detalle.</p>}
    {p.auto_keyframes&&<p className="composer-hint">Conserva la proporción del vídeo original y todo el encuadre; sólo añade bandas si la proporción excede el lienzo admitido. Qwen Image Edit compone los personajes en un fotograma del tramo; Qwen3.8 27B revisa identidades y escenario; una comprobación independiente contrasta posiciones y apoyos. Se permiten dos intentos de imagen. Si la revisión del vídeo falla o no es concluyente, se detiene antes de continuar. Este proceso tarda más.</p>}
    <div className="parameter-grid pika-quality-grid"><label>Resolución<select value="736" disabled><option value="736">736p · Alta calidad</option></select></label><label>Formato<select value={p.format} disabled={disabled||p.auto_keyframes} onChange={e=>update('format',e.target.value)}>{p.auto_keyframes?<option value={p.format}>Proporción original · Lado corto 736 px</option>:formats.map(f=><option key={f.id} value={f.id}>{f.id==='portrait'?'Vertical':'Horizontal'} · {f.width} × {f.height}</option>)}</select></label><label>Semilla<input type="number" min="0" max={MAX_SEED} step="1" value={p.seed} disabled={disabled} onChange={e=>update('seed',Number(e.target.value))}/></label></div>
    <p className="composer-hint">Resolución nativa. Las continuaciones del mismo plano conservan 17 fotogramas de contexto del tramo anterior; se recortan al unir el vídeo y se reinician en los cambios de plano. Se conserva el audio original.</p>
    <p className="method-info">Este método reemplaza al personaje siguiendo el movimiento y el escenario del vídeo original. Revisa identidad, manos, apoyos y uniones. {p.auto_keyframes?'El ajuste conserva el encuadre completo.':'Sin fotogramas automáticos, el formato elegido puede recortar el encuadre.'}</p>
   </StudioStep>
   {!cap?.ok&&<p role="status">{cap?.errors?.join(' · ')||'Comprobando disponibilidad…'}</p>}{(error||recoveryError)&&<p role="alert">{error||recoveryError}</p>}
   <SwapStage step="review"><StudioSlot name="actions"><button className="primary" type="submit" disabled={sending||Boolean(recoveryError)||(!pending.current&&(disabled||busy||uploading||!available||!cap?.ok||(multi&&!cap?.multi_character?.enabled)||(p.auto_keyframes&&!cap?.painted_keyframes?.enabled)||(p.auto_detail_corrections&&!cap?.detail_corrections?.enabled)||!formats.some(f=>f.id===p.format)||!p.video_id||!p.reference_id||p.prompt.trim().length<3||!p.target.trim()||p.frames<5||p.frames>remaining||!valid(requestBody(mode,p,'00000000-0000-0000-0000-000000000001'))))}>{sending?'Enviando…':pending.current?'Recuperar envío':job?'Generar otra toma':'Generar con Viggle'}</button></StudioSlot></SwapStage>
  </form></ObjectSwapGuide>
  <StudioSlot name="result"><section className="panel bunny-result pika-result" data-studio-pane="result" aria-label={'Resultado Viggle '+label}>
   <div className="panel-title"><h2>{job?'Tu toma':'Vista del resultado'}</h2>{job&&<JobElapsed job={job}/>}</div>
   {job?<><p role="status">{job.stage}</p>{job.progress?.max>0&&<progress max={job.progress.max} value={job.progress.value}/>}<JobCancelButton job={job} onCancel={onCancel}/>{job.error&&<p role="alert">{job.error}</p>}{(job.viggle_anchor_ready||job.viggle_anchor_job_id)&&<details><summary>Fotograma editado y revisado</summary><img style={{maxWidth:'100%'}} src={'/api/viggle-remake/jobs/'+job.id+'/keyframe?child='+(job.viggle_anchor_job_id||job.id)} alt="Referencia editada del tramo, revisada por Qwen"/></details>}{job.viggle_review_summary&&<p>{job.viggle_review_summary}</p>}{job.result_available&&<ResultViewer job={job} assets={assets} method={LABEL+' · '+label} onReviewSaved={onReviewSaved}/>}<details><summary>Datos de generación</summary><p>{LABEL} · {job.parameters.resolution} · Semilla {job.parameters.seed} · 6 pasos · Continuidad nativa de 17 fotogramas</p>{job.viggle_effective_prompt&&<p className="qwen-notes">{job.viggle_effective_prompt}</p>}</details></>:<div className="pika-empty"><strong>Tu nueva toma aparecerá aquí</strong><p>Elige el vídeo, la referencia y las indicaciones.</p></div>}
   {previous.length>0&&<div className="pika-recent"><h3>Tomas recientes</h3>{previous.slice(0,5).map(j=><button className="secondary" key={j.id} onClick={()=>onOpen(j.id)}>{new Date(j.created_at).toLocaleString('es-MX')} · {j.id.slice(0,8)}</button>)}</div>}
  </section></StudioSlot>
 </div>;
}
