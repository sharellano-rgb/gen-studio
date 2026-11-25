import React,{useEffect,useRef,useState} from 'react';
import {workspaceLocalStorage as storage,activeWorkspaceId} from './workspaceStorage.js';
import {imageSize,imageValid,imagePreflight,imageRequest,imageAcknowledges,imageForm,readImageDraft,imageModeForm,imageReferenceForm,imageEditProfileForm,isQwenImage,imageModelForm,imageEndpoint,isImageUpscale} from './imageState.js';
import {uploadAsset} from './assetUploads.js';
import JobCancelButton from './JobCancelButton.jsx';
import JobElapsed from './JobElapsed.jsx';
import {imageHistoryDelay,imageHistoryPath} from './imageHistoryPolling.js';
import './imageStudio.css';

const TERMINAL=new Set(['completed','failed','cancelled','submission_unknown']);
const LABELS={queued:'En cola',preparing:'Preparando',submitting:'Enviando',running:'Generando',downloading:'Guardando',completed:'Generada',failed:'Error',cancelled:'Cancelada',cancelling:'Cancelando',submission_unknown:'Envío sin confirmar'};
const FORMATS=[['square','Cuadrado · 1:1'],['portrait','Retrato · 7:9'],['landscape','Horizontal · 9:7'],['vertical','Vertical · 9:16'],['wide','Panorámico · 16:9']];
function read(key,fallback){try{return JSON.parse(storage.getItem(key)||'null')??fallback;}catch{return fallback;}}
async function workspaceFetch(path,options){
 const r=await fetch(path,options);
 if(r.headers.get('X-Gen-Studio-Workspace')!==activeWorkspaceId()){window.location.reload();throw Error('Actualizando tu espacio…');}
 return r;
}
async function api(path,options){
 const r=await workspaceFetch('/api'+path,options),d=await r.json();
 if(!r.ok){const e=Error(typeof d.detail==='string'?d.detail:'No se pudo completar la solicitud.');e.status=r.status;throw e;}
 return d;
}
const newSeed=()=>crypto.getRandomValues(new Uint32Array(1))[0];
const realismLabel=p=>({lenovo:'Lenovo UltraReal',oldnokia:'oldNokia Ultrareal',smartphone:'Smartphone Snapshot v14'}[p.realism_lora]||'');

