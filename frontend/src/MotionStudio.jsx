import {StudioSlot} from './StudioWorkspace.jsx';
import React,{useEffect,useRef,useState} from 'react';
import {workspaceLocalStorage as storage} from './workspaceStorage.js';
import {bunnyDurations,bunnyIntervalValid,bunnySelectDuration,bunnyFormForJob} from './bunnyDuration.js';
import JobElapsed from './JobElapsed.jsx';
import {isActiveJob} from './jobActivity.js';
import JobCancelButton from './JobCancelButton.jsx';
import StudioStep from './StudioStep.jsx';
import BunnyResult from './BunnyResult.jsx';
import useStudioDraft from './useStudioDraft.js';
import {readStudioDraft,preserveSubmissionDraft,pendingDraftId,clearPendingDraft,recoverPendingDraft,createStudioDraft} from './studioDrafts.js';
import {DraftSaveStatus} from './DraftShelf.jsx';
import './motionStudio.css';
const PENDING='motion-bunny-pending-v1';
function read(k){try{return JSON.parse(storage.getItem(k));}catch{return null;}}
export default function MotionStudio({assets:libraryAssets,selection,job,draftId,onResumeDraft,busy,uploading,onUpload,onCreated,onOpen,onError,onCancel,onReviewSaved,view='edit',onViewChange}){
 const own=job?.parameters?.engine==='viggle_motion'?job:null;
 const [savedDraft]=useState(()=>readStudioDraft(storage,draftId));
 const [boundAssets,setBoundAssets]=useState(savedDraft?.assets||[]);
 const assets=[...libraryAssets,...boundAssets.filter(a=>!libraryAssets.some(b=>a.id===b.id))];
 const [pending,setPending]=useState(()=>read(PENDING)),[sending,setSending]=useState(false),[check,setCheck]=useState(null);
 const pendingHere=Boolean(pending&&pendingDraftId(storage,'bunny',pending.body.request_key)===draftId);
 const [form,setForm]=useState(()=>(pendingHere?pending.form:null)||savedDraft?.form||bunnyFormForJob(own)||{video_id:selection?.video||'',reference_id:'',environment_reference_id:'',frames:120,start_seconds:'0',quality:'balanced',scene_mode:'reference',direction:'',seed:42});
 const lock=useRef(false);
 const draftStatus=useStudioDraft({id:draftId,engine:'bunny',jobId:own?.id,form,assets,restored:Boolean(savedDraft),enabled:!pending});
 useEffect(()=>{if(!own||own.parameters.operation!=='motion_bunny')return;const abort=new AbortController();fetch('/api/jobs/'+own.id+'/bunny-inputs',{signal:abort.signal}).then(async r=>{if(!r.ok)throw Error('No se pudieron recuperar las entradas de Bunny.');return r.json();}).then(data=>{if(!abort.signal.aborted)setBoundAssets(current=>[...new Map([...current,...(data.assets||[])].map(a=>[a.id,a])).values()]);}).catch(e=>{if(!abort.signal.aborted)onError(e.message);});return()=>abort.abort();},[own?.id]);
 useEffect(()=>{let live=true;fetch('/api/workflows/motion-bunny/preflight').then(r=>r.json()).then(v=>{if(live)setCheck(v);}).catch(()=>{if(live)setCheck({ok:false});});return()=>{live=false;};},[]);
 useEffect(()=>{if(!form.video_id&&!pending&&assets.length){const v=assets.find(a=>a.id===selection?.video);if(v)setForm(f=>({...f,video_id:v.id,frames:Math.min(120,Math.round(v.duration*24)),start_seconds:'0'}));}},[assets,selection?.video,form.video_id,pending]);
 const video=assets.find(a=>a.id===form.video_id),images=assets.filter(a=>a.kind==='image');
 const ratio=video?(video.display_width||video.width)/(video.display_height||video.height):null;
 const area=form.quality==='high'?1152*640:960*544;
 const roundEven=x=>{const n=Math.floor(x);return x-n===.5?(n%2?n+1:n):Math.round(x);};
 const output=ratio?[ratio,1/ratio].map(r=>roundEven(Math.max(1,roundEven(Math.sqrt(area*r)/32))*1.5)*32).join(' × '):null;
 const locked=Boolean(sending||pending||uploading||busy);
 const valid=bunnyIntervalValid(video,form.frames,form.start_seconds)&&images.some(a=>a.id===form.reference_id);
 function update(k,v){setForm(f=>({...f,[k]:v}));}
 async function upload(e,role){const input=e.currentTarget,file=input.files[0];if(!file)return;try{const a=await onUpload(file,role==='video_id'?'video':'image');setForm(f=>({...f,[role]:a.id,...(role==='video_id'?{start_seconds:'0',frames:Math.min(120,Math.round(a.duration*24))}:{})}));}catch(e){onError(e.message);}finally{input.value='';}}
 async function send(){if(lock.current||(pending&&!pendingHere))return;lock.current=true;setSending(true);onError('');try{
 const request=pending||{form,body:{...form,request_key:crypto.randomUUID(),frames:Number(form.frames),start_seconds:Number(form.start_seconds),seed:Number(form.seed),environment_reference_id:form.scene_mode==='reference'?(form.environment_reference_id||null):null}};
 preserveSubmissionDraft(storage,{id:draftId,engine:'bunny',job_id:own?.id||null,form,assets},request.body.request_key,Boolean(pending));
 storage.setItem(PENDING,JSON.stringify(request));setPending(request);
 const response=await fetch('/api/motion-bunny/render',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request.body)});const result=await response.json();
 if(!response.ok){if(response.status>=400&&response.status<500){storage.removeItem(PENDING);setPending(null);}throw Error(typeof result.detail==='string'?result.detail:'No se pudo iniciar Bunny.');}
 if(!result.id||result.parameters?.operation!=='motion_bunny')throw Error('No se pudo confirmar la solicitud; reintenta con la misma clave.');
 storage.removeItem(PENDING);try{clearPendingDraft(storage,'bunny',request.body.request_key);}catch{}setPending(null);onCreated(result);onViewChange?.('result');
 }catch(e){onError(e.message);}finally{lock.current=false;setSending(false);}}
 const character=images.find(a=>a.id===form.reference_id),environment=images.find(a=>a.id===form.environment_reference_id);
 const segmentCount=Math.ceil(Number(form.frames)/120),seconds=Number(form.frames)/24;
 const intervalSummary=`${Number.isFinite(seconds)?Number(seconds.toFixed(2)):0} s · ${form.quality==='high'?'Alta':'Equilibrada'}`;
 const sceneSummary=form.scene_mode==='source'?'Conservar el escenario original':environment?.name||'Escenario de la imagen del personaje';
 const submitReason=sending?'Enviando tu solicitud…':uploading?'Espera a que termine la carga.':pending?'La solicitud está pendiente de confirmación. Puedes volver a consultarla sin duplicar el trabajo.':busy?'Hay un trabajo en proceso. Podrás generar cuando termine.':!video?'Añade un vídeo de movimiento.':!character?'Elige la imagen de tu personaje.':!bunnyIntervalValid(video,form.frames,form.start_seconds)?'Revisa que el intervalo esté dentro del vídeo y no supere 60 segundos.':!check?'Comprobando el motor…':!check.ok?'El motor Bunny no está disponible. Revisa su estado en Sistema.':'Todo listo para generar.';
 const active=isActiveJob(own);
 return <div className="motion-studio bunny-layout" data-studio-view={view}>
 <section className="panel bunny-form" data-studio-pane="edit">
  <div className="bunny-intro"><h2>Motion Transfer · Bunny</h2><p>El movimiento de tu vídeo con tu personaje y el escenario que elijas.</p></div>
  <DraftSaveStatus status={draftStatus}/>
  {pending&&!pendingHere&&<div className="draft-pending-warning" role="status">Hay un envío pendiente de otra toma. Retómalo para confirmar su estado sin duplicarlo.<button className="secondary" onClick={()=>{try{onResumeDraft(recoverPendingDraft(storage,'bunny',pending.body.request_key,pending.form,assets));}catch(e){onError(e.message);}}}>Retomar envío pendiente</button></div>}
  <div className="bunny-steps">
   <StudioStep number={1} title="Vídeo" summary={video?`${video.name} · ${Number(video.duration).toFixed(2)} s`:'Añade un vídeo de movimiento'} defaultOpen>
    <fieldset disabled={locked}>
    <label className={'upload-tile bunny-upload-tile '+(video?'source-loaded':'')}><span className="tile-icon" aria-hidden="true">＋</span><strong>{uploading?'Cargando…':video?'Cambiar vídeo':'Añadir vídeo de movimiento'}</strong><small>Selecciona un archivo de vídeo</small><input aria-label="Subir vídeo de movimiento para Bunny" type="file" accept="video/*" onChange={e=>upload(e,'video_id')}/></label>
    {video&&<><video className="bunny-source-preview" controls playsInline preload="metadata" src={'/api/assets/'+video.id}/><p className="bunny-media-caption">{video.name} · {Number(video.duration).toFixed(2)} s</p></>}
    <p className="bunny-help">Puedes elegir hasta 60 segundos en Duración y calidad.</p>
    </fieldset>
   </StudioStep>
   <StudioStep number={2} title="Personaje" summary={character?.name||'Elige la imagen de referencia'}>
    <fieldset disabled={locked}>
    <div className={'bunny-reference-picker '+(character?'has-reference':'')}>
     {character&&<img className="bunny-reference" src={'/api/assets/'+character.id} alt="Personaje de referencia"/>}
     <div className="bunny-reference-fields"><label className="upload-tile bunny-upload-tile"><span className="tile-icon" aria-hidden="true">＋</span><strong>{character?'Cambiar imagen':'Añadir imagen del personaje'}</strong><small>Usa una imagen clara de cuerpo completo</small><input aria-label="Subir imagen del personaje para Bunny" type="file" accept="image/*" onChange={e=>upload(e,'reference_id')}/></label>
      <label>O elige una imagen guardada<select aria-label="Imagen del personaje" value={form.reference_id} onChange={e=>update('reference_id',e.target.value)}><option value="">Selecciona una imagen</option>{images.map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
     </div>
    </div>
    </fieldset>
   </StudioStep>
   <StudioStep number={3} title="Escena y cambios" summary={sceneSummary}>
    <fieldset disabled={locked}>
    <label>Escenario<select value={form.scene_mode} onChange={e=>update('scene_mode',e.target.value)}><option value="reference">Cambiar según la referencia</option><option value="source">Conservar el escenario del vídeo</option></select></label>
    {form.scene_mode==='reference'&&<div className={'bunny-reference-picker '+(environment?'has-reference':'')}>
     {environment&&<img className="bunny-reference bunny-scene-reference" src={'/api/assets/'+environment.id} alt="Escenario de referencia"/>}
     <div className="bunny-reference-fields"><label className="upload-tile bunny-upload-tile"><strong>{environment?'Cambiar imagen del escenario':'Añadir imagen del escenario'}</strong><small>Opcional · Si no eliges una, se usará la imagen del personaje</small><input aria-label="Subir imagen del escenario para Bunny" type="file" accept="image/*" onChange={e=>upload(e,'environment_reference_id')}/></label>
      <label>Referencia del escenario<select aria-label="Referencia del escenario" value={form.environment_reference_id} onChange={e=>update('environment_reference_id',e.target.value)}><option value="">Usar el escenario de la imagen del personaje</option>{images.map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
     </div>
    </div>}
    <label>Indicaciones adicionales <span className="bunny-optional">Opcional</span><textarea value={form.direction} maxLength={1600} onChange={e=>update('direction',e.target.value)} placeholder="Describe qué quieres cambiar o conservar. Puedes escribir en español."/></label>
    </fieldset>
   </StudioStep>
   <StudioStep number={4} title="Duración y calidad" summary={intervalSummary}>
    <fieldset disabled={locked}>
    <div className="bunny-interval-fields"><label>Inicio (segundos)<input type="number" min="0" step="any" value={form.start_seconds} onChange={e=>update('start_seconds',e.target.value)}/></label>
     <label>Duración<select value={form.frames} onChange={e=>setForm(f=>bunnySelectDuration(f,video,e.target.value))}>{bunnyDurations(video,form.frames).map(n=><option key={n} value={n}>{(n/24).toFixed(2)} s{video&&n===Math.round(video.duration*24)?' · Vídeo completo':''}</option>)}</select></label>
    </div>
    <label>Calidad final<select value={form.quality} onChange={e=>update('quality',e.target.value)}><option value="balanced">Equilibrada</option><option value="high">Alta · Más tiempo y memoria</option></select></label>
    <p className="bunny-output-summary">{output?`${output} px · 24 fps · `:''}{segmentCount} {segmentCount===1?'tramo':'tramos'}{segmentCount>1?' · Continuidad automática':''}<br/>Se conserva la duración y el audio del intervalo.</p>
    <details className="bunny-how"><summary>Cómo se procesan los vídeos largos</summary><p>Los intervalos de más de 5 segundos se dividen en tramos de hasta 5 segundos. Cada tramo recibe dos pasadas; el último fotograma anterior guía la continuidad del siguiente.</p></details>
    </fieldset>
   </StudioStep>
  </div>
  <StudioSlot name="actions"><div className="bunny-submit-footer"><div><strong>{intervalSummary}</strong><p id="bunny-submit-reason" role={check&&!check.ok?'alert':undefined}>{submitReason}</p></div><button className="primary" aria-describedby="bunny-submit-reason" disabled={sending||uploading||(pending&&!pendingHere)||(!pending&&(!valid||busy||!check?.ok))} onClick={send}>{sending?'Enviando…':pending?'Confirmar solicitud':'Generar vídeo'}</button></div></StudioSlot>
 </section>
 <StudioSlot name="result"><section className={"panel bunny-result"+(own?.status==='completed'&&own.result_available?' has-video-result':'')} data-studio-pane="result">
  {own?<><h2>{own.parameters.operation==='motion_bunny'?'Resultado Bunny':'Resultado anterior'}</h2>{own.status!=='completed'&&!active&&own.stage&&<p>{own.stage}</p>}{own.status!=='completed'&&own.bunny_segment_total>0&&<p className="bunny-segment-progress">{own.bunny_segments_done||0} de {own.bunny_segment_total} tramos terminados{own.status!=='completed'&&Number.isInteger(own.bunny_segment_index)?' · Tramo actual '+(own.bunny_segment_index+1):''}</p>}{own.status!=='completed'&&<JobElapsed job={own}/>}<JobCancelButton job={own} onCancel={onCancel}/>{own.error&&<p role="alert">{own.error}</p>}{own.result_available&&(own.parameters.operation==='motion_bunny'&&own.status==='completed'?<BunnyResult key={own.id} job={own} assets={assets} onReviewSaved={onReviewSaved}/>:<><video controls playsInline src={'/api/jobs/'+own.id+'/result'}/><a className="bunny-download" href={'/api/jobs/'+own.id+'/result?download=true'}>Descargar vídeo</a></>)}{own.status==='completed'&&<details className="bunny-process-details"><summary>Información del trabajo</summary>{own.stage&&<p>{own.stage}</p>}<JobElapsed job={own}/></details>}<button className="secondary" disabled={Boolean(pending)||sending||Boolean(uploading)} onClick={()=>{try{onResumeDraft(createStudioDraft(storage,'bunny',form,assets));}catch(e){onError(e.message);}}}>Usar ajustes en otra toma</button></>:<div className="bunny-empty-result"><span className="bunny-empty-mark" aria-hidden="true">▷</span><h2>Tu resultado aparecerá aquí</h2><p>Prepara el vídeo y el personaje en Editar. Al generar podrás seguir el progreso y revisar el vídeo en esta vista.</p><button className="secondary bunny-return-edit" onClick={()=>onViewChange?.('edit')}>Volver a editar</button></div>}
 </section></StudioSlot>
 </div>;
}
