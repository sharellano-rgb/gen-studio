import {StudioSlot} from './StudioWorkspace.jsx';
import StudioStep from './StudioStep.jsx';
import ObjectSwapGuide,{SwapStage} from './ObjectSwapGuide.jsx';
import {sourceStep,referenceStep,intervalSummary} from './objectSwapGuide.js';
import './motionStudio.css';
import './unifiedMethods.css';
import JobElapsed from './JobElapsed.jsx';
import React,{useEffect,useRef,useState} from 'react';
import {workspaceSessionStorage as storage} from './workspaceStorage.js';
import {pointInVideo} from './ninjaState.js';
import {isNinjaShots,ninjaShotsLabel,ninjaShotLabel,validInterval,defaultSelections,canPrepare,canRender,maskApproval,maskApprovalKey} from './ninjaShotsState.js';
import './ninjaShots.css';
const terminal=new Set(['completed','failed','cancelled','submission_unknown']);
const pendingKey='gen-ninja-shots-pending';
function read(key,fallback){try{return JSON.parse(storage.getItem(key))||fallback;}catch{return fallback;}}
async function api(path,body,signal){
  const r=await fetch('/api'+path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal}:{signal});
  const data=await r.json();if(!r.ok){const e=Error(typeof data.detail==='string'?data.detail:'No se pudo completar la petición.');e.status=r.status;throw e;}return data;
}
export default function NinjaShots({assets,selection,job,draftParams,busy,uploading,onUpload,onDraftChanged,onCreated,onNewSequence,onError,onBusyChange,onReviewSaved}){
  const context=isNinjaShots(job)?job:null,p=context?.parameters;
  const [form,setForm]=useState(()=>({video_id:p?.video_id||draftParams?.video_id||selection.video||'',reference_id:p?.reference_id||draftParams?.reference_id||selection.image||selection.face||'',
    mode:p?.mode||draftParams?.mode||'head',start_seconds:p?.start_seconds??draftParams?.start_seconds??0,frames:p?.frames??draftParams?.frames??120,seed:p?.seed??draftParams?.seed??123456}));
  const [detail,setDetail]=useState(null),[choices,setChoices]=useState([]),[approved,setApproved]=useState({}),[loaded,setLoaded]=useState({}),[images,setImages]=useState({});
  const [motion,setMotion]=useState(p?.ninja_head_motion||draftParams?.head_motion||'preserve'),[pending,setPending]=useState(()=>read(pendingKey,null)),[sending,setSending]=useState(false),[error,setError]=useState('');
  const hydrated=useRef(null),sendLock=useRef(false),publishedStatus=useRef(context?.status);
  const current=detail?.job&&context&&detail.job.id===context.id?detail.job:context;
  const active=current&&!terminal.has(current.status),locked=Boolean(context||sending||uploading||pending);
  const [pollError,setPollError]=useState('');
  const video=assets.find(a=>a.id===form.video_id),reference=assets.find(a=>a.id===form.reference_id);
  const segments=detail?.segments||[],planId=detail?.plan_id;
  useEffect(()=>{if(!context)onDraftChanged?.({...form,ninja_by_cuts:true,engine:'h3_ninja',head_motion:motion});},[form,motion,context?.id,onDraftChanged]);
  useEffect(()=>{onBusyChange?.(Boolean(sending||pending||uploading));return()=>onBusyChange?.(false);},[sending,pending,uploading,onBusyChange]);
  useEffect(()=>{
    if(!context)return;let live=true,timer;const abort=new AbortController();
    async function poll(){
      try{const d=await api('/jobs/'+context.id+'/ninja-shots',null,abort.signal);if(!live)return;setDetail(d);setPollError('');
        if(publishedStatus.current!==d.job.status){publishedStatus.current=d.job.status;onCreated(d.job);}
        if(d.segments.length&&hydrated.current!==d.plan_id){
          const saved=read('gen-ninja-shots-selection:'+d.plan_id,null);
          setChoices(d.job.parameters.ninja_selections||saved||defaultSelections(d.segments));
          setApproved(read('gen-ninja-shots-approved:'+(d.masks_id||context.id),{}));hydrated.current=d.plan_id;
        }
      }catch(e){if(live)setPollError('No se pudo actualizar la secuencia. '+e.message);}
      if(live)timer=setTimeout(poll,3000);
    }poll();return()=>{live=false;abort.abort();clearTimeout(timer);};
  },[context?.id]);
  function choose(index,changes){setChoices(current=>{const next=current.map(s=>s.index===index?{...s,...changes}:s);storage.setItem('gen-ninja-shots-selection:'+planId,JSON.stringify(next));return next;});}
  function approve(s,value){const key=maskApprovalKey(s);setApproved(current=>{const next={...current,[key]:value};storage.setItem('gen-ninja-shots-approved:'+detail.masks_id,JSON.stringify(next));return next;});}
  async function submit(path,body){
    if(sendLock.current)return;sendLock.current=true;setSending(true);setError('');
    const request=pending||{path,body:{request_key:crypto.randomUUID(),...body}};
    try{storage.setItem(pendingKey,JSON.stringify(request));setPending(request);const result=await api(request.path,request.body);
      storage.removeItem(pendingKey);setPending(null);onCreated(result);
    }catch(e){if(e.status>=400&&e.status<500){storage.removeItem(pendingKey);setPending(null);}setError(e.message);}
    finally{sendLock.current=false;setSending(false);}
  }
  async function upload(e,kind){
    const input=e.currentTarget,file=input.files?.[0];if(!file)return;setSending(true);
    try{const asset=await onUpload(file,kind);setForm(f=>({...f,[kind==='video'?'video_id':'reference_id']:asset.id,...(kind==='video'?{start_seconds:0,frames:Math.min(120,Math.ceil(asset.duration*24))}:{})}));}
    catch(e){onError(e.message);}finally{setSending(false);input.value='';}
  }
  const approvals=segments.filter(s=>approved[maskApprovalKey(s)]&&loaded[maskApprovalKey(s)]).map(maskApproval).filter(Boolean);
  const canSelect=current?.parameters.operation==='ninja_shots_plan'&&current.status==='completed'&&!pending&&!sending&&!busy;
  const readyMasks=current?.parameters.operation==='ninja_shots_prepare'&&current.status==='completed';
  async function stop(){try{const result=await api('/jobs/'+context.id+'/ninja-shots/stop',{});onCreated(result);}catch(e){setError(e.message);}}
  const guideSteps=[
    sourceStep(video),referenceStep(reference,{summary:reference?.name+' · '+(form.mode==='head'?'Rostro y cabeza':'Cuerpo completo')}),
    {id:'quality',title:'Intervalo del vídeo',help:'Elige el fragmento que quieres dividir en planos.',summary:intervalSummary(form.start_seconds,form.frames),issue:!validInterval(form,assets)&&'Ajusta el intervalo al vídeo. El máximo es de 60 segundos.'},
    {id:'review',title:'Preparar planos y máscaras',help:'Comprueba la toma y prepara los tramos. Después podrás marcar a la persona en cada plano y revisar sus máscaras antes de generar.'}
  ];
  const recover=pending&&<div className="notice"><p>Falta confirmar el envío. Recupera la misma petición antes de iniciar otra.</p><button disabled={sending} onClick={()=>submit(pending.path,pending.body)}>Recuperar envío pendiente</button></div>;
  return <section className={'bunny-layout unified-method-layout ninja-shots'+(current?.result_available?' ninja-has-result':'')} aria-label="Refinamiento Facial por planos">
    <ObjectSwapGuide enabled={!context} recovery={Boolean(pending)} scope="h3_ninja" sourceId={form.video_id} steps={guideSteps} busy={sending||uploading}>
    <section className="panel bunny-form" data-studio-pane="edit"><div className="bunny-intro"><h2>Refinamiento Facial</h2>
    <p>Reemplaza rostro o cuerpo con máscaras revisadas. Los tramos de hasta 5 s enlazan el último fotograma dentro del mismo plano y se reinician en cada corte.</p></div>
    <StudioStep guideStep="source" number={1} title="Vídeo" summary={video?.name||'Añade el vídeo fuente'} defaultOpen><label>Vídeo fuente<select aria-label="Vídeo por planos" disabled={locked} value={form.video_id} onChange={e=>{const a=assets.find(a=>a.id===e.target.value);setForm(f=>({...f,video_id:e.target.value,start_seconds:0,frames:Math.min(120,Math.ceil((a?.duration||5)*24))}));}}><option value="">Selecciona un vídeo</option>{assets.filter(a=>a.kind==='video').map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></label><label>Subir vídeo<input type="file" accept=".mp4,.mov,.webm" disabled={locked} onChange={e=>upload(e,'video')}/></label>{video&&<video className="bunny-source-preview" src={'/api/assets/'+video.id} controls playsInline preload="metadata"/>}</StudioStep>
      <StudioStep guideStep="references" number={2} title="Personaje" summary={reference?.name||'Elige su imagen'}><label>Referencia del personaje<select aria-label="Referencia por planos" disabled={locked} value={form.reference_id} onChange={e=>setForm(f=>({...f,reference_id:e.target.value}))}><option value="">Selecciona una imagen</option>{assets.filter(a=>a.kind==='image').map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></label><label>Subir referencia<input type="file" accept=".jpg,.jpeg,.png,.webp" disabled={locked} onChange={e=>upload(e,'image')}/></label>{reference&&<img className="ninja-reference" src={'/api/assets/'+reference.id} alt="Referencia facial común para todos los planos"/>}<label>Zona a reemplazar<select value={form.mode} disabled={locked} onChange={e=>setForm(f=>({...f,mode:e.target.value}))}><option value="head">Rostro y cabeza</option><option value="body">Cuerpo completo</option></select></label></StudioStep>
    <StudioStep guideStep="quality" number={3} title="Duración y calidad" summary={`${(form.frames/24).toFixed(2)} s · Receta 2`}><div className="ninja-grid"><label>Inicio (segundos)<input type="number" min="0" step="0.041666667" disabled={locked} value={form.start_seconds} onChange={e=>setForm(f=>({...f,start_seconds:Number(e.target.value)}))}/></label><label>Duración (segundos)<input type="number" min="0.208333333" max="60" step="0.041666667" disabled={locked} value={Number((form.frames/24).toFixed(3))} onChange={e=>setForm(f=>({...f,frames:Math.round(Number(e.target.value)*24)}))}/></label></div>
    <button className="secondary" disabled={locked||!video||Math.ceil(video.duration*24)>1440} onClick={()=>setForm(f=>({...f,start_seconds:0,frames:Math.ceil(video.duration*24)}))}>Usar vídeo completo</button>
    <p>{form.frames} fotogramas · 24 fps · {form.start_seconds.toFixed(2)}–{(form.start_seconds+form.frames/24).toFixed(2)} s. Máximo 60 s por secuencia.{video?.duration>60&&' Este vídeo es más largo: selecciona un intervalo de hasta 60 s.'}</p>
    </StudioStep>
    {!context&&<>{error&&<p role="alert" className="notice error">{error}</p>}{recover}</>}
    <SwapStage step="review">{!context&&<button className="primary" disabled={sending||uploading||busy||!validInterval(form,assets)||Boolean(pending)} onClick={()=>submit('/ninja/shots/plan',form)}>Preparar tramos y planos</button>}</SwapStage>
    </section></ObjectSwapGuide>
    <StudioSlot name="result"><section className="panel bunny-result" data-studio-pane="result"><div className="bunny-result-heading"><h2>Revisión y resultado</h2></div>
    {context&&<div className="ninja-job"><strong>{ninjaShotsLabel(context)}</strong><p role="status">{detail?.job.stage||context.stage}</p><JobElapsed job={current}/>{(detail?.job.error||context.error)&&<p role="alert">{detail?.job.error||context.error}</p>}
      {active&&<button className="secondary" disabled={current.ninja_stop_requested} onClick={stop}>{current.ninja_stop_requested?'Detención solicitada':'Detener al terminar este plano'}</button>}
      {terminal.has(current.status)&&<><button className="secondary" disabled={sending||Boolean(pending)} onClick={()=>{onDraftChanged?.({...form,ninja_by_cuts:true,engine:'h3_ninja',head_motion:motion});onNewSequence?.();}}>Preparar otra secuencia</button>
        {detail?.plan&&current.parameters.operation!=='ninja_shots_plan'&&<button className="secondary" disabled={sending||busy||Boolean(pending)} onClick={()=>onCreated(detail.plan)}>Corregir selección de rostros</button>}</>}
    </div>}
    {context&&error&&<p className="notice error" role="alert">{error}</p>}
    {pollError&&<p className="notice" role="status">{pollError}</p>}
    {context&&recover}

    {segments.length>0&&<><h3>{`${segments.length} tramos · ${new Set(segments.map(s=>s.shot??s.index)).size} planos`}</h3><p>Comprueba los límites. Marca el personaje al inicio de cada tramo; su posición puede cambiar después de un corte. Los planos muy breves se conservan originales.</p></>}
    <div className="facial-shots-grid">{segments.map(s=>{const choice=choices[s.index]||{passthrough:s.keep<5},key=maskApprovalKey(s),mask=s.mask_job;return <article className="facial-shot" key={s.index}>
      <h4>{ninjaShotLabel(s,detail.plan.parameters.start_frame)}</h4>
      <div className="ninja-selector" role="button" tabIndex={canSelect&&!choice.passthrough&&images[s.index]?0:-1} aria-label={`Marcar rostro del plano ${s.index+1}`} aria-disabled={!canSelect||choice.passthrough||!images[s.index]} style={{aspectRatio:detail.plan.parameters.width+'/'+detail.plan.parameters.height}}
        onPointerUp={e=>{if(!canSelect||choice.passthrough||!images[s.index])return;const point=pointInVideo(e.clientX,e.clientY,e.currentTarget.getBoundingClientRect(),detail.plan.parameters.width,detail.plan.parameters.height);if(point)choose(s.index,point);}}
        onKeyDown={e=>{if(!canSelect||choice.passthrough||!images[s.index]||!['Enter',' ','ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(e.key))return;e.preventDefault();choose(s.index,{x:Math.max(0,Math.min(1,(choice.x??.5)+(e.key==='ArrowLeft'?-.01:e.key==='ArrowRight'?.01:0))),y:Math.max(0,Math.min(1,(choice.y??.5)+(e.key==='ArrowUp'?-.01:e.key==='ArrowDown'?.01:0)))});}}>
        <img src={s.thumbnail_url} alt={`Primer fotograma del plano ${s.index+1}`} onLoad={()=>setImages(v=>({...v,[s.index]:true}))} onError={()=>setImages(v=>({...v,[s.index]:false}))}/>
        {!choice.passthrough&&Number.isFinite(choice.x)&&<span className="ninja-marker" style={{left:choice.x*100+'%',top:choice.y*100+'%'}}/>}
      </div>
      <label><input type="checkbox" checked={choice.passthrough} disabled={!canSelect||s.keep<5} onChange={e=>choose(s.index,{passthrough:e.target.checked})}/> Conservar este plano sin cambios</label>
      {s.keep<5&&<p>Menos de cinco fotogramas: se conserva el original.</p>}
      {!choice.passthrough&&!mask&&<p>{Number.isFinite(choice.x)?'Rostro marcado':'Falta marcar el rostro'}</p>}
      {mask&&<><p>{mask.stage}</p>{mask.error&&<p role="alert">{mask.error}</p>}{mask.ninja_preview_available&&<><video src={'/api/jobs/'+mask.id+'/ninja-preview'} controls playsInline preload="metadata" onLoadedData={()=>setLoaded(v=>({...v,[key]:true}))} onError={()=>setLoaded(v=>({...v,[key]:false}))}/><label><input type="checkbox" checked={Boolean(approved[key])} disabled={!readyMasks||!loaded[key]||sending||Boolean(pending)} onChange={e=>approve(s,e.target.checked)}/> Revisé la máscara: sigue sólo a la persona correcta</label></>}</>}
      {s.render_job&&<><p>{s.render_job.stage}</p>{s.render_job.result_available&&<video src={'/api/jobs/'+s.render_job.id+'/result'} controls playsInline preload="metadata"/>}</>}
    </article>;})}</div>
    {canSelect&&<button className="primary ninja-action" disabled={!canPrepare(segments,choices)} onClick={()=>submit('/jobs/'+planId+'/ninja-shots/prepare',{shots:choices})}>Preparar máscaras de los planos</button>}
    {readyMasks&&<><label>Movimiento de la cabeza<select value={motion} disabled={sending||Boolean(pending)} onChange={e=>setMotion(e.target.value)}><option value="preserve">Priorizar movimiento del vídeo</option><option value="original">Ajuste original de la Receta 2</option></select></label><button className="primary ninja-action" disabled={sending||busy||Boolean(pending)||!canRender(segments,approvals)} onClick={()=>submit('/jobs/'+context.id+'/ninja-shots/render',{head_motion:form.mode==='body'?'original':motion,approvals})}>Generar y unir los planos</button></>}

      {current?.result_available&&<ResultViewer key={current.id} job={current} assets={assets} method="Refinamiento Facial" onReviewSaved={onReviewSaved}/>}
      {!segments.length&&<div className="empty-preview"><strong>Prepara el vídeo para revisar sus máscaras.</strong><p>Después podrás generar y unir los tramos.</p></div>}
    </section></StudioSlot>
  </section>;
}
import ResultViewer from './ResultViewer.jsx';
