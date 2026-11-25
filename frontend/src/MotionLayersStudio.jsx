import {StudioSlot} from './StudioWorkspace.jsx';
import React,{useEffect,useRef,useState} from 'react';
import {workspaceLocalStorage as storage,activeWorkspaceId} from './workspaceStorage.js';
import {draftAssets} from './studioDrafts.js';
import {pointInImage} from './visualTargets.js';
import {LAYERS_DEFAULTS,layersFormForJob,layersIntervalValid,layersDurations,layersFullFrames,layersSelectDuration,layersDimensions,layersPointValid,layersRequest,layersIntermediateComparison,layersSourceFrame,layersAcknowledges} from './motionLayersState.js';
import StudioStep from './StudioStep.jsx';
import JobElapsed from './JobElapsed.jsx';
import JobCancelButton from './JobCancelButton.jsx';
import ResultViewer from './ResultViewer.jsx';
import ResultComparison from './ResultComparison.jsx';
import {isActiveJob} from './jobActivity.js';
import './motionStudio.css';
import './motionLayers.css';

const PENDING='motion-layers-pending-v1',DRAFT='motion-layers-draft-v1:';
function read(key){try{return JSON.parse(storage.getItem(key));}catch{return null;}}
async function data(response){
 const workspace=response.headers.get('X-Gen-Studio-Workspace');
 if(workspace&&workspace!==activeWorkspaceId()){window.location.reload();throw Error('Actualizando tu espacio de trabajo…');}
 const body=await response.json();if(!response.ok){const error=Error(typeof body.detail==='string'?body.detail:'No se pudo procesar la solicitud.');error.status=response.status;throw error;}return body;
}

