import {StudioSlot} from './StudioWorkspace.jsx';
import StudioStep from './StudioStep.jsx';
import ObjectSwapGuide,{SwapStage} from './ObjectSwapGuide.jsx';
import {sourceStep,referenceStep,intervalSummary} from './objectSwapGuide.js';
import './motionStudio.css';
import './unifiedMethods.css';
import React,{useEffect,useRef,useState} from 'react';
import {workspaceSessionStorage as storage} from './workspaceStorage.js';
import ResultViewer from './ResultViewer.jsx';
import JobElapsed from './JobElapsed.jsx';
import JobCancelButton from './JobCancelButton.jsx';
import './secoursesSwap.css';
import {visualMethod,visualEngineFor,secoursesLabel,secoursesStudioMode,secoursesStorageKeys,readSecoursesState,secoursesFormForJob,
  prepareSecoursesForm,secoursesMethodSubmission,visualEditorError} from './secoursesVisualMethods.js';

async function api(path,body){
  const response=await fetch('/api/secourses/'+path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});
  const data=await response.json();
  if(!response.ok){const error=Error(typeof data.detail==='string'?data.detail:'Revisa los datos del formulario.');error.status=response.status;throw error;}
  return data;
}
export default function SECoursesSwap({assets,selection={},job,visualMethod:chosenMethod,mode,busy,uploading,onUpload,onCreated,onCancel,onError,onSelectMethod,onPending}){
  const method=chosenMethod===undefined?visualMethod(job):chosenMethod;
  const creationMode=mode||secoursesStudioMode(job),keys=secoursesStorageKeys(method,creationMode),label=secoursesLabel({visual_method:method});
  const [saved]=useState(()=>readSecoursesState(storage,method,creationMode));
  const [pending,setPending]=useState(saved.pending),[pendingKey,setPendingKey]=useState(saved.pendingKey);
  const [form,setForm]=useState(()=>{const initial={video_id:selection.video||'',reference_id:selection.image||'',variant:'fast',background:'video',
    frames:120,start_seconds:0,megapixels:1,guide_megapixels:1,threshold:0.5,mask_grow:0,target:'person',prompt:'',speech:'',speech_language:'Spanish',
    auto_describe:true,lock_audio:true,seed:42,...(job?secoursesFormForJob(job):saved.draft||{})};
    return saved.pending?{...initial,...saved.pending}:prepareSecoursesForm(initial,{method,mode:creationMode});});
  const [options,setOptions]=useState(null),[sending,setSending]=useState(false),[loadingFile,setLoadingFile]=useState(false);
  const live=useRef(true);
  useEffect(()=>{live.current=true;api('options'+(method?'?visual_method='+encodeURIComponent(method):'')).then(x=>{if(live.current)setOptions(x);}).catch(e=>{if(live.current)onError(e.message);});return()=>{live.current=false;};},[method]);
  useEffect(()=>{if(!pending)storage.setItem(keys.draft,JSON.stringify(form));},[form,pending,keys.draft]);
  const locked=Boolean(pending||sending||uploading||loadingFile);
  useEffect(()=>{onPending?.(locked);return()=>onPending?.(false);},[locked,onPending]);
  const formError=visualEditorError(form,method),otherPending=!pending&&saved.otherPending;
  const video=assets.find(a=>a.id===form.video_id&&a.kind==='video'),reference=assets.find(a=>a.id===form.reference_id&&a.kind==='image');
  const valid=video&&reference&&form.frames>=5&&form.frames<=1440&&Number.isInteger(form.frames)&&form.start_seconds>=0&&
    Math.round(form.start_seconds*24)+form.frames<=Math.round(video.duration*24)+1&&form.target.trim()&&!formError&&!otherPending;
  const ready=options?.available&&options?.enabled;
  const terminal=!job||['completed','failed','cancelled'].includes(job.status);
  function change(key,value){if(!locked)setForm(f=>({...f,[key]:value}));}
  function selectVideo(id){const a=assets.find(x=>x.id===id);setForm(f=>({...f,video_id:id,start_seconds:0,frames:Math.max(5,Math.min(1440,Math.round((a?.duration||5)*24)))}));}
  async function upload(event,kind){const input=event.currentTarget,file=input.files?.[0];if(!file)return;setLoadingFile(true);
    try{const a=await onUpload(file,kind);if(live.current)setForm(f=>kind==='video'?{...f,video_id:a.id,start_seconds:0,frames:Math.max(5,Math.min(1440,Math.round(a.duration*24)))}:{...f,reference_id:a.id});}
    catch(e){onError(e.message);}finally{input.value='';if(live.current)setLoadingFile(false);}}
  async function generate(){if(sending||(!pending&&(!valid||!ready||busy)))return;setSending(true);
    try{const body=secoursesMethodSubmission(form,pending,()=>crypto.randomUUID(),{method,mode:creationMode});const key=pending?pendingKey:keys.pending;storage.setItem(key,JSON.stringify(body));setPendingKey(key);setPending(body);
      const result=await api('generate',body);
      if(!result.id||result.parameters?.engine!=='h3_secourses'||Object.entries(body).some(([k,v])=>k!=='request_key'&&result.parameters.secourses_request?.[k]!==v))throw Error('El servidor no confirmó esta solicitud.');
      storage.removeItem(key);if(live.current){setPending(null);onCreated(result);}}
    catch(e){if([400,401,403,404,409,410,422].includes(e.status)){storage.removeItem(pending?pendingKey:keys.pending);setPending(null);}onError(e.message);}
    finally{if(live.current)setSending(false);}}
  const guideSteps=[
    sourceStep(video),
    {id:'cast',title:'Persona que vas a reemplazar',help:'Describe a la persona del vídeo. Si hay varias, distingue la ropa o la posición de la que quieres cambiar.',summary:form.target,issue:!form.target.trim()&&'Indica a quién quieres reemplazar.'},
    referenceStep(reference),
    {id:'scene',summary:form.prompt|| (form.background==='video'?'Conservar el escenario original':'Escenario de la referencia'),issue:formError},
    {id:'quality',summary:intervalSummary(form.start_seconds,form.frames)+' · '+(form.variant==='fast'?'Rápida':'Normal')+' · '+form.megapixels+' MP',
      issue:!valid&&'Ajusta el intervalo al vídeo. El máximo es de 60 segundos.'},
    {id:'review'}
  ];
  return <div className="bunny-layout unified-method-layout secourses-layout">
    <ObjectSwapGuide enabled={creationMode==='character'&&!job} recovery={Boolean(pending||otherPending)} scope={visualEngineFor({engine:'h3_secourses',visual_method:method})} sourceId={form.video_id} storage={storage} steps={guideSteps} busy={locked}>
    <section className="panel bunny-form" data-studio-pane="edit"><div className="bunny-intro"><h2>{label}</h2><p>{method==='faithful'?'Guía reforzada con varios fotogramas del vídeo y análisis de Qwen para cada tramo.':method==='experimental'?'Qwen compara el encuadre completo y detalles del personaje a lo largo del vídeo.':'Reemplaza al personaje o transfiere su movimiento al escenario de la referencia.'}</p></div>
      {otherPending&&<div className="draft-pending-warning" role="status">Hay un envío pendiente de {otherPending.label}. Confírmalo antes de crear otra toma.{onSelectMethod&&<button className="secondary" onClick={()=>onSelectMethod(otherPending.body)}>Retomar envío pendiente</button>}</div>}
      {options&&!options.available&&<p role="alert">Faltan componentes: {options.missing.join(' · ')}</p>}
      <div className="bunny-steps">
        <StudioStep guideStep="source" number={1} title="Vídeo" summary={video?.name||'Añade el vídeo fuente'} defaultOpen><fieldset disabled={locked||!terminal}>
          <label className="upload-tile bunny-upload-tile"><strong>{video?'Cambiar vídeo':'Añadir vídeo'}</strong><input type="file" aria-label={'Subir vídeo fuente · '+label} accept="video/*" onChange={e=>upload(e,'video')}/></label>
          <label>Vídeo de tu biblioteca<select value={form.video_id} onChange={e=>selectVideo(e.target.value)}><option value="">Selecciona un vídeo</option>{assets.filter(a=>a.kind==='video').map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
          {video&&<><video className="bunny-source-preview" controls playsInline preload="metadata" src={'/api/assets/'+video.id}/><p className="method-info">{Number(video.duration).toFixed(2)} s · {video.name}</p></>}
        </fieldset></StudioStep>
        <StudioStep guideStep={['cast','references']} number={2} title="Personaje" summary={reference?.name||'Elige su imagen'}><fieldset disabled={locked||!terminal}>
          <SwapStage step="references">{reference&&<img className="bunny-reference" src={'/api/assets/'+reference.id} alt="Personaje de referencia"/>}
          <label className="upload-tile bunny-upload-tile"><strong>Añadir imagen del personaje</strong><input type="file" aria-label={'Subir personaje · '+label} accept="image/*" onChange={e=>upload(e,'image')}/></label>
          <label>Imagen del personaje<select value={form.reference_id} onChange={e=>change('reference_id',e.target.value)}><option value="">Selecciona una imagen</option>{assets.filter(a=>a.kind==='image').map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
          </SwapStage><SwapStage step="cast"><label>Persona que se reemplaza<input value={form.target} maxLength={200} onChange={e=>change('target',e.target.value)} placeholder="Describe a la persona si aparecen varias"/></label></SwapStage>
        </fieldset></StudioStep>
        <StudioStep guideStep="scene" number={3} title="Escena y cambios" summary={form.background==='video'?'Escenario original':'Escenario de la imagen'}><fieldset disabled={locked||!terminal}>
          {method?<p className="bunny-help">{form.background==='video'?'Escenario del vídeo original.':'Escenario de la imagen de referencia.'}</p>:<label>Escenario<select value={form.background} onChange={e=>change('background',e.target.value)}><option value="video">Conservar el del vídeo</option><option value="image">Usar el de la imagen</option></select></label>}
          <label>Indicaciones adicionales<textarea value={form.prompt} maxLength={12000} onChange={e=>change('prompt',e.target.value)} placeholder="Opcional. Qwen preparará las indicaciones de cada tramo; puedes añadir tus correcciones."/></label>
          {method?<p className="bunny-help">Qwen prepara los prompts y las indicaciones automáticamente. El chat de esta toma permite aclarar dudas y corregirlas.</p>:<label><input type="checkbox" checked={form.auto_describe} onChange={e=>change('auto_describe',e.target.checked)}/> Redactar también el prompt completo de SECourses con Qwen</label>}
          {formError&&<p role="alert">{formError}</p>}
        </fieldset></StudioStep>
        <StudioStep guideStep="quality" number={4} title="Duración y calidad" summary={`${(form.frames/24).toFixed(2)} s · ${form.variant==='fast'?'Rápida':'Normal'}`}><fieldset disabled={locked||!terminal}>
          <div className="bunny-interval-fields"><label>Inicio (segundos)<input type="number" min="0" step="any" value={form.start_seconds} onChange={e=>change('start_seconds',Number(e.target.value))}/></label>
          <label>Duración (segundos)<input type="number" min={5/24} max="60" step={1/24} value={Number((form.frames/24).toFixed(3))} onChange={e=>change('frames',Math.round(Number(e.target.value)*24))}/></label></div>
          <button type="button" className="secondary" disabled={!video||video.duration>60} onClick={()=>{change('start_seconds',0);change('frames',Math.round(video.duration*24));}}>Usar vídeo completo</button>
          <label>Variante<select value={form.variant} onChange={e=>change('variant',e.target.value)}><option value="fast">Rápida</option><option value="standard">Normal</option></select></label>
          <label>Calidad del vídeo<select value={form.megapixels} onChange={e=>change('megapixels',Number(e.target.value))}><option value={0.3}>Vista previa</option><option value={0.5}>Equilibrada</option><option value={1}>Alta</option></select></label>
          <p className="bunny-output-summary">Hasta 60 s · Tramos de hasta 5 s · {method==='faithful'?'5 fotogramas de contexto dentro del plano':'Continuidad dentro del plano'} · Reinicio al cambiar de plano.</p>
          <details><summary>Ajustes avanzados</summary>
            {[['seed','Semilla',0,9007199254740991,1],['guide_megapixels','Detalle de la guía (MP)',0.05,1,0.01],['threshold','Umbral de detección',0.05,0.95,0.01],['mask_grow','Ampliar máscara (píxeles)',0,64,1]].map(([key,label,min,max,step])=><label key={key}>{label}<input type="number" min={min} max={max} step={step} value={form[key]} onChange={e=>change(key,Number(e.target.value))}/></label>)}
            <label>Palabras pronunciadas<input value={form.speech} maxLength={2000} onChange={e=>change('speech',e.target.value)}/></label><label>Idioma<input value={form.speech_language} onChange={e=>change('speech_language',e.target.value)}/></label>
            <label><input type="checkbox" checked={form.lock_audio} onChange={e=>change('lock_audio',e.target.checked)}/> Guiar labios con el audio original</label>
          </details>
        </fieldset></StudioStep>
      </div>
      <SwapStage step="review"><StudioSlot name="actions"><div className="bunny-submit-footer"><div><strong>{label} · {form.variant==='fast'?'Rápida':'Normal'}</strong><p role="status">{pending?'Hay un envío sin confirmar.':otherPending?'Retoma el envío pendiente de otro método.':formError|| (busy?'Hay un trabajo en curso.':!valid?'Selecciona vídeo, personaje e intervalo.':'Listo para generar con Qwen.')}</p></div>
      <button className="primary" disabled={sending||loadingFile||uploading||(!pending&&(!ready||!valid||busy||!terminal))} onClick={generate}>{sending?'Enviando…':pending?'Recuperar envío':'Generar vídeo'}</button></div></StudioSlot></SwapStage>
    </section></ObjectSwapGuide>
    <StudioSlot name="result"><section className={'panel bunny-result'+(job?.result_available?' has-video-result':'')} data-studio-pane="result">
      {job?<><h2>{job.result_available?'Resultado · '+label:'Proceso · '+label}</h2><JobElapsed job={job}/><JobCancelButton job={job} onCancel={onCancel}/><p role="status">{job.stage}</p>{job.error&&<p role="alert">{job.error}</p>}
        {job.result_available&&<ResultViewer job={job} assets={assets} method={label}/>}
        {job.secourses_analysis&&<details><summary>Indicaciones del método</summary><p>{job.secourses_analysis.observations.summary_es}</p><textarea readOnly rows={8} value={job.secourses_analysis.prompt}/></details>}
      </>:<div className="bunny-empty-result"><h2>Tu vídeo aparecerá aquí</h2><p>Podrás revisar el personaje, el movimiento y la continuidad entre tramos.</p></div>}
    </section></StudioSlot>
  </div>;
}
