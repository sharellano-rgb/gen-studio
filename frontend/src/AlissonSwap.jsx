import {StudioSlot} from './StudioWorkspace.jsx';
import StudioStep from './StudioStep.jsx';
import ObjectSwapGuide,{SwapStage} from './ObjectSwapGuide.jsx';
import {sourceStep,referenceStep,intervalSummary} from './objectSwapGuide.js';
import './motionStudio.css';
import './unifiedMethods.css';
import React,{useEffect,useRef,useState} from 'react';
import {workspaceSessionStorage as sessionStorage} from './workspaceStorage.js';
import {isAlisson,alissonChangeVideo,alissonChangeReference,alissonForm,alissonBody,alissonSourceFrames,validAlissonForm,readPendingAlisson,savePendingAlisson,clearPendingAlisson,canSendAlisson,definitiveAlissonRejection,acknowledgedAlisson,alissonUsesCharacterSheet,alissonCharacterSheetReady} from './alissonState.js';
import ResultViewer from './ResultViewer.jsx';
import JobElapsed from './JobElapsed.jsx';
import JobCancelButton from './JobCancelButton.jsx';
import {cancellationPending} from './jobCancellation.js';
import {ALISSON_QUALITIES,alissonGeometry,alissonQualityLabel} from './alissonQuality.js';
import {alissonDurationOptions,alissonDurationValue,chooseAlissonDuration} from './alissonDuration.js';
import {formatAlissonStart,alissonStartCandidate} from './alissonStartInput.js';
import './alissonSwap.css';
async function api(path,body){
  const response=await fetch('/api'+path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:undefined);
  let data;try{data=await response.json();}catch{throw Error('No se pudo confirmar la respuesta del servidor.');}
  if(!response.ok){const error=Error(typeof data.detail==='string'?data.detail:data.errors?.join(' · ')||'No se pudo completar la petición.');error.status=response.status;throw error;}
  return data;
}
export default function AlissonSwap({assets,selection={},job,draftParams,busy,available,uploading,onUpload,onDraftChanged,onCreated,onError,onReuse,onCancel,onReviewSaved}){
  const context=isAlisson(job)?job:null;
  const [pending,setPending]=useState(()=>readPendingAlisson(sessionStorage));
  const initial=pending||context?.parameters||draftParams||{video_id:selection.video||'',reference_id:selection.image||selection.face||''};
  const [form,setForm]=useState(()=>alissonForm(initial,assets.find(a=>a.id===initial.video_id)));
  const [startText,setStartText]=useState(()=>formatAlissonStart(form.start_seconds));
  useEffect(()=>{setStartText(formatAlissonStart(form.start_seconds));},[form.video_id,form.start_seconds]);
  const [customDuration,setCustomDuration]=useState(false);
  const [options,setOptions]=useState(null),[optionsError,setOptionsError]=useState(''),[sending,setSending]=useState(false),[uploadTarget,setUploadTarget]=useState('');
  const live=useRef(true),dirty=useRef(false),player=useRef(null),initialized=useRef(Boolean(pending||context||draftParams));
  useEffect(()=>{live.current=true;return()=>{live.current=false;};},[]);
  async function checkOptions(){setOptionsError('');try{const result=await api('/alisson/options');if(live.current)setOptions(result);}catch(error){if(live.current)setOptionsError(error.message);}}
  useEffect(()=>{checkOptions();},[]);
  useEffect(()=>{
    if(initialized.current||!assets.length)return;
    const selected=assets.find(a=>a.id===(form.video_id||selection.video)&&a.kind==='video');
    if(selected){initialized.current=true;setForm(current=>({...alissonChangeVideo(current,selected),reference_id:current.reference_id||selection.image||selection.face||''}));}
  },[assets,selection.video,selection.image,selection.face]);
  useEffect(()=>{if(!context||dirty.current)onDraftChanged?.({...form,engine:'h3_alisson'});},[form,onDraftChanged,context?.id]);
  const video=assets.find(a=>a.id===form.video_id&&a.kind==='video'),reference=assets.find(a=>a.id===form.reference_id&&a.kind==='image');
  const faceReference=assets.find(a=>a.id===form.face_reference_id&&a.kind==='image');
  const automaticSheet=alissonUsesCharacterSheet(form),sheetReady=alissonCharacterSheetReady(form,options);
  const sheetError=automaticSheet&&!sheetReady?(options?options.character_sheet?.errors?.join(' · ')||'El preset Qwen Image 2.1 · 4 MP todavía no está disponible. Puedes desactivar la hoja automática para generar con tus referencias.':'Comprobando el preset para crear la hoja de personaje…'):'';
  const resultSheet=context?.alisson_character_sheet;
  const resultSheetAsset=resultSheet&&assets.find(a=>a.id===resultSheet.asset_id&&a.kind==='image');
  const faceError=form.face_reference_id&&!faceReference?'Selecciona una referencia facial disponible o elige «Sin referencia facial».':form.face_reference_id&&form.face_reference_id===form.reference_id?'Elige una imagen facial distinta de la foto de cuerpo.':form.face_reference_id&&options&&!options.supports_face_reference?'Actualiza Gen Studio para usar la segunda referencia facial.':'';
  const maxFrames=Math.min(1440,options?.max_frames||1440),locked=sending||Boolean(uploading)||Boolean(uploadTarget)||Boolean(pending);
  const candidate=alissonStartCandidate(form,startText);
  const durationValue=customDuration?'custom':alissonDurationValue(candidate||form,video,maxFrames);
  const ready=Boolean(available&&options?.enabled&&options?.available&&sheetReady),valid=Boolean(candidate&&validAlissonForm(candidate,assets,maxFrames)&&!faceError);
  const uncertain=context?.status==='submission_unknown';
  const canSend=canSendAlisson({pending,sending,uploading:uploading||uploadTarget,busy,available:ready&&!uncertain,valid});
  function seek(){const v=player.current;if(v?.readyState>=1){v.pause();v.currentTime=Math.min(form.start_seconds,Math.max(0,v.duration-1/24));}}
  useEffect(()=>{seek();},[form.video_id,form.start_seconds]);
  function change(field,value){if(locked)return;dirty.current=true;setForm(current=>field==='reference_id'?{...alissonChangeReference(current,value),...(assets.find(a=>a.id===value)?.character_sheet?{auto_character_sheet:false}:{})}:({...current,[field]:value,...(field==='face_reference_id'&&value!==current.face_reference_id?{character_details:''}:{})}));}
  function useSheet(){if(locked||busy||!resultSheetAsset)return;dirty.current=true;setForm(current=>({...current,reference_id:resultSheetAsset.id,face_reference_id:'',character_details:'',auto_character_sheet:false}));}
  function confirmStart(){if(locked||!candidate)return;change('start_seconds',candidate.start_seconds);setStartText(formatAlissonStart(candidate.start_seconds));}
  function startFromBeginning(){if(locked)return;change('start_seconds',0);setStartText('0');}
  function selectVideo(asset){if(locked)return;dirty.current=true;setCustomDuration(false);setStartText('0');setForm(current=>alissonChangeVideo(current,asset));}
  function selectDuration(value){
    if(locked)return;dirty.current=true;setCustomDuration(value==='custom');
    const next=chooseAlissonDuration(candidate||form,video,value,maxFrames);
    if(value==='full'||candidate){setForm(next);setStartText(formatAlissonStart(next.start_seconds));}
  }
  async function upload(event,kind){
    const input=event.currentTarget,file=input.files?.[0];if(!file)return;setUploadTarget(kind);
    try{
      const asset=await onUpload(file,kind==='face'?'image':kind);if(!live.current)return;dirty.current=true;
      if(kind==='video'){setCustomDuration(false);setStartText('0');setForm(current=>alissonChangeVideo(current,asset));}
      else if(kind==='face')setForm(current=>({...current,face_reference_id:asset.id,character_details:''}));
      else setForm(current=>alissonChangeReference(current,asset.id));
    }catch(error){if(live.current)onError(error.message);}finally{input.value='';if(live.current)setUploadTarget('');}
  }
  async function generate(){
    if(!canSend)return;
    const saved=pending||readPendingAlisson(sessionStorage);
    const next=alissonStartCandidate(form,startText);
    if(!saved&&(!next||!validAlissonForm(next,assets,maxFrames)||faceError||sheetError)){onError(sheetError||faceError||'Revisa el inicio y la duración antes de generar.');return;}
    setSending(true);
    try{
      const body=saved||alissonBody(next,crypto.randomUUID());
      savePendingAlisson(sessionStorage,body);setPending(body);
      const result=await api('/alisson/generate',body);
      if(!acknowledgedAlisson(result,body))throw Error('El servidor no confirmó un trabajo correspondiente a esta petición.');
      clearPendingAlisson(sessionStorage);
      if(live.current){setPending(null);onCreated(result);}
    }catch(error){
      if(definitiveAlissonRejection(error.status)){clearPendingAlisson(sessionStorage);if(live.current)setPending(null);}
      if(live.current)onError(error.message+(definitiveAlissonRejection(error.status)?'':' Se conserva el envío para recuperarlo con la misma petición.'));
    }finally{if(live.current)setSending(false);}
  }
  const reason=pending?'Hay un envío pendiente. Recupéralo con la misma petición antes de modificar los archivos.':!video?'Selecciona el vídeo fuente.':!reference?'Selecciona la foto del personaje.':faceError?faceError:sheetError?sheetError:!candidate?'Escribe un inicio válido en segundos, por ejemplo 0, o pulsa «Desde el principio».':!valid?'Revisa el intervalo: entre 5 fotogramas y 15 segundos, dentro del vídeo.':busy?'Espera a que termine el trabajo actual.':!ready?'El método todavía no está disponible para generar.':automaticSheet?'Primero se creará la hoja de personaje con Qwen; después Alisson la usará para generar el vídeo.':'Se reemplazará un personaje conservando el movimiento y el escenario del vídeo.';
  const guideSteps=[
    sourceStep(video),
    {id:'cast',title:'Persona que vas a reemplazar',help:'Si aparecen varias personas, describe a cuál quieres cambiar. Puedes dejarlo vacío para usar la persona principal.',summary:form.target||'Persona principal'},
    referenceStep(reference,{help:'Añade una foto de cuerpo y vestuario. Puedes sumar una referencia facial del mismo personaje.',
      summary:[reference?.name,faceReference?.name].filter(Boolean),issue:faceError||sheetError}),
    {id:'quality',summary:intervalSummary(candidate?.start_seconds??form.start_seconds,form.frames)+' · '+alissonQualityLabel(ALISSON_QUALITIES.find(q=>q.id===form.quality)||ALISSON_QUALITIES[0],video),
      issue:!valid&&'Revisa el inicio, la duración y los ajustes del intervalo.'},
    {id:'review'}
  ];
  return <section className="alisson-swap bunny-layout unified-method-layout" aria-label="Alisson Object Swap">
    <ObjectSwapGuide enabled={!context} recovery={Boolean(pending)} scope="h3_alisson" sourceId={form.video_id} steps={guideSteps} busy={locked}>
    <section className="panel alisson-form bunny-form" data-studio-pane="edit"><div className="panel-title"><h2>Alisson v1.1 · Object Swap</h2><span className="tiny">EXPERIMENTAL</span></div>
      <SwapStage step="references"><p>Un personaje, una o dos referencias. Procesa hasta 60 segundos en tramos de hasta 5 s, con continuidad dentro de cada plano.</p>
      <p className="muted">La foto de cuerpo define la ropa y las proporciones. Puedes añadir una referencia facial del mismo personaje. Las imágenes se analizan automáticamente.</p></SwapStage>
      {optionsError&&<p className="notice error" role="alert">{optionsError}</p>}
      {options?.errors?.length>0&&<p className="notice error" role="status">{options.errors.join(' · ')}</p>}
      <fieldset disabled={locked} className="alisson-fields">
        <StudioStep guideStep="source" number={1} title="Vídeo" summary={video?.name||'Añade el vídeo fuente'} defaultOpen><label>Vídeo fuente<select aria-label="Vídeo Alisson" value={form.video_id} onChange={e=>selectVideo(assets.find(a=>a.id===e.target.value))}><option value="">Selecciona un vídeo</option>{assets.filter(a=>a.kind==='video').map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
          <label className="alisson-upload">Subir vídeo<input type="file" accept=".mp4,.mov,.webm" onChange={e=>upload(e,'video')}/></label>
          {video&&<video key={video.id} ref={player} src={'/api/assets/'+video.id} controls playsInline preload="metadata" onLoadedMetadata={seek} aria-label="Vídeo fuente Alisson"/>}
        </StudioStep><StudioStep guideStep={['cast','references']} number={2} title="Personaje" summary={reference?.name||'Elige sus referencias'}><SwapStage step="references"><label>Cuerpo y vestuario<select aria-label="Referencia Alisson" value={form.reference_id} onChange={e=>change('reference_id',e.target.value)}><option value="">Selecciona una imagen</option>{assets.filter(a=>a.kind==='image').map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
          <label className="alisson-upload">Subir foto de cuerpo<input type="file" accept=".png,.jpg,.jpeg,.webp" onChange={e=>upload(e,'image')}/></label>
          {reference&&<img src={'/api/assets/'+reference.id} alt="Personaje y vestuario de referencia"/>}<p className="muted">Una foto clara del personaje y de la ropa que quieres conservar. Al cambiarla, vuelve a elegir su referencia facial.</p>
          <label>Referencia facial <span className="muted">Opcional · Mismo personaje</span><select aria-label="Referencia facial Alisson" value={form.face_reference_id} onChange={e=>change('face_reference_id',e.target.value)}><option value="">Sin referencia facial</option>{assets.filter(a=>a.kind==='image').map(a=><option key={a.id} value={a.id} disabled={a.id===form.reference_id}>{a.name}</option>)}</select></label>
          <label className="alisson-upload">Subir referencia facial<input type="file" accept=".png,.jpg,.jpeg,.webp" onChange={e=>upload(e,'face')}/></label>
          {faceReference&&<><img src={'/api/assets/'+faceReference.id} alt="Referencia facial completa del mismo personaje"/><StudioSlot name="actions"><div className="alisson-actions"><button className="secondary" type="button" onClick={()=>change('face_reference_id','')}>Quitar referencia facial</button></div></StudioSlot></>}
          <p className="muted">Puedes usar un primer plano o una sola imagen con tres vistas: frente, perfil izquierdo y perfil derecho. Se conserva completa; todas las vistas deben ser del mismo personaje.</p>
        <div className="alisson-sheet-controls">
          <label className="alisson-sheet-option"><input type="checkbox" checked={automaticSheet} onChange={e=>change('auto_character_sheet',e.target.checked)}/><span>Crear character sheet automáticamente <small>Qwen Image 2.1 · 4 MP</small></span></label>
          <p className="muted">Crea una hoja con varias vistas del mismo personaje a partir de tu foto de cuerpo y, si la añades, su referencia facial. Esa hoja se usará como referencia para el vídeo. Añade tiempo de generación.</p>
          {reference?.character_sheet&&<p className="notice">La imagen seleccionada ya es una hoja de personaje. Puedes usarla directamente con esta opción desactivada.</p>}
          {sheetError&&<p className="notice" role="status">{sheetError}</p>}
        </div>
        </SwapStage><SwapStage step="cast"><label>Persona que quieres reemplazar <span className="muted">Opcional</span><input value={form.target} maxLength={300} placeholder="La persona principal; o describe cuál si aparecen varias" onChange={e=>change('target',e.target.value)}/></label></SwapStage>
        </StudioStep><StudioStep guideStep="quality" number={3} title="Duración y calidad" summary={`${(form.frames/24).toFixed(2)} s · ${form.quality}`}>
        <p className="method-info">El último fotograma enlaza los tramos del mismo plano. Un corte reinicia la continuidad.</p><div className="alisson-timing"><label>Duración<select aria-label="Duración Alisson" value={durationValue} onChange={e=>selectDuration(e.target.value)}>
          {alissonDurationOptions(candidate||form,video,maxFrames).map(option=><option key={option.value} value={option.value} disabled={option.disabled}>{option.label}</option>)}
        </select></label>
          <div><label>Inicio (segundos)<input type="text" inputMode="decimal" value={startText} aria-invalid={!candidate} onChange={e=>{if(!locked)setStartText(e.target.value);}} onBlur={confirmStart} onKeyDown={e=>{if(e.key==='Enter'){e.preventDefault();confirmStart();}}}/></label><StudioSlot name="actions"><div className="alisson-actions"><button type="button" className="secondary" onClick={startFromBeginning}>Desde el principio</button></div></StudioSlot></div></div>
        {durationValue==='custom'&&<label>Duración personalizada (segundos)<input type="number" min={5/24} max={maxFrames/24} step={1/24} value={Number((form.frames/24).toFixed(3))} onChange={e=>change('frames',Math.round(Number(e.target.value)*24))}/></label>}
        {durationValue==='full'&&<p className="muted">Se procesará todo el vídeo desde el inicio hasta el final.</p>}
        {alissonSourceFrames(video)>maxFrames&&<p className="notice">Este vídeo dura {(alissonSourceFrames(video)/24).toFixed(2)} s. Alisson admite hasta {maxFrames/24} s por generación; selecciona un intervalo para este archivo.</p>}
        {video&&<p className="muted">{form.frames} fotogramas a 24 fps · {candidate?`${candidate.start_seconds.toFixed(2)}–${(candidate.start_seconds+form.frames/24).toFixed(2)} s`:'Completa el inicio'} · Vídeo: {(alissonSourceFrames(video)/24).toFixed(2)} s.</p>}
        <label>Calidad del vídeo<select aria-label="Calidad del vídeo Alisson" value={form.quality} onChange={e=>change('quality',e.target.value)}>
          {ALISSON_QUALITIES.map(preset=><option key={preset.id} value={preset.id}>{alissonQualityLabel(preset,video)}</option>)}
        </select></label>
        <p className="muted" role="status">{alissonGeometry(video,form.quality)?.join(' × ')||'Resolución adaptada al vídeo'}{video?' píxeles':''} · 24 fps. Una resolución mayor requiere más tiempo y memoria. Se conservan las proporciones.</p>
        <details className="advanced-controls"><summary>Opciones avanzadas</summary>
          <label>Descripción del personaje<textarea value={form.character_details} maxLength={1000} placeholder="Automática si lo dejas vacío. Puedes describir al personaje en español." onChange={e=>change('character_details',e.target.value)}/></label>
          <label>Semilla<input type="number" min="0" max="9007199254740991" step="1" value={form.seed} onChange={e=>change('seed',Number(e.target.value))}/></label>
          <label className="ali-nsfw-toggle"><input type="checkbox" checked={Boolean(form.nsfw_h3_motion_booster)} onChange={e=>change('nsfw_h3_motion_booster',e.target.checked)}/><span>Adaptador H3 Motion Booster V2</span></label>
          <label className="ali-nsfw-toggle"><input type="checkbox" checked={Boolean(form.nsfw_penis_v2)} onChange={e=>change('nsfw_penis_v2',e.target.checked)}/><span>Adaptador Penis V2</span></label>
          <label className="ali-nsfw-toggle"><input type="checkbox" checked={Boolean(form.nsfw_hm_pussy)} onChange={e=>change('nsfw_hm_pussy',e.target.checked)}/><span>Adaptador HM Pussy V2</span></label>
          <label className="ali-nsfw-toggle"><input type="checkbox" checked={Boolean(form.nsfw_minimax_unlocked)} onChange={e=>change('nsfw_minimax_unlocked',e.target.checked)}/><span>Adaptador Minimax H3 Unlocked V2.1</span></label>
          <label>Intensidad NSFW<input type="number" min="0" max="2" step="0.05" value={form.nsfw_strength} onChange={e=>change('nsfw_strength',Number(e.target.value))}/></label>
          <p className="muted">Los adaptadores NSFW son opcionales y desactivados por defecto. Se registran archivo, hash e intensidad en el metadato de la toma.</p>
          <p className="muted">8 pasos. Revisa rostro, ropa y continuidad entre cortes.</p>
        </details>
        </StudioStep>
      </fieldset>
      <SwapStage step="review"><p role="status" className={valid?'muted':'notice'}>{reason}</p>
      {uncertain&&<p role="alert" className="notice">El envío anterior no está confirmado. Revisa su estado antes de crear otra toma.</p>}
      <StudioSlot name="actions"><div className="alisson-actions"><button className="primary" disabled={!canSend} onClick={generate}>{sending?'Enviando…':pending?'Recuperar envío pendiente':automaticSheet?'Crear hoja y generar vídeo':'Generar con Alisson'}</button><button className="secondary" disabled={sending} onClick={checkOptions}>Comprobar método</button></div></StudioSlot></SwapStage>
    </section></ObjectSwapGuide>
    {context&&<StudioSlot name="result"><section className="panel alisson-result bunny-result" data-studio-pane="result"><div className="panel-title"><h2>{context.status==='completed'?'Resultado Alisson':'Proceso Alisson'}</h2><span className="tiny">Alisson v1.1</span></div>{context.status==='completed'&&context.result_available&&<ResultViewer key={context.id} job={context} assets={assets} method="Alisson" onReviewSaved={onReviewSaved}/>}<div className="alisson-process-info"><p role="status">{cancellationPending(context)&&context.status!=='cancelling'?'Cancelando el trabajo y liberando sus recursos…':context.stage}</p><JobElapsed job={context}/>
      {alissonUsesCharacterSheet(context.parameters.alisson_request||context.parameters)&&<p className="muted">{resultSheet?'Hoja de personaje creada y lista para usar como referencia del vídeo.':'Este trabajo crea primero la hoja de personaje y después genera el vídeo.'}</p>}
      {resultSheet&&<section className="alisson-sheet-result" aria-label="Hoja de personaje generada">
        <h3>Character sheet · Referencia del vídeo</h3>
        <p className="muted">{resultSheet.width} × {resultSheet.height} píxeles · Qwen Image 2.1 · 4 MP</p>
        <a href={'/api/alisson/jobs/'+context.id+'/character-sheet'} target="_blank" rel="noopener noreferrer" aria-label="Abrir hoja de personaje completa"><img src={'/api/alisson/jobs/'+context.id+'/character-sheet'} alt="Hoja generada con varias vistas del mismo personaje" width={resultSheet.width} height={resultSheet.height}/></a>
        <div className="alisson-actions"><a className="download" href={'/api/alisson/jobs/'+context.id+'/character-sheet?download=true'}>Descargar character sheet ↓</a>{resultSheetAsset&&<button type="button" className="secondary" disabled={busy||locked} onClick={useSheet}>Usar esta hoja como referencia</button>}</div>
        <p className="muted">Puedes reutilizar esta imagen en otra toma sin volver a crear la hoja.</p>
      </section>}
      {context.parameters.resolution&&<p className="muted">Resolución de esta toma: {context.parameters.resolution.replace('x',' × ')} píxeles · 24 fps</p>}
      <JobCancelButton job={context} onCancel={onCancel}/>
      {context.progress?.max>0&&<p>{context.progress.value} / {context.progress.max}</p>}
      {context.error&&<p role="alert" className="notice error">{context.error}</p>}
      
      </div>
      {onReuse&&['completed','failed','cancelled'].includes(context.status)&&<button className="secondary" disabled={busy||sending||Boolean(pending)} onClick={onReuse}>Reutilizar esta toma</button>}
    </section></StudioSlot>}
    {!context&&<StudioSlot name="result"><section className="panel bunny-result" data-studio-pane="result"><div className="bunny-result-heading"><h2>Tu resultado</h2></div><div className="empty-preview"><strong>Todo empieza con una toma.</strong><p>Añade el vídeo y las referencias del personaje.</p></div></section></StudioSlot>}
  </section>;
}