export default function ImageStudio({generationEnabled,onReference,onError,view='create',onViewChange}){
 const [form,setForm]=useState(()=>readImageDraft(storage)),[jobs,setJobs]=useState([]),[selected,setSelected]=useState(()=>read('moss-image-selected-v1',''));
 const [pending,setPending]=useState(()=>read('moss-image-pending-v1',null)),[sending,setSending]=useState(false),[check,setCheck]=useState(null),[checkRetry,setCheckRetry]=useState(0);
 const [networkError,setNetworkError]=useState(''),[next,setNext]=useState(null),[more,setMore]=useState(false),[saved,setSaved]=useState(''),[full,setFull]=useState(false),[referenceReady,setReferenceReady]=useState('');
 const [assets,setAssets]=useState([]),[assetsLoading,setAssetsLoading]=useState(true),[assetError,setAssetError]=useState(''),[assetRetry,setAssetRetry]=useState(0);
 const [uploading,setUploading]=useState(false),[referenceBusy,setReferenceBusy]=useState(false),[previewError,setPreviewError]=useState(false);
 const lock=useRef(false),live=useRef(true),loadedMore=useRef(false),selectedRef=useRef(selected),composer=useRef(null);selectedRef.current=selected;
 const jobsRef=useRef(jobs),refreshHistory=useRef(null);jobsRef.current=jobs;
 const historyView=view==='history';
 const qwen=isQwenImage(form),upscale=isImageUpscale(form),edit=form.mode==='edit'||upscale,mode=upscale?'upscale':edit?'edit':'generate',mystic=form.edit_profile==='mystic_base8';
 const preflight=imagePreflight(form);
 const source=assets.find(a=>a.id===form.edit_reference_id)||null;
 const job=jobs.find(j=>j.id===selected)||null,active=Boolean(job&&!TERMINAL.has(job.status));
 const locked=sending||Boolean(pending)||uploading||referenceBusy,size=imageSize(form);
 const available=check?.key===preflight.key&&check.ok;
 const valid=imageValid(form)&&(!edit||Boolean(source&&!previewError))&&(!qwen||!edit||(form.extra_reference_ids||[]).every(id=>assets.some(a=>a.id===id)));

 useEffect(()=>{live.current=true;return()=>{live.current=false;};},[]);
 useEffect(()=>{try{storage.setItem('moss-image-draft-v1',JSON.stringify(form));setSaved('Borrador guardado en este navegador.');}catch{setSaved('No se pudo guardar el borrador.');}},[form]);
 useEffect(()=>{try{storage.setItem('moss-image-selected-v1',JSON.stringify(selected));}catch{}setFull(false);setReferenceReady('');},[selected]);
 useEffect(()=>{
  if(historyView)return;
  let current=true;setCheck(null);
  api(preflight.path).then(v=>{if(current)setCheck({...v,key:preflight.key});}).catch(e=>{if(current)setCheck({ok:false,errors:[e.message],key:preflight.key});});
  return()=>{current=false;};
 },[checkRetry,preflight.key,historyView]);
 useEffect(()=>{
  if(historyView)return;
  let current=true;setAssetsLoading(true);
  api('/assets').then(v=>{if(current){setAssets(v.assets.filter(a=>a.kind==='image'));setAssetError('');}}).catch(e=>{if(current)setAssetError('No se pudieron cargar tus imágenes: '+e.message);}).finally(()=>{if(current)setAssetsLoading(false);});
  return()=>{current=false;};
 },[assetRetry,historyView]);
 useEffect(()=>{
  if(!edit||!source)return;
  setForm(f=>{const next=imageReferenceForm(f,source);return f.edit_reference_width===next.edit_reference_width&&f.edit_reference_height===next.edit_reference_height?f:next;});
 },[mode,source]);
 useEffect(()=>{setPreviewError(false);},[form.edit_reference_id]);
 useEffect(()=>{
  let current=true,inFlight=false,timer=null;
  const controller=new AbortController();
  const schedule=()=>{clearTimeout(timer);if(current)timer=setTimeout(refresh,imageHistoryDelay(jobsRef.current,document.hidden));};
  async function refresh(){
   if(!current||inFlight)return;
   clearTimeout(timer);
   if(document.hidden){schedule();return;}
   inFlight=true;
   try{
    const id=selectedRef.current,path=imageHistoryPath(historyView,id);
    const data=path?await api(path,{signal:controller.signal}):null;
    const h=historyView?data:{jobs:data?.parameters?.operation==='image_generation'?[data]:[],next_before:null};let extra=null;
    if(historyView&&id&&!h.jobs.some(j=>j.id===id)){const candidate=await api('/jobs/'+id,{signal:controller.signal});if(candidate.parameters.operation==='image_generation')extra=candidate;}
    if(!current)return;
    const merged=[...new Map([...h.jobs,...(extra?[extra]:[]),...jobsRef.current.filter(j=>!h.jobs.some(n=>n.id===j.id))].map(j=>[j.id,j])).values()];
    jobsRef.current=merged;setJobs(merged);
    if(!loadedMore.current)setNext(h.next_before);setNetworkError('');
   }catch(e){if(current&&e.name!=='AbortError')setNetworkError('No se pudo actualizar el historial: '+e.message);}
   finally{inFlight=false;schedule();}
  }
  const resume=()=>{if(!document.hidden)refresh();else clearTimeout(timer);};
  refreshHistory.current=refresh;document.addEventListener('visibilitychange',resume);refresh();
  return()=>{current=false;controller.abort();clearTimeout(timer);refreshHistory.current=null;document.removeEventListener('visibilitychange',resume);};
 },[historyView,selected]);
 useEffect(()=>{if(!full)return;const close=e=>{if(e.key==='Escape')setFull(false);};document.addEventListener('keydown',close);return()=>document.removeEventListener('keydown',close);},[full]);

 function update(key,value){setForm(f=>({...f,[key]:value}));}
 function keepAsset(asset){setAssets(current=>[asset,...current.filter(a=>a.id!==asset.id)]);onReference?.(asset);}
 async function upload(event,extraIndex=null){
  const file=event.target.files?.[0];event.target.value='';if(!file||locked)return;
  setUploading(true);setAssetError('');onError('');
  try{
   const asset=await uploadAsset(file,'image',workspaceFetch);
   if(live.current){keepAsset(asset);setForm(f=>extraIndex===null?imageReferenceForm(f,asset):{...f,extra_reference_ids:[...(f.extra_reference_ids||[]).slice(0,extraIndex),asset.id,...(f.extra_reference_ids||[]).slice(extraIndex+1)]});setPreviewError(false);}
  }catch(e){if(live.current)setAssetError(e.message);}
  finally{if(live.current)setUploading(false);}
 }
 async function send(){
  if(lock.current||uploading||referenceBusy||(!pending&&(!valid||!generationEnabled||!available)))return;
  lock.current=true;setSending(true);onError('');
  const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),30000);
  try{
   const body=pending||imageRequest(form,crypto.randomUUID());
   storage.setItem('moss-image-pending-v1',JSON.stringify(body));setPending(body);
   const j=await api(imageEndpoint(body),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:abort.signal});
   if(!imageAcknowledges(j,body))throw Error('El servidor no confirmó esta imagen. Reintenta con la misma solicitud.');
   storage.removeItem('moss-image-pending-v1');
   if(live.current){setPending(null);setSelected(j.id);setJobs(current=>[j,...current.filter(x=>x.id!==j.id)]);refreshHistory.current?.();}
  }catch(e){
   if(e.status>=400&&e.status<500){storage.removeItem('moss-image-pending-v1');if(live.current)setPending(null);}
   if(live.current)onError(e.name==='AbortError'?'Envío pendiente de confirmar. Reintenta la misma solicitud para evitar duplicados.':e.message);
  }finally{clearTimeout(timer);lock.current=false;if(live.current)setSending(false);}
 }
 async function cancel(id){try{const j=await api('/jobs/'+id+'/cancel',{method:'POST'});if(live.current)setJobs(current=>current.map(x=>x.id===id?j:x));}catch(e){onError(e.message);}}
 async function reference(forEdit=false){
  if(referenceBusy||!job||forEdit&&locked)return;
  setReferenceBusy(true);onError('');
  try{
   const asset=await api('/images/'+job.id+'/reference');
   if(!live.current)return;keepAsset(asset);
   if(forEdit){
    setForm(f=>({...imageReferenceForm(imageModeForm(forEdit==='upscale'?imageModelForm(f,'qwen_image21'):f,forEdit==='upscale'?'upscale':'edit'),asset),prompt:''}));
    setAssetError('');setPreviewError(false);setReferenceReady(forEdit==='upscale'?'Imagen preparada para ampliar. Elige el tamaño de salida.':'Imagen preparada para editar. Describe qué quieres cambiar.');
    composer.current?.scrollIntoView({block:'start',behavior:'smooth'});
   }else setReferenceReady('Lista en tus referencias de personaje y escenario. Puedes seleccionarla al crear un vídeo.');
  }catch(e){if(live.current)onError(e.message);}
  finally{if(live.current)setReferenceBusy(false);}
 }
 async function loadMore(){if(more||!next)return;setMore(true);try{const h=await api('/images/history?before='+next);setJobs(current=>[...new Map([...current,...h.jobs].map(j=>[j.id,j])).values()]);loadedMore.current=true;setNext(h.next_before);}catch(e){onError(e.message);}finally{setMore(false);}}
 const result=job?.status==='completed'&&job.result_available?'/api/images/'+job.id+'/result':null;
 const status=pending?'Hay una solicitud pendiente de confirmar.':uploading?'Subiendo tu imagen…':!generationEnabled?'Generación desactivada.':!check||check.key!==preflight.key?'Comprobando el modelo…':!available?'Este modo no está disponible.':edit&&!source?'Añade una imagen para comenzar.':active?'Tu imagen está en proceso. Puedes preparar otra.':upscale?'Lista para ampliar. Se guardará una nueva imagen.':edit?'Lista para editar. Se guardará una nueva imagen.':'Lista para crear. La generación comparte motor con tus vídeos.';
 return <div className="image-studio">
 <div className="image-section-actions">{historyView
  ?<button type="button" className="primary" onClick={()=>onViewChange?.('create')}>Nueva imagen <span aria-hidden="true">＋</span></button>
  :<button type="button" className="secondary" onClick={()=>onViewChange?.('history')}>Ver historial de imágenes ↗</button>}</div>
 {!historyView&&networkError&&<p role="status" className="notice error">{networkError}</p>}
 {!historyView&&<div className="image-workbench">
  <section ref={composer} className="panel image-composer" aria-label={upscale?'Ampliar imagen':edit?'Editar imagen':'Crear imagen'}>
   <div className="image-model"><span className="image-spark" aria-hidden="true">✦</span><div><h2>{qwen?'Qwen Image 2.1':edit?'MOSS Image Edit':mystic?'MOSS Image BASE':'MOSS-Image-SFT'}</h2><p>{upscale?'Upscale · Detail Enhancer · En tu PC':edit?'Transforma una imagen · En tu PC':'Texto a imagen · En tu PC'}</p></div><span className="image-local">LOCAL</span></div>
   <label className="image-model-picker">Modelo<select disabled={locked} value={qwen?'qwen_image21':'moss'} onChange={e=>setForm(f=>imageModelForm(f,e.target.value))}><option value="moss">MOSS Image</option><option value="qwen_image21">Qwen Image 2.1 · Crear y editar</option></select></label>
   <div className={'image-mode'+(qwen?' image-mode-qwen':'')} role="group" aria-label="Modo de imagen">
    <button type="button" aria-pressed={!edit} disabled={locked} onClick={()=>setForm(f=>imageModeForm(f,'generate'))}>Crear imagen</button>
    <button type="button" aria-pressed={edit&&!upscale} disabled={locked} onClick={()=>setForm(f=>imageModeForm(f,'edit'))}>Editar imagen</button>
    {qwen&&<button type="button" aria-pressed={upscale} disabled={locked} onClick={()=>setForm(f=>imageModeForm(f,'upscale'))}>Upscale</button>}
   </div>
   <fieldset disabled={locked}>
    {!qwen&&<div className="image-edit-profile">
     <label htmlFor="moss-edit-profile">{edit?'Perfil de edición':'Perfil de generación'}<select id="moss-edit-profile" value={form.edit_profile||'standard'} onChange={e=>setForm(f=>imageEditProfileForm(f,e.target.value))}>
      <option value="standard">MOSS actual · SFT</option><option value="mystic_base8">MOSS BASE + Mystic · 8 pasos</option>
     </select></label>
     <p className="image-hint">{mystic?'MOSS BASE int8 con el adaptador BASE de 8 pasos y el LoRA Mystic. Empieza con 8 pasos y adherencia 3; puedes ajustar sus intensidades.':edit?'El perfil habitual de edición con MOSS SFT. Empieza con 30 pasos y adherencia 4.':'MOSS SFT para crear imágenes desde texto. Empieza con 50 pasos y adherencia 3.'}</p>
     <label htmlFor="moss-realism">Acabado fotográfico<select id="moss-realism" value={form.realism_lora||'none'} onChange={e=>setForm(f=>({...f,realism_lora:e.target.value,realism_strength:f.realism_strength??1}))}>
      <option value="none">Sin acabado adicional</option><option value="lenovo">Lenovo UltraReal</option><option value="oldnokia">oldNokia Ultrareal</option><option value="smartphone">Smartphone Snapshot Photo Reality · v14</option>
     </select></label><p className="image-hint">Elige uno para comparar su aspecto fotográfico. Puedes ajustar su intensidad en Ajustes de {edit?'edición':'generación'}{mystic?' y poner Mystic a 0 para probarlo por separado.':'. Se conservan los pasos y ajustes de SFT.'}</p>
    </div>}
    {qwen&&<div className="image-qwen-description"><p className="image-hint">{upscale?<>Amplía tu imagen con <a href="https://huggingface.co/reverentelusarca/elusarcas-qwen-2.1-detail-enhancer-lora" target="_blank" rel="noreferrer">Detail Enhancer de Elusarca ↗</a>. Mantiene la proporción y genera detalle nuevo; puede modificar rasgos o letras pequeñas. Se guarda una copia.</>:edit?'Edita la imagen principal con tus instrucciones. Puedes añadir hasta tres referencias más y nombrarlas como imagen 2, 3 y 4.':'Crea una imagen desde tu descripción con Qwen Image 2.1.'}</p><p className="image-hint">Licencia de investigación y evaluación no comercial. El uso comercial requiere una licencia aparte. <a href="https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE" target="_blank" rel="noreferrer">Ver licencia ↗</a></p></div>}
    {edit&&<div className="image-edit-source">
     <label htmlFor="moss-image-upload">{source?'Cambiar imagen':upscale?'Añadir imagen para ampliar':'Añadir imagen para editar'}<input id="moss-image-upload" type="file" accept=".png,.jpg,.jpeg,.webp" onChange={e=>upload(e)}/></label>
     <label htmlFor="moss-image-reference">O elige una imagen de tu espacio<select id="moss-image-reference" value={form.edit_reference_id||''} onChange={e=>{setForm(f=>imageReferenceForm(f,assets.find(a=>a.id===e.target.value)));setAssetError('');}}>
      <option value="">{assetsLoading?'Cargando tus imágenes…':'Selecciona una imagen'}</option>
      {form.edit_reference_id&&!source&&<option value={form.edit_reference_id}>Imagen no disponible</option>}
      {assets.map(a=><option key={a.id} value={a.id}>{a.name}</option>)}
     </select></label>
     {source&&<figure><img src={'/api/assets/'+source.id} alt="Imagen original que se editará" onError={()=>setPreviewError(true)}/><figcaption>{source.name}</figcaption></figure>}
     {qwen&&!upscale&&<div className="image-extra-references"><p className="image-hint">La imagen 1 fija la proporción del resultado. Indica qué tomar de cada referencia.</p>{(form.extra_reference_ids||[]).map((id,index)=><div className="image-extra-reference" key={index}><label>Imagen {index+2}<select value={id} onChange={e=>setForm(f=>({...f,extra_reference_ids:f.extra_reference_ids.map((value,i)=>i===index?e.target.value:value)}))}><option value="">Selecciona una referencia</option>{assets.map(a=><option key={a.id} value={a.id} disabled={a.id===form.edit_reference_id||(form.extra_reference_ids||[]).some((other,i)=>i!==index&&other===a.id)}>{a.name}</option>)}</select></label><label>Subir referencia {index+2}<input type="file" accept=".png,.jpg,.jpeg,.webp" onChange={e=>upload(e,index)}/></label>{id&&<img src={'/api/assets/'+id} alt={'Referencia '+(index+2)} loading="lazy"/>}<button type="button" className="secondary" onClick={()=>setForm(f=>({...f,extra_reference_ids:f.extra_reference_ids.filter((_,i)=>i!==index)}))}>Quitar referencia {index+2}</button></div>)}{(form.extra_reference_ids||[]).length<3&&<button type="button" className="secondary" onClick={()=>setForm(f=>({...f,extra_reference_ids:[...(f.extra_reference_ids||[]),'']}))}>Añadir otra referencia</button>}</div>}
     {uploading&&<p role="status" className="image-hint">Subiendo y verificando tu imagen…</p>}
     {assetError&&<p role="alert" className="notice error">{assetError}</p>}
     {assetError&&!uploading&&<button type="button" className="secondary" onClick={()=>setAssetRetry(v=>v+1)}>Actualizar imágenes</button>}
     {previewError&&<p role="alert" className="notice error">No se pudo abrir esta imagen. Vuelve a cargarla o selecciona otra.</p>}
     {!assetsLoading&&form.edit_reference_id&&!source&&<p role="status" className="notice error">La imagen guardada no está disponible en este espacio. Selecciona otra.</p>}
    </div>}
    <label htmlFor="moss-prompt">{upscale?'Indicaciones adicionales (opcional)':edit?'¿Qué quieres cambiar?':'Describe tu imagen'}</label>
    <textarea id="moss-prompt" value={form.prompt} maxLength={12000} onChange={e=>update('prompt',e.target.value)} placeholder={upscale?'Por ejemplo: conserva el rostro y las letras; mejora el detalle de la tela.':edit?'Cambia el fondo por un auditorio con luces cálidas. Conserva el rostro, la ropa y la pose de la persona.':'Un auditorio vacío con luces cálidas, un suelo mojado con reflejos y una atmósfera cinematográfica…'}/>
    <p className="image-hint">{upscale?'Puedes dejarlo vacío. Se aplica automáticamente la indicación de mejora y conservación de la composición.':edit?'Describe el cambio y lo que quieres conservar. El resultado se guarda como una nueva imagen.':'Describe el sujeto, la escena, la luz y el estilo. Pon entre comillas cualquier texto que deba aparecer.'}</p>
    <div className={'image-settings'+(edit?' image-settings-single':'')}>
     {!edit&&<label>Formato<select value={form.format} onChange={e=>update('format',e.target.value)}>{FORMATS.map(([v,label])=><option key={v} value={v}>{label}</option>)}</select></label>}
     <label>{upscale?'Tamaño de salida':'Calidad'}<select value={form.quality} onChange={e=>update('quality',e.target.value)}><option value="standard">{upscale?'2 MP · según proporción':'Estándar · 1 MP'}</option><option value="high">{upscale?'4 MP · según proporción':'Alta · 2 MP'}</option></select></label>
    </div>
    <p className="image-size">{size?size.join(' × ')+' px · PNG · '+form.steps+' pasos':edit?'El tamaño se calcula al elegir tu imagen.':'Selecciona un formato.'}{edit&&size&&<span>Formato según la imagen de origen.</span>}</p>
    {edit&&source&&!size&&<p role="alert" className="notice error">{upscale?'La imagen ya alcanza ese tamaño o tiene una proporción demasiado alargada. Elige 4 MP o una imagen más pequeña. No se reducirá tu original.':'Esta proporción es demasiado estrecha o alargada. Usa otra imagen o recórtala antes de subirla.'}</p>}
    <details className="image-advanced"><summary>Ajustes de {upscale?'upscale':edit?'edición':'generación'}</summary>
     <label>Evitar en la imagen<textarea value={form.negative_prompt} maxLength={3000} rows={3} onChange={e=>update('negative_prompt',e.target.value)} placeholder="Por ejemplo: texto borroso, marcas de agua"/></label>
     <div className="image-settings"><label>Pasos<input type="number" min={upscale?'12':qwen||mystic?'4':'20'} max={upscale?'40':'80'} step="1" value={form.steps} onChange={e=>update('steps',e.target.value)}/></label><label>Adherencia al prompt<input type="number" min="1" max={upscale?'4':'6'} step="0.1" value={form.cfg} onChange={e=>update('cfg',e.target.value)}/></label></div>
     {upscale&&<div className="image-upscale-controls"><label>Intensidad del detalle · {Number(form.upscale_strength).toFixed(2)}<input type="range" min="0.1" max="1.5" step="0.05" value={form.upscale_strength} onChange={e=>update('upscale_strength',e.target.value)}/></label><label>Libertad de reconstrucción · {Number(form.upscale_denoise).toFixed(2)}<input type="range" min="0.3" max="1" step="0.05" value={form.upscale_denoise} onChange={e=>update('upscale_denoise',e.target.value)}/></label><p className="image-hint">Empieza con detalle 1 y reconstrucción 0.8. Baja la reconstrucción para conservar más del original; puede añadir menos detalle.</p></div>}
     <label>Semilla<div className="image-seed"><input type="number" min="0" max="9007199254740991" step="1" value={form.seed} onChange={e=>update('seed',e.target.value)}/><button type="button" className="secondary" onClick={()=>update('seed',newSeed())}>Aleatoria</button></div></label>
     {edit&&!qwen&&<label>Intensidad de edición<input type="number" min="0.1" max="2" step="0.05" value={form.edit_strength} onChange={e=>update('edit_strength',e.target.value)}/><span className="image-hint">1 es el valor recomendado para empezar.</span></label>}
     {!qwen&&(mystic?<div className="image-mystic-settings" role="group" aria-label="Ajustes de MOSS BASE y Mystic">
      <p className="image-hint">{edit?'Modelo MOSS BASE int8 · Los dos adaptadores se aplican antes de MOSS Image Edit.':'Modelo MOSS BASE int8 · Crea desde tu descripción con los LoRA elegidos.'}</p>
      <div className="image-settings"><label>Intensidad de Mystic<input type="number" min="0" max="2" step="0.05" value={form.mystic_strength} onChange={e=>update('mystic_strength',e.target.value)}/></label><label>Intensidad del LoRA BASE de 8 pasos<input type="number" min="0.1" max="2" step="0.05" value={form.distill_strength} onChange={e=>update('distill_strength',e.target.value)}/></label></div>
      <div className="image-settings"><label>Muestreador<select value={form.edit_sampler} onChange={e=>update('edit_sampler',e.target.value)}><option value="er_sde">ER SDE</option><option value="euler">Euler</option></select></label><label>Plan de ruido<select value={form.edit_scheduler} onChange={e=>update('edit_scheduler',e.target.value)}><option value="sgm_uniform">SGM Uniform</option><option value="normal">Normal</option></select></label></div>
      <p className="image-hint">Punto de partida: Mystic 0.7 y LoRA BASE 0.65. El activador de Mystic se añade automáticamente. La imagen de origen, el prompt y estos ajustes influyen en el resultado.</p>
     </div>:<div className="image-lora">
      <label className="image-lora-toggle"><input type="checkbox" checked={Boolean(form.lora_enabled)} onChange={e=>{const on=e.target.checked;setForm(f=>({...f,lora_enabled:on,lora_strength:on&&(!f.lora_strength||Number(f.lora_strength)<=0)?0.7:f.lora_strength}));}}/><span>Estilo LoRA · MOSS NSFW</span></label>
      <label>Intensidad · {Number(form.lora_strength).toFixed(2)}<input type="range" min="0" max="2" step="0.05" value={form.lora_strength??0} disabled={!form.lora_enabled} onChange={e=>update('lora_strength',e.target.value)}/></label>
      <p className="image-hint">Añade el estilo del LoRA (palabra activadora moss_xvstyle) a tu prompt. Inténtalo entre 0.5 y 0.8.</p>
     </div>)}
     {!qwen&&form.realism_lora&&form.realism_lora!=='none'&&<label>Intensidad del acabado fotográfico<input type="number" min="0" max="2" step="0.05" value={form.realism_strength??1} onChange={e=>update('realism_strength',e.target.value)}/></label>}
     <p className="image-hint">{upscale?'25 pasos y adherencia 1 son el punto de partida de Detail Enhancer.':qwen?'20 pasos y adherencia 3 son el punto de partida de Qwen Image 2.1.':mystic?'8 pasos y adherencia 3 son el punto de partida de este perfil.':edit?'30 pasos y adherencia 4 son el punto de partida para editar.':'50 pasos es el punto de partida recomendado.'} La misma semilla y los mismos ajustes permiten repetir una imagen.</p>
    </details>
   </fieldset>
   <div className="image-generate">
    <p role="status">{status}</p>
    {check?.key===preflight.key&&!check.ok&&<><p className="notice error">{check.errors?.join(' · ')}</p><button className="secondary" onClick={()=>setCheckRetry(v=>v+1)}>Volver a comprobar</button></>}
    <button type="button" className="primary" disabled={sending||uploading||referenceBusy||(!pending&&(!valid||!available||!generationEnabled))} onClick={send}>{sending?'Enviando…':pending?'Confirmar solicitud':upscale?'Ampliar imagen':edit?'Crear imagen editada':'Generar imagen'}<span aria-hidden="true">↗</span></button>
    <small>{saved}</small>
   </div>
  </section>
  <section className={'panel image-result'+(result?' has-image':'')} aria-label="Resultado de imagen">
   {job?<><div className="image-result-heading"><span className="eyebrow">{LABELS[job.status]||job.status}</span><h2>{result?isImageUpscale(job.parameters)?'Tu imagen, con más detalle.':job.parameters.mode==='edit'?'Tu imagen, transformada.':'Tu idea, en imagen.':job.stage}</h2></div>
    {result?<><button type="button" className="image-artwork" aria-label="Ampliar imagen generada" onClick={()=>setFull(true)}><img src={result} alt={job.parameters.prompt}/></button>
     <div className="image-result-actions"><a className="primary" href={result+'?download=true'}>Descargar PNG ↓</a><button type="button" className="secondary" disabled={locked} onClick={()=>reference(true)}>Editar esta imagen</button><button type="button" className="secondary" disabled={locked} onClick={()=>reference('upscale')}>Ampliar con Qwen</button><button type="button" className="secondary" disabled={referenceBusy} onClick={()=>reference(false)}>Usar como referencia</button><button type="button" className="secondary" disabled={locked} onClick={()=>setForm(imageForm(job))}>Copiar ajustes</button></div>
     {referenceReady&&<p role="status" className="image-reference-ready">{referenceReady}</p>}
     <p className="image-hint">{job.parameters.resolution.replace('x',' × ')} px · Semilla {job.parameters.seed} · {job.parameters.steps} pasos{isImageUpscale(job.parameters)?' · Qwen Image 2.1 · Detail Enhancer':isQwenImage(job.parameters)?' · Qwen Image 2.1':job.parameters.edit_profile==='mystic_base8'?' · MOSS BASE + Mystic':''}{realismLabel(job.parameters)&&' · '+realismLabel(job.parameters)}</p>
    </>:<div className="image-waiting"><span className="image-orbit" aria-hidden="true"/><p>Tu imagen aparecerá aquí. Puedes salir y volver al historial.</p></div>}
    <JobElapsed job={job}/><JobCancelButton job={job} onCancel={cancel}/>{job.error&&<p role="alert" className="notice error">{job.error}</p>}
   </>:<div className="image-empty"><div className="image-empty-frame" aria-hidden="true"><span>✦</span></div><span className="eyebrow">UN ESPACIO PARA TU IMAGINACIÓN</span><h2>{edit?<>Una imagen.<br/>Nuevas posibilidades.</>:<>Todo empieza<br/>con una idea.</>}</h2><p>{edit?<>Añade tu imagen y describe el cambio.<br/>{qwen?'Qwen':'MOSS'} crea una nueva versión.</>:<>Escribe lo que quieres ver.<br/>{qwen?'Qwen':'MOSS'} se encarga de darle forma.</>}</p></div>}
  </section>
 </div>}
 {historyView&&<section className="image-history" aria-label="Historial de imágenes">
  <div className="image-history-heading"><div><h2>Historial de imágenes</h2><p>Creaciones y ediciones de tu espacio privado.</p></div></div>
  {networkError&&<p role="status" className="notice error">{networkError}</p>}
  {!jobs.length&&!networkError&&<p className="image-hint">Las imágenes que generes se guardarán aquí.</p>}
  <div className="image-gallery">{jobs.map(item=><button type="button" key={item.id} aria-label={'Abrir imagen: '+item.parameters.prompt} aria-pressed={selected===item.id} className={'image-card'+(selected===item.id?' selected':'')} onClick={()=>{setSelected(item.id);onViewChange?.('create');}}>
   {item.result_available&&item.status==='completed'?<img loading="lazy" decoding="async" src={'/api/images/'+item.id+'/thumbnail'} alt=""/>:<span className="image-card-state">{LABELS[item.status]||item.status}</span>}
   <span className="image-card-caption"><strong>{item.parameters.prompt}</strong><small>{isQwenImage(item.parameters)&&'Qwen Image 2.1 · '}{isImageUpscale(item.parameters)?'Upscale · ':item.parameters.mode==='edit'?item.parameters.edit_profile==='mystic_base8'?'Edición BASE + Mystic · ':'Edición · ':''}{new Date(item.created_at).toLocaleDateString('es-MX')} · {item.parameters.resolution.replace('x',' × ')}</small></span>
  </button>)}</div>
  {next&&<button type="button" className="secondary" disabled={more} onClick={loadMore}>{more?'Cargando…':'Ver más imágenes'}</button>}
 </section>}
 {!historyView&&full&&result&&<div className="image-lightbox" role="dialog" aria-modal="true" aria-label="Imagen generada ampliada" onClick={()=>setFull(false)}><button type="button" className="secondary" autoFocus onClick={()=>setFull(false)}>Cerrar ×</button><img src={result} alt={job.parameters.prompt} onClick={e=>e.stopPropagation()}/><a href={result+'?download=true'} className="primary" onClick={e=>e.stopPropagation()}>Descargar PNG ↓</a></div>}
 </div>;
}
