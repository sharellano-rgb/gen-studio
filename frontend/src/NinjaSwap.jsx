import {StudioSlot} from './StudioWorkspace.jsx';
import JobElapsed from './JobElapsed.jsx';
import React,{useEffect,useRef,useState} from 'react';
import {isNinja,ninjaDefaults,newNinjaForm,ninjaSourceFrames,ninjaFullTake,ninjaForm,ninjaPrepareBody,ninjaPreparation,ninjaModeLabel,ninjaLabel,ninjaHasHeadMotion,ninjaRenderBody,restoreNinjaMotion,saveNinjaMotion,clearNinjaMotion,validNinjaForm,matchesNinjaPreparation,canRenderNinja,pointInVideo,readPendingNinja,savePendingNinja,clearPendingNinja,canSendNinja} from './ninjaState.js';
import './ninjaSwap.css';
import NinjaShots from './NinjaShots.jsx';
import ResultViewer from './ResultViewer.jsx';
import {isNinjaShots} from './ninjaShotsState.js';
async function api(path,body){
  const response=await fetch('/api'+path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:undefined);
  let data;try{data=await response.json();}catch{throw Error('No se pudo confirmar la respuesta del servidor.');}
  if(!response.ok){const error=Error(typeof data.detail==='string'?data.detail:'No se pudo completar la petición de Refinamiento Facial.');error.status=response.status;throw error;}
  return data;
}
export default function NinjaSwap(props){
  const legacy=props.job&&isNinja(props.job)&&!isNinjaShots(props.job);
  const pendingLegacy=Boolean(readPendingNinja(sessionStorage,'prepare'));
  return legacy||pendingLegacy?<NinjaSingle {...props}/>:<NinjaShots {...props}/>;
}