export default function MotionLayersStudio({assets:libraryAssets=[],selection,job,onCreated,onError,onUpload,onCancel,onReviewSaved,onPending,busy,uploading,view='edit',onViewChange,enabled=true}){
 const own=job?.parameters?.operation==='motion_layers'?job:null,draftKey=DRAFT+(own?.id||'new');
 const [pending,setPending]=useState(()=>read(PENDING));
 const [form,setForm]=useState(()=>read(PENDING)?.form||read(draftKey)?.form||layersFormForJob(own)||{...LAYERS_DEFAULTS,video_id:selection?.video||''});
 const [boundAssets,setBoundAssets]=useState(()=>read(PENDING)?.assets||read(draftKey)?.assets||[]),[reuse,setReuse]=useState(null),[sending,setSending]=useState(false),[check,setCheck]=useState(null),[retry,setRetry]=useState(0),[loaded,setLoaded]=useState(''),[frameError,setFrameError]=useState(''),[intermediate,setIntermediate]=useState(false),[maskOpen,setMaskOpen]=useState(false),[maskError,setMaskError]=useState(false),[draftError,setDraftError]=useState('');
 const lock=useRef(false),lastJob=useRef(own?.id||null),image=useRef(null),container=useRef(null),maskVideo=useRef(null);
 const assets=[...new Map([...boundAssets,...libraryAssets].map(asset=>[asset.id,asset])).values()];
 const video=assets.find(asset=>asset.id===form.video_id),images=assets.filter(asset=>asset.kind==='image'),character=images.find(asset=>asset.id===form.reference_id),environment=images.find(asset=>asset.id===form.environment_reference_id);
 const intervalValid=layersIntervalValid(video,form.frames,form.start_seconds),startFrame=layersSourceFrame(form.start_seconds);
 const frameUrl=intervalValid?'/api/direct/target-frame?'+new URLSearchParams({video_id:form.video_id,frame:String(startFrame)}):'';
 const ready=Boolean(frameUrl&&loaded===frameUrl),pointValid=layersPointValid(form),sceneValid=Boolean(form.scene_description.trim()||environment),locked=Boolean(sending||pending||busy||uploading),baseLocked=locked||Boolean(reuse);
 const dims=layersDimensions(video,form.quality),active=isActiveJob(own);
 useEffect(()=>{onPending?.(Boolean(sending||pending));return()=>onPending?.(false);},[sending,pending,onPending]);
 useEffect(()=>{
  const current=own?.id||null;if(lastJob.current===current)return;lastJob.current=current;setReuse(null);setIntermediate(false);setMaskOpen(false);setMaskError(false);
  if(pending)return;
  const saved=read(draftKey),next=saved?.form||layersFormForJob(own)||{...LAYERS_DEFAULTS,video_id:selection?.video||''};setForm(next);setBoundAssets(saved?.assets||[]);setLoaded('');setFrameError('');
 },[own?.id]);
 useEffect(()=>{if(!enabled)return;const abort=new AbortController();setCheck(null);fetch('/api/workflows/motion-layers/preflight',{signal:abort.signal}).then(data).then(value=>{if(!abort.signal.aborted)setCheck(value);}).catch(error=>{if(!abort.signal.aborted)setCheck({ready:false,errors:[error.message]});});return()=>abort.abort();},[enabled,retry]);
 useEffect(()=>{
  if(!own)return;const abort=new AbortController();fetch('/api/jobs/'+own.id+'/layers-inputs',{signal:abort.signal}).then(data).then(value=>{if(!abort.signal.aborted)setBoundAssets(previous=>[...new Map([...previous,...(value.assets||[]),value.video,value.reference,value.environment].filter(Boolean).map(asset=>[asset.id,asset])).values()]);}).catch(error=>{if(!abort.signal.aborted)onError(error.message);});return()=>abort.abort();
 },[own?.id]);
 useEffect(()=>{if(pending)return;try{storage.setItem(draftKey,JSON.stringify({form,assets:draftAssets(form,assets)}));setDraftError('');}catch{setDraftError('No se pudo guardar el borrador en este navegador.');}},[draftKey,form,libraryAssets,boundAssets,pending]);
 useEffect(()=>{if(form.video_id||pending||!enabled)return;const selected=assets.find(asset=>asset.id===selection?.video);if(selected)setForm(current=>({...current,video_id:selected.id,duration_mode:'interval',frames:Math.max(5,Math.min(120,Math.round(selected.duration*24)))}));},[form.video_id,selection?.video,libraryAssets,pending,enabled]);
 useEffect(()=>{setFrameError('');},[frameUrl]);
 useEffect(()=>{if(!enabled)container.current?.querySelectorAll('video').forEach(media=>media.pause());},[enabled]);
 function update(key,value){setForm(current=>({...current,[key]:value,...(['video_id','start_seconds'].includes(key)?{x:null,y:null,duration_mode:'interval'}:{})}));}
 function choose(point){if(baseLocked||!ready||!point)return;setForm(current=>({...current,...point}));}
 function keyboard(event){if(baseLocked||!ready)return;const p=pointValid?{x:form.x,y:form.y}:{x:.5,y:.5},step=event.shiftKey ? .05 : .01;let next;
  if(event.key==='ArrowLeft')next={...p,x:Math.max(0,p.x-step)};if(event.key==='ArrowRight')next={...p,x:Math.min(1,p.x+step)};if(event.key==='ArrowUp')next={...p,y:Math.max(0,p.y-step)};if(event.key==='ArrowDown')next={...p,y:Math.min(1,p.y+step)};if(event.key==='Enter'||event.key===' ')next=p;if(next){event.preventDefault();choose(next);}}
 async function upload(event,role){const input=event.currentTarget,file=input.files[0];if(!file)return;try{const asset=await onUpload(file,role==='video_id'?'video':'image');setBoundAssets(current=>[...current.filter(item=>item.id!==asset.id),asset]);setForm(current=>({...current,[role]:asset.id,...(role==='video_id'?{start_seconds:'0',duration_mode:'interval',frames:Math.max(5,Math.min(120,Math.round(asset.duration*24))),x:null,y:null}:{})}));}catch(error){onError(error.message);}finally{input.value='';}}
 function prepareReuse(){if(locked||!own?.layers_character_ready)return;const base=layersFormForJob(own);setForm(current=>({...base,environment_reference_id:current.environment_reference_id,scene_description:current.scene_description,interaction_description:current.interaction_description}));setReuse(own);onViewChange?.('edit');}
 async function send(){
  if(lock.current)return;
  if(!pending&&(!intervalValid||!character||!sceneValid||!pointValid||!check?.ready||busy||uploading||(!reuse&&!ready)))return;
  lock.current=true;setSending(true);onError('');const abort=new AbortController(),timeout=setTimeout(()=>abort.abort(),30000);
  try{
   const request=pending||{form,assets:draftAssets(form,assets),body:layersRequest(form,crypto.randomUUID(),reuse)};
   storage.setItem(PENDING,JSON.stringify(request));setPending(request);
   const response=await fetch('/api/motion-layers/render',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request.body),signal:abort.signal});const result=await data(response);
   if(!layersAcknowledges(result,request.body))throw Error('El servidor no confirmó esta solicitud. Vuelve a consultarla con la misma clave.');
   storage.removeItem(PENDING);setPending(null);setReuse(null);onCreated(result);onViewChange?.('result');
  }catch(error){
   if(error.status>=400&&error.status<500){storage.removeItem(PENDING);setPending(null);}
   onError(error.name==='AbortError'?'Aún no se confirmó el envío. Pulsa Confirmar solicitud para consultar el mismo trabajo sin duplicarlo.':error.message);
  }finally{clearTimeout(timeout);lock.current=false;setSending(false);}
 }
 const reason=sending?'Enviando…':pending?'Envío pendiente. Confirma la misma solicitud sin duplicar el trabajo.':uploading?'Espera a que termine la carga.':busy?'Hay un trabajo en proceso. Podrás generar cuando termine.':!video?'Añade un vídeo de movimiento.':!character?'Elige una imagen del personaje.':!intervalValid?'Selecciona un intervalo de hasta 60 segundos en tramos de 5 s dentro del vídeo.':!pointValid?'Marca a la persona en el primer fotograma del intervalo.':!ready&&!reuse?'Espera a que cargue el primer fotograma.':!sceneValid?'Describe el nuevo escenario o añade una imagen.':!check?'Comprobando el método…':!check.ready?'El método aún no está disponible.':'Todo listo para la prueba.';
 return <div className="motion-studio bunny-layout motion-layers" ref={container} data-studio-view={view}>
  <section className="panel bunny-form" data-studio-pane="edit">
   <div className="bunny-intro"><span className="layers-intro-badge">EXPERIMENTAL · TRAMOS DE 5 S</span><h2>Personaje + Escenario</h2><p>La primera pasada cambia el personaje y conserva el escenario original. La segunda cambia la escena y protege al personaje. Los contactos y efectos del entorno siguen en prueba.</p></div>
   {draftError&&<p className="layers-draft-status" role="status">{draftError}</p>}
   {reuse&&<div className="layers-reuse-notice" role="status"><p>Se reutilizará el personaje de esta toma. Sólo se generará de nuevo el escenario.</p><button className="secondary" disabled={locked} onClick={()=>setReuse(null)}>Volver a generar también el personaje</button></div>}
   <div className="bunny-steps">
    <StudioStep number={1} title="Vídeo y persona" summary={video?video.name:'Añade una toma continua'} defaultOpen>
     <fieldset disabled={baseLocked}>
      <label className="upload-tile bunny-upload-tile"><strong>{video?'Cambiar vídeo':'Añadir vídeo de movimiento'}</strong><small>Una toma continua, sin cortes</small><input type="file" accept="video/*" aria-label="Subir vídeo para Personaje + Escenario" onChange={event=>upload(event,'video_id')}/></label>
      {video&&<p className="bunny-media-caption">{video.name} · {Number(video.duration).toFixed(2)} s</p>}
      <div className="bunny-interval-fields"><label>Inicio (segundos)<input type="number" min="0" step="any" value={form.start_seconds} onChange={event=>update('start_seconds',event.target.value)}/></label><label>Duración<select value={form.duration_mode==='full'?'full':form.frames} onChange={event=>setForm(current=>layersSelectDuration(current,video,event.target.value))}>{layersDurations(video,form.frames).map(frames=><option key={frames} value={frames}>{Number((frames/24).toFixed(2))} s</option>)}{layersFullFrames(video)!==null&&<option value="full">Vídeo completo · {Number((layersFullFrames(video)/24).toFixed(2))} s</option>}</select></label></div>
      <p className="bunny-help">Marca el centro de la persona en el primer fotograma. Con teclado, usa las flechas y Enter; Mayús mueve la marca más rápido.</p>
      {frameUrl&&<div className="layers-target" role="button" tabIndex={baseLocked?-1:0} aria-disabled={baseLocked||!ready} aria-label="Marcar persona en el primer fotograma" onKeyDown={keyboard} onClick={event=>choose(pointInImage(event.clientX,event.clientY,image.current?.getBoundingClientRect()))}><img key={frameUrl} ref={image} src={frameUrl} draggable="false" alt={'Primer fotograma del intervalo, '+(startFrame/24).toFixed(2)+' segundos'} onLoad={()=>setLoaded(frameUrl)} onError={()=>{setLoaded('');setFrameError('No se pudo cargar el primer fotograma. Revisa el inicio o vuelve a cargar el vídeo.');}}/>{pointValid&&<span className="layers-marker" aria-hidden="true" style={{left:form.x*100+'%',top:form.y*100+'%'}}/>}</div>}
      {frameError&&<p className="layers-error" role="alert">{frameError}</p>}
      <p className="layers-selection-status" role="status">{pointValid?'Persona marcada.':intervalValid?'Selecciona a la persona que realizará el movimiento.':'Elige un intervalo válido para marcar a la persona.'}</p>
     </fieldset>
    </StudioStep>
    <StudioStep number={2} title="Personaje" summary={character?.name||'Referencia de cuerpo completo'}>
     <fieldset disabled={baseLocked}>
      {character&&<img className="bunny-reference" src={'/api/assets/'+character.id} alt="Personaje de referencia"/>}
      <label className="upload-tile bunny-upload-tile"><strong>{character?'Cambiar imagen del personaje':'Añadir imagen del personaje'}</strong><small>Incluye el cuerpo y sus accesorios</small><input type="file" accept="image/*" aria-label="Subir personaje para Personaje + Escenario" onChange={event=>upload(event,'reference_id')}/></label>
      <label>Imagen guardada<select value={form.reference_id} onChange={event=>update('reference_id',event.target.value)}><option value="">Selecciona una imagen</option>{images.map(asset=><option key={asset.id} value={asset.id}>{asset.name}</option>)}</select></label>
      <label>Personaje y accesorios <span className="bunny-optional">Opcional</span><textarea maxLength={1000} value={form.character_direction} onChange={event=>update('character_direction',event.target.value)} placeholder="Por ejemplo: conservar a Goku y su nube Nimbus con el aspecto de la referencia."/></label>
     </fieldset>
    </StudioStep>
    <StudioStep number={3} title="Nuevo escenario" summary={environment?.name||form.scene_description.trim()||'Describe la escena o añade una imagen'} defaultOpen>
     <fieldset disabled={locked}>
      {environment&&<img className="bunny-reference bunny-scene-reference" src={'/api/assets/'+environment.id} alt="Escenario de referencia"/>}
      <label className="upload-tile bunny-upload-tile"><strong>{environment?'Cambiar imagen del escenario':'Añadir imagen del escenario'}</strong><small>Opcional si describes el escenario</small><input type="file" accept="image/*" aria-label="Subir escenario para Personaje + Escenario" onChange={event=>upload(event,'environment_reference_id')}/></label>
      <label>Imagen del escenario<select value={form.environment_reference_id} onChange={event=>update('environment_reference_id',event.target.value)}><option value="">Sin imagen · Usar descripción</option>{images.map(asset=><option key={asset.id} value={asset.id}>{asset.name}</option>)}</select></label>
      <label>Descripción del escenario<textarea maxLength={1200} value={form.scene_description} onChange={event=>update('scene_description',event.target.value)} placeholder="Describe el lugar, la luz y la atmósfera. Puedes escribir en español."/></label>
      <label>Interacciones con el entorno <span className="bunny-optional">Opcional · Experimental</span><textarea maxLength={1000} value={form.interaction_description} onChange={event=>update('interaction_description',event.target.value)} placeholder="Por ejemplo: salpicaduras delgadas alrededor de la nube, sin tapar al personaje."/></label>
     </fieldset>
    </StudioStep>
    <StudioStep number={4} title="Calidad" summary={(dims?dims.join(' × ')+' px · ':'')+(form.quality==='high'?'Alta':'Equilibrada')}>
     <fieldset disabled={baseLocked}><label>Calidad del vídeo<select value={form.quality} onChange={event=>update('quality',event.target.value)}><option value="balanced">Equilibrada</option><option value="high">Alta · Más tiempo y memoria</option></select></label><p className="bunny-output-summary">{dims?dims.join(' × ')+' píxeles · ':''}24 fps · Hasta 60 segundos, en tramos de 5 s. El último fotograma enlaza el mismo plano; un corte reinicia la continuidad. Se conserva el audio del intervalo.</p></fieldset>
    </StudioStep>
   </div>
   {check&&!check.ready&&<div className="layers-retry"><p role="status">{(check.errors||[]).map(error=>typeof error==='string'?error:JSON.stringify(error)).join(' · ')||'El método no está disponible.'}</p><button type="button" className="secondary" disabled={locked} onClick={()=>setRetry(value=>value+1)}>Volver a comprobar</button></div>}
   <StudioSlot name="actions"><div className="bunny-submit-footer"><div><strong>{Number(form.frames)/24} s · {reuse?'Nuevo escenario':'Personaje + Escenario'}</strong><p id="layers-submit-reason">{reason}</p></div><button className="primary" aria-describedby="layers-submit-reason" disabled={sending||Boolean(uploading)||(!pending&&(!intervalValid||!character||!pointValid||!sceneValid||(!reuse&&!ready)||busy||!check?.ready))} onClick={send}>{sending?'Enviando…':pending?'Confirmar solicitud':reuse?'Generar nuevo escenario':'Generar prueba'}</button></div></StudioSlot>
  </section>
  <StudioSlot name="result"><section className={'panel bunny-result'+(own?.result_available?' has-video-result':'')} data-studio-pane="result">
   {own?<><h2>{own.result_available?'Resultado · Personaje + Escenario':own.status==='failed'?'Prueba con error':own.status==='cancelled'?'Prueba cancelada':own.status==='submission_unknown'?'Envío sin confirmar':active?'Personaje + Escenario en proceso':'Prueba terminada'}</h2>{!active&&own.status!=='completed'&&<p className="layers-result-stage" role="status">{own.status==='failed'?'Estado: error. La prueba no se completó.':own.status==='cancelled'?'Estado: cancelado. La generación se detuvo.':'No se pudo confirmar el envío de esta prueba.'}</p>}{own.layers_stage&&<p className="layers-result-stage" role={active?'status':undefined}>{!active&&own.status!=='completed'?'Última etapa registrada: ':''}{own.layers_stage}</p>}<JobElapsed job={own}/><JobCancelButton job={own} onCancel={onCancel}/>{own.error&&<p className="layers-error" role="alert">{own.error}</p>}
    {own.result_available&&own.status==='completed'&&<ResultViewer key={own.id} job={own} assets={assets} method="Personaje + Escenario" onReviewSaved={onReviewSaved}/>}
    {own.layers_character_ready&&<><details key={own.id+'-intermediate'} className="layers-intermediate" onToggle={event=>setIntermediate(event.currentTarget.open)}><summary>{own.result_available?'Comparar la primera pasada con el resultado':'Ver primera pasada · Personaje'}</summary><p>El personaje ya está preparado. Puedes conservar esta pasada y probar otro escenario.</p>{intermediate&&(layersIntermediateComparison(own)?<ResultComparison job={own} specOverride={layersIntermediateComparison(own)} active={enabled&&intermediate} onReviewSaved={onReviewSaved}/>:<video controls playsInline preload="metadata" src={'/api/jobs/'+own.id+'/layers-intermediate'} aria-label="Primera pasada del personaje"/>)}</details><button type="button" className="secondary" disabled={locked||active} onClick={prepareReuse}>Cambiar sólo el escenario</button></>}
    {own.layers_mask_ready&&<details key={own.id+'-mask'} className="layers-intermediate layers-mask-preview" onToggle={event=>{const open=event.currentTarget.open;if(!open)maskVideo.current?.pause();setMaskOpen(open);}}><summary>Ver protección del personaje</summary><p>La parte marcada en verde es la que se conserva al cambiar el escenario. Puedes revisar esta protección aunque el resultado final no se haya completado.</p>{maskOpen&&<><video ref={maskVideo} controls playsInline preload="metadata" src={'/api/jobs/'+own.id+'/layers-mask-preview'} aria-label="Protección del personaje en verde" onLoadedData={()=>setMaskError(false)} onError={()=>setMaskError(true)}/>{maskError&&<div role="alert"><p className="layers-error">No se pudo cargar la vista de protección.</p><button type="button" className="secondary" onClick={()=>{setMaskError(false);maskVideo.current?.load();}}>Volver a cargar la protección</button></div>}</>}</details>}
   </>:<div className="bunny-empty-result"><span className="bunny-empty-mark" aria-hidden="true">▷</span><h2>Dos pasos, una escena nueva</h2><p>Aquí podrás revisar el personaje de la primera pasada y compararlo con el escenario final.</p><button className="secondary bunny-return-edit" onClick={()=>onViewChange?.('edit')}>Volver a editar</button></div>}
  </section></StudioSlot>
 </div>;
}