function NinjaSingle({assets,selection,job,jobs,draftParams,busy,uploading,onUpload,onDraftChanged,onCreated,onError,onBusyChange,onReviewSaved}){
  const initialPreparation=ninjaPreparation(job,jobs);
  const context=isNinja(job)?job:null;
  const [pendingPrepare,setPendingPrepare]=useState(()=>readPendingNinja(sessionStorage,'prepare'));
  const [parent,setParent]=useState(initialPreparation);
  const initial=initialPreparation?.parameters||(context?.parameters.operation==='ninja_prepare'?context.parameters:context?.parameters.operation==='ninja_render'?null:draftParams);
  const [form,setForm]=useState(()=>(pendingPrepare||context?ninjaForm:newNinjaForm)(pendingPrepare||initial||{reference_id:selection.image||selection.face||''},assets.find(a=>a.id===(pendingPrepare?.video_id||initial?.video_id||selection.video))));
  const parentId=initialPreparation?.id||context?.parameters.ninja_parent_id||'';
  const [pendingRender,setPendingRender]=useState(()=>parentId?readPendingNinja(sessionStorage,'render',parentId):null);
  const [headMotion,setHeadMotion]=useState(()=>restoreNinjaMotion(sessionStorage,{pending:pendingRender,pendingPrepare,context,parentId,draft:draftParams}));
  const [options,setOptions]=useState(null),[optionsError,setOptionsError]=useState(''),[sending,setSending]=useState(false),[uploadTarget,setUploadTarget]=useState(''),[ready,setReady]=useState(false),[frameError,setFrameError]=useState(false),[parentError,setParentError]=useState(''),[previewReady,setPreviewReady]=useState(false),[previewError,setPreviewError]=useState(false);
  const player=useRef(null),previewPlayer=useRef(null),dirty=useRef(false),hydratedParent=useRef(initialPreparation?.id||null);
  const live=useRef(true);
  useEffect(()=>{live.current=true;return()=>{live.current=false;};},[]);
  const preparation=ninjaPreparation(context,jobs)||parent;
  const video=assets.find(a=>a.id===form.video_id&&a.kind==='video');
  const reference=assets.find(a=>a.id===form.reference_id&&a.kind==='image');
  const restoring=context?.parameters.operation==='ninja_render'&&!preparation;
  const locked=restoring||sending||Boolean(uploading)||Boolean(uploadTarget)||Boolean(pendingPrepare)||Boolean(pendingRender);
  useEffect(()=>{onBusyChange?.(locked);return()=>onBusyChange?.(false);},[locked,onBusyChange]);
  useEffect(()=>{let active=true;api('/ninja/options').then(value=>{if(active)setOptions(value);}).catch(error=>{if(active)setOptionsError(error.message);});return()=>{active=false;};},[]);
  useEffect(()=>{
    if(context?.parameters.operation!=='ninja_render')return;
    const id=context.parameters.ninja_parent_id;
    const restore=value=>{
      if(!isNinja(value)||value.parameters.operation!=='ninja_prepare'||value.id!==id)throw Error('La preparación de esta toma no coincide.');
      setParent(value);
      if(!dirty.current&&!pendingPrepare&&hydratedParent.current!==id){setForm(ninjaForm(value.parameters,assets.find(a=>a.id===value.parameters.video_id)));hydratedParent.current=id;}
    };
    const known=ninjaPreparation(context,jobs);
    if(known){restore(known);return;}
    let active=true;setParentError('');
    api('/jobs/'+id).then(value=>{if(active)restore(value);}).catch(error=>{if(active)setParentError(error.message);});
    return()=>{active=false;};
  },[context?.id,context?.parameters.ninja_parent_id,jobs]);
  useEffect(()=>{onDraftChanged?.({...form,head_motion:headMotion,engine:'h3_ninja'});},[form,headMotion,onDraftChanged]);
  useEffect(()=>{setPreviewReady(false);setPreviewError(false);},[preparation?.id,preparation?.ninja_mask_sha256]);
  function atFrame(){
    const v=player.current;return Boolean(v&&v.readyState>=2&&!v.seeking&&Math.abs(v.currentTime-form.start_seconds)<1/48);
  }
  function seek(){
    const v=player.current;if(!v)return;
    v.pause();
    if(v.readyState>=1&&Math.abs(v.currentTime-form.start_seconds)>=1/48){v.currentTime=form.start_seconds;setReady(false);}
    else setReady(atFrame());
  }
  useEffect(()=>{setReady(false);setFrameError(false);seek();},[form.video_id,form.start_seconds]);
  function loaded(){const value=atFrame();setReady(value);if(value)setFrameError(false);}
  function change(field,value){
    if(locked)return;dirty.current=true;
    setForm(current=>({...current,[field]:value,...(['start_seconds','mode','video_id'].includes(field)?{x:null,y:null}:{})}));
  }
  function selectVideo(asset){
    if(locked)return;dirty.current=true;setReady(false);
    setForm(current=>({...ninjaDefaults(asset,options?.max_frames||720),reference_id:current.reference_id,recipe:'w2',mode:current.mode,seed:current.seed}));
  }
  async function upload(event,kind){
    const input=event.currentTarget,file=input.files?.[0];if(!file)return;
    setUploadTarget(kind);
    try{
      const asset=await onUpload(file,kind);
      if(!live.current)return;
      dirty.current=true;
      if(kind==='video'){setReady(false);setForm(current=>({...ninjaDefaults(asset,options?.max_frames||720),reference_id:current.reference_id,recipe:'w2',mode:current.mode,seed:current.seed}));}
      else setForm(current=>({...current,reference_id:asset.id}));
    }catch(error){onError(error.message);}finally{if(live.current)setUploadTarget('');input.value='';}
  }
  function mark(event){
    if(locked||!ready||!video)return;
    const v=player.current;
    const point=pointInVideo(event.clientX,event.clientY,event.currentTarget.getBoundingClientRect(),v?.videoWidth||video.width,v?.videoHeight||video.height);
    if(point){dirty.current=true;setForm(current=>({...current,...point}));}
  }
  function keyboardMark(event){
    if(locked||!ready||!['Enter',' ','ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(event.key))return;
    event.preventDefault();dirty.current=true;
    setForm(current=>({...current,x:Math.max(0,Math.min(1,(current.x??.5)+(event.key==='ArrowLeft'?-.01:event.key==='ArrowRight'?.01:0))),y:Math.max(0,Math.min(1,(current.y??.5)+(event.key==='ArrowUp'?-.01:event.key==='ArrowDown'?.01:0)))}));
  }
  async function prepare(){
    if(!canSendNinja({busy,sending:locked&&!pendingPrepare,pending:pendingPrepare,valid:valid&&ready&&options?.enabled&&!restoring}))return;
    setSending(true);
    try{
      const body=pendingPrepare||readPendingNinja(sessionStorage,'prepare')||ninjaPrepareBody(form,crypto.randomUUID());
      saveNinjaMotion(sessionStorage,'request:'+body.request_key,headMotion);
      savePendingNinja(sessionStorage,'prepare','',body);setPendingPrepare(body);
      const result=await api('/ninja/prepare',body);
      saveNinjaMotion(sessionStorage,result.id,restoreNinjaMotion(sessionStorage,{pendingPrepare:body}));
      clearNinjaMotion(sessionStorage,'request:'+body.request_key);
      clearPendingNinja(sessionStorage,'prepare');setPendingPrepare(null);onCreated(result);
    }catch(error){
      if(error.status>=400&&error.status<500){const rejected=readPendingNinja(sessionStorage,'prepare');if(rejected)clearNinjaMotion(sessionStorage,'request:'+rejected.request_key);clearPendingNinja(sessionStorage,'prepare');setPendingPrepare(null);}
      onError(error.message+' Si el envío quedó sin confirmar, reintenta la misma petición.');
    }finally{if(live.current)setSending(false);}
  }
  async function render(){
    if(!preparation||!canSendNinja({busy,sending,pending:pendingRender,valid:options?.enabled&&canRenderNinja(preparation,form)&&previewReady&&!pendingPrepare&&!uploading}))return;
    setSending(true);
    try{
      const body=ninjaRenderBody(preparation,headMotion,crypto.randomUUID(),pendingRender||readPendingNinja(sessionStorage,'render',preparation.id));
      savePendingNinja(sessionStorage,'render',preparation.id,body);setPendingRender(body);
      const result=await api('/jobs/'+preparation.id+'/ninja-render',body);
      clearPendingNinja(sessionStorage,'render',preparation.id);setPendingRender(null);onCreated(result);
    }catch(error){
      if(error.status>=400&&error.status<500){clearPendingNinja(sessionStorage,'render',preparation.id);setPendingRender(null);}
      onError(error.message+' Se conserva la misma petición si falta confirmar el envío.');
    }finally{if(live.current)setSending(false);}
  }
  const valid=validNinjaForm(form,assets,options?.max_frames||720);
  const marked=typeof form.x==='number'&&typeof form.y==='number';
  const matching=matchesNinjaPreparation(form,preparation);
  const pending=Boolean(pendingPrepare||pendingRender);
  const total=ninjaSourceFrames(video);
  const explanation=form.recipe==='w1'&&!pending?'Esta toma usa una receta retirada. Puedes ver su resultado o repetirla desde el Historial con la Receta 2.':restoring?'Recuperando la preparación original de esta toma…':pending?'Hay un envío pendiente. Reintenta la misma petición para recuperar su trabajo.':!video?'1. Carga o selecciona el vídeo que quieres editar.':!reference?'2. Carga o selecciona la imagen del personaje.':frameError?'No se pudo cargar el fotograma. Vuelve a cargar la vista previa.':!ready?'Cargando el primer fotograma del intervalo…':!marked?'3. Toca a la persona en la imagen. El círculo amarillo es obligatorio.':!valid?'Revisa que el intervalo esté dentro del vídeo y que dure entre 5 y '+(options?.max_frames||720)+' fotogramas.':busy?'Persona marcada. Espera a que termine el trabajo en curso.':'Persona marcada. Prepara la máscara y revísala antes de generar.';
  return <section className={'panel ninja-swap'+(context?.result_available?' ninja-has-result':'')} aria-label="Refinamiento Facial Object Swap">
    <div data-studio-pane="edit"><div className="panel-title"><h2>Refinamiento Facial · Experimental</h2><span className="tiny">{context?.parameters.ninja_version||options?.version||'MÁSCARA + H3'}</span></div>
    <p>Selecciona una persona, revisa su máscara en movimiento y genera una copia con tu personaje.</p>
    {optionsError&&<p role="alert" className="notice error">{optionsError}</p>}
    {options&&!options.enabled&&<p role="status" className="notice">Refinamiento Facial está pendiente de activación. Puedes preparar tus archivos y seleccionar a la persona.</p>}
    {parentError&&<p role="alert" className="notice error">No se pudo recuperar la preparación: {parentError}</p>}
    <div className="ninja-grid">
      <div><label>Vídeo fuente<select aria-label="Vídeo Refinamiento Facial" disabled={locked} value={form.video_id} onChange={e=>selectVideo(assets.find(a=>a.id===e.target.value))}><option value="">Selecciona un vídeo</option>{assets.filter(a=>a.kind==='video').map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
      <label className="ninja-upload">Subir vídeo<input type="file" accept=".mp4,.mov,.webm" disabled={locked} onChange={e=>upload(e,'video')}/></label></div>
      <div><label>Personaje de referencia<select aria-label="Referencia Refinamiento Facial" disabled={locked} value={form.reference_id} onChange={e=>change('reference_id',e.target.value)}><option value="">Selecciona una imagen</option>{assets.filter(a=>a.kind==='image').map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
      <label className="ninja-upload">Subir imagen<input type="file" accept=".png,.jpg,.jpeg,.webp" disabled={locked} onChange={e=>upload(e,'image')}/></label><p>{form.mode==='body'?'Usa una foto que muestre el cuerpo, la apariencia y la ropa del personaje.':'Usa una foto clara del rostro y el cabello del personaje.'}</p>{reference&&<img className="ninja-reference" src={'/api/assets/'+reference.id} alt="Personaje de referencia para Refinamiento Facial"/>}</div>
    </div>
    <div className="ninja-grid"><label>Zona a reemplazar<select aria-label="Modalidad Refinamiento Facial" disabled={locked} value={form.mode} onChange={e=>change('mode',e.target.value)}><option value="head">Rostro y cabello</option><option value="body">Cuerpo completo</option></select></label>
      <p className="ninja-help">{form.recipe==='w1'?'Configuración histórica retirada. Para crear otra prueba, usa Repetir desde el Historial.':'Receta 2 · Workflow 2'}</p></div>
    <div className="ninja-grid"><label>Inicio (segundos)<input aria-label="Inicio Refinamiento Facial" disabled={locked} type="number" min="0" max={Math.max(0,(total-5)/24)} step="0.041666667" value={Number(form.start_seconds.toFixed(3))} onChange={e=>change('start_seconds',Math.round(Number(e.target.value)*24)/24)}/></label>
      <label>Duración (segundos)<input aria-label="Duración Refinamiento Facial" disabled={locked} type="number" min="0.208333333" max={(options?.max_frames||720)/24} step="0.041666667" value={Number((form.frames/24).toFixed(3))} onChange={e=>change('frames',Math.round(Number(e.target.value)*24))}/></label></div>
    <button className="secondary" disabled={locked||!video||total<5} onClick={()=>{dirty.current=true;setForm(current=>ninjaFullTake(current,video,options?.max_frames||720));}}>Usar vídeo completo (máximo 30 s)</button>
    <p className="ninja-help">{form.frames} fotogramas a 24 fps · {form.start_seconds.toFixed(2)}–{(form.start_seconds+form.frames/24).toFixed(2)} s. Al elegir vídeo se proponen sus últimos 5 segundos. Puedes ampliar la duración hasta 30 s. Usa un solo plano continuo, sin cortes.</p>
    {form.frames>360&&<p className="notice" role="status">Duración extendida experimental: se procesa la toma completa en una sola generación. Requiere más memoria y tiempo; revisa identidad y movimiento durante todo el vídeo.</p>}
    <p className="ninja-instruction"><strong>Marca a una persona en el primer fotograma del intervalo.</strong><br/>{form.mode==='head'?'Toca el centro del rostro que quieres reemplazar, incluido su cabello.':'Toca el torso de la persona cuyo cuerpo quieres reemplazar.'} El círculo amarillo confirma tu selección.</p>
    {video&&<div className="ninja-selector" role="button" tabIndex={locked||!ready?-1:0} aria-label="Selecciona la persona para Refinamiento Facial" aria-disabled={locked||!ready} style={{aspectRatio:(video.width||16)+'/'+(video.height||9)}} onPointerUp={mark} onKeyDown={keyboardMark}>
      <video key={video.id} ref={player} src={'/api/assets/'+video.id} playsInline muted preload="auto" onLoadedMetadata={seek} onLoadedData={loaded} onCanPlay={loaded} onSeeked={loaded} onSeeking={()=>setReady(false)} onError={()=>{setFrameError(true);setReady(false);}}/>
      {marked&&<span className="ninja-marker" style={{left:(form.x*100)+'%',top:(form.y*100)+'%'}}/>}
      <span className="ninja-hint">{frameError?'Vista previa no disponible':marked?'Persona marcada':ready?'Toca a la persona':'Cargando fotograma…'}</span>
    </div>}
    {frameError&&<button className="secondary" disabled={locked} onClick={()=>{setFrameError(false);setReady(false);player.current?.load();}}>Volver a cargar vista previa</button>}
    <p className="ninja-status" role="status" aria-live="polite">{explanation}</p>
    <details><summary>Configuración de la prueba</summary><label>Semilla<input disabled={locked} type="number" min="0" max="9007199254740991" step="1" value={form.seed} onChange={e=>change('seed',Number(e.target.value))}/></label></details>
    <button className="primary ninja-action" disabled={!canSendNinja({busy,sending:sending||Boolean(uploading)||Boolean(uploadTarget)||Boolean(pendingRender),pending:pendingPrepare,valid:options?.enabled&&valid&&ready&&!parentError&&!restoring})} onClick={prepare}>{sending?'Enviando…':pendingPrepare?'Reintentar preparación pendiente':'Preparar y revisar máscara'}</button>
    {context&&<div className="ninja-job"><strong>{ninjaLabel(context)}</strong><p role="status">{context.stage}</p><JobElapsed job={context}/>{context.progress?.max>0&&<p>{context.progress.value} / {context.progress.max}</p>}{context.error&&<p role="alert" className="error">{context.error}</p>}</div>}
    {preparation?.ninja_preview_available&&<section className="ninja-review" aria-label="Revisión de máscara Refinamiento Facial"><h3>Revisa la máscara antes de generar</h3><p>Esta vista muestra la región SAM3 seleccionada. La receta añade limpieza y márgenes suaves durante la generación. Comprueba que el seguimiento corresponde sólo a la persona elegida durante todo el tramo.</p>
      <video key={preparation.id+preparation.ninja_mask_sha256} ref={previewPlayer} src={'/api/jobs/'+preparation.id+'/ninja-preview'} controls playsInline preload="auto" onLoadedData={()=>{setPreviewReady(true);setPreviewError(false);}} onCanPlay={()=>{setPreviewReady(true);setPreviewError(false);}} onError={()=>{setPreviewReady(false);setPreviewError(true);}}/>
      {previewError?<div role="alert"><p>No se pudo cargar la máscara. Vuelve a cargar la vista previa para revisarla.</p><button className="secondary" onClick={()=>{setPreviewError(false);setPreviewReady(false);previewPlayer.current?.load();}}>Volver a cargar máscara</button></div>:!previewReady&&<p role="status">Cargando la vista previa de la máscara…</p>}
      {!matching&&<p className="notice">Cambiaste la configuración. Prepara otra máscara para estos ajustes.</p>}
      {ninjaHasHeadMotion(preparation.parameters)&&<label>Movimiento de la cabeza<select value={headMotion} disabled={locked||busy||!matching} onChange={e=>{setHeadMotion(e.target.value);saveNinjaMotion(sessionStorage,preparation.id,e.target.value);}}><option value="preserve">Priorizar movimiento del vídeo</option><option value="original">Ajuste original de la receta 2</option></select><span className="ninja-help">Priorizar movimiento conserva más de la toma original al generar el rostro. Revisa también la identidad, sobre todo si estás cambiando de persona. Puedes comparar ambos ajustes con esta misma máscara.</span></label>}
      <button className="primary ninja-action" disabled={!canSendNinja({busy,sending:sending||Boolean(uploading)||Boolean(uploadTarget)||Boolean(pendingPrepare),pending:pendingRender,valid:options?.enabled&&canRenderNinja(preparation,form)&&previewReady})} onClick={render}>{sending?'Enviando…':pendingRender?'Reintentar generación pendiente':'Generar con esta máscara'}</button>
      {context?.parameters.operation==='ninja_render'&&<button className="secondary" onClick={()=>onCreated(preparation)}>Ver preparación original</button>}
    </section>}
    </div>
    {context?.parameters.operation==='ninja_render'&&context.result_available&&<StudioSlot name="result"><section className="ninja-review" data-studio-pane="result"><h3>Tu resultado · {ninjaModeLabel(context.parameters.mode)}</h3><ResultViewer key={context.id} job={context} assets={assets} method="Refinamiento Facial" onReviewSaved={onReviewSaved}/><p>Revisa identidad, movimiento, bordes y las personas que debían conservarse.</p></section></StudioSlot>}
  </section>;
}
import {workspaceSessionStorage as sessionStorage} from './workspaceStorage.js';
