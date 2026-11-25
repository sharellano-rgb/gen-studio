import {StudioSlot} from './StudioWorkspace.jsx';
import React,{useEffect,useRef,useState,useId} from 'react';
import StudioStep from './StudioStep.jsx';
import JobCancelButton from './JobCancelButton.jsx';
import JobElapsed from './JobElapsed.jsx';
import {workspaceLocalStorage as sharedStorage,activeWorkspaceId} from './workspaceStorage.js';
import {PENDING_KEY,pendingRequest,rememberRequest,acknowledge} from './pikaState.js';
import './pikaVideo.css';

function engineStorage(engine){
 const key=k=>engine==='h3_i2va'?k.replace('gen-pika','gen-h3-i2va'):k;
 return {getItem:k=>sharedStorage.getItem(key(k)),setItem:(k,v)=>sharedStorage.setItem(key(k),v),removeItem:k=>sharedStorage.removeItem(key(k))};
}
const defaults={reference_id:'',prompt:'',format:'landscape',seed:42};
const terminal=new Set(['completed','failed','cancelled','submission_unknown']);
async function api(path,options){
 const r=await fetch('/api'+path,options);
 if(r.headers.get('X-Gen-Studio-Workspace')&&r.headers.get('X-Gen-Studio-Workspace')!==activeWorkspaceId()){window.location.reload();throw Error('Actualizando tu espacio.');}
 const data=await r.json();if(!r.ok){const e=Error(typeof data.detail==='string'?data.detail:'No se pudo completar la solicitud de Pika.');e.status=r.status;throw e;}return data;
}
function initial(job,storage,engine){
 const saved=(()=>{try{return JSON.parse(storage.getItem('gen-pika-draft'))||{};}catch{return {};}})();
 let outstanding;try{outstanding=pendingRequest(storage);}catch{}
 const p=outstanding||job?.parameters||saved;
 const values={...defaults,profile:engine==='h3_i2va'?'h3-i2va-480p-5s-v1':'pika-i2va-480p-5s-v1'};
 return Object.fromEntries(Object.keys(values).map(k=>[k,p[k]??values[k]]));
}
export default function PikaVideo({engine="pika_h3_flex",assets,job,jobs=[],available,busy,uploading,onUpload,onCreated,onCancel,onOpen,onError}){
 const storage=engineStorage(engine),name=engine==='h3_i2va'?'H3 directo':'Pika H3 Flex';
 const [p,setP]=useState(()=>initial(job,storage,engine)),[cap,setCap]=useState(null),[sending,setSending]=useState(false),[error,setError]=useState('');
 const [compare,setCompare]=useState('');const pending=useRef(null),live=useRef(true),fileId=useId();
 let recoveryError='';try{pending.current=pendingRequest(storage);}catch(e){recoveryError=e.message;}
 const active=Boolean(job&&!terminal.has(job.status));
 const disabled=active||sending||Boolean(pending.current);
 const image=assets.find(a=>a.id===p.reference_id);
 const previous=jobs.filter(j=>j.id!==job?.id&&j.parameters.engine===engine&&j.status==='completed'&&j.result_available);
 useEffect(()=>{live.current=true;api(engine==='h3_i2va'?'/engines/h3-i2va/capabilities':'/engines/pika-h3-flex/capabilities').then(v=>{if(live.current)setCap(v);}).catch(e=>{if(live.current)setError(e.message);});return()=>{live.current=false;};},[]);
 useEffect(()=>{storage.setItem('gen-pika-draft',JSON.stringify(p));},[p]);
 const selectedProfile=cap?.profiles?.find(v=>v.id===p.profile);
 const update=(key,value)=>setP(current=>({...current,[key]:value}));
 async function upload(e){const input=e.currentTarget,file=input.files?.[0];if(!file)return;try{const asset=await onUpload(file,'image',{select:false});if(live.current)update('reference_id',asset.id);}catch(e){onError(e.message);}finally{input.value='';}}
 async function submit(e){e.preventDefault();if(sending)return;setSending(true);setError('');
  try{pending.current=rememberRequest(storage,p,crypto.randomUUID());const result=await api('/pika-h3-flex/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(pending.current)});acknowledge(storage,pending.current,result);pending.current=null;onCreated(result);}
  catch(e){if(live.current){setError(e.message);if(e.status&&e.status<500){storage.removeItem(PENDING_KEY);pending.current=null;}}}
  finally{if(live.current)setSending(false);}
 }
 return <div className="pika-studio">
  <form className="panel studio-composer pika-composer" data-studio-pane="edit" onSubmit={submit}>
   <div className="composer-heading"><div><h2>De imagen a vídeo</h2><p>{name} · Experimental</p></div><span className="local-pill">LOCAL</span></div>
   <StudioStep number={1} title="Imagen inicial" summary={image?.name||'El punto de partida de tu vídeo'} defaultOpen>
    <label htmlFor={fileId} className="secondary pika-upload">{uploading?'Subiendo imagen…':'Subir una imagen'}</label><input id={fileId} className="pika-file" type="file" accept="image/png,image/jpeg,image/webp" disabled={disabled||uploading} onChange={upload}/>
    <label>Elegir de mi biblioteca<select value={p.reference_id} disabled={disabled} onChange={e=>update('reference_id',e.target.value)}><option value="">Selecciona una imagen</option>{assets.filter(a=>a.kind==='image').map(a=><option key={a.id} value={a.id}>{a.name||a.id.slice(0,8)}</option>)}</select></label>
    {p.reference_id&&<img className="pika-reference" src={'/api/assets/'+p.reference_id} alt="Imagen inicial seleccionada"/>}
    <p className="composer-hint">Se conserva la proporción de la imagen y se completa el encuadre con margen blanco.</p>
   </StudioStep>
   <StudioStep number={2} title="Movimiento e indicaciones" summary="Qwen preparará el prompt de esta toma" defaultOpen>
    <label>¿Qué quieres que ocurra?<textarea rows={5} maxLength={3000} value={p.prompt} disabled={disabled} onChange={e=>update('prompt',e.target.value)} placeholder="Por ejemplo: mira a cámara, levanta la mano derecha y saluda. La cámara permanece fija."/></label>
    <p className="composer-hint">Qwen analiza la imagen, prepara las indicaciones y te pregunta en el chat de la toma si necesita una aclaración.</p>
   </StudioStep>
   <StudioStep number={3} title="Formato" summary={(selectedProfile?.label||'480p')+' · 5.17 segundos · Audio generado'} defaultOpen>
    <label>Calidad<select value={p.profile} disabled={disabled} onChange={e=>update('profile',e.target.value)}>{(cap?.profiles||[{id:p.profile,label:'480p'}]).map(v=><option key={v.id} value={v.id}>{v.label}</option>)}</select></label>
    <p className="composer-hint">{selectedProfile?.native_note}</p>
    <div className="parameter-grid"><label>Encuadre<select value={p.format} disabled={disabled} onChange={e=>update('format',e.target.value)}>{(selectedProfile?.formats||cap?.formats||[{id:'landscape',width:832,height:480}]).map(f=><option key={f.id} value={f.id}>{f.id==='portrait'?'Vertical':'Horizontal'} · {f.width} × {f.height}</option>)}</select></label><label>Semilla<input type="number" min="0" max="9007199254740991" step="1" value={p.seed} disabled={disabled} onChange={e=>update('seed',Number(e.target.value))}/></label></div>
   </StudioStep>
   {!cap?.ok&&<p role="status">{cap?.errors?.join(' · ')||'Comprobando '+name+'…'}</p>}
   {(error||recoveryError)&&<p role="alert">{error||recoveryError}</p>}
   <StudioSlot name="actions"><button className="primary" type="submit" disabled={sending||Boolean(recoveryError)||(!pending.current&&(active||busy||uploading||!available||!cap?.ok||!p.reference_id||p.prompt.trim().length<3||!Number.isSafeInteger(p.seed)||p.seed<0))}>{sending?'Enviando…':pending.current?'Recuperar envío':job?'Generar otra toma':'Generar vídeo con '+name}</button></StudioSlot>
  </form>
  <StudioSlot name="result"><section className="panel pika-result" data-studio-pane="result" aria-label={"Resultado "+name}>
   <div className="panel-title"><h2>{job?'Tu toma':'Vista del resultado'}</h2>{job&&<JobElapsed job={job}/>}</div>
   {job?<><p role="status">{job.stage}</p>{job.progress?.max>0&&<progress max={job.progress.max} value={job.progress.value}/>}<JobCancelButton job={job} onCancel={onCancel}/>{job.error&&<p role="alert">{job.error}</p>}
    {job.result_available&&<><div className={'pika-videos'+(compare?' is-comparing':'')}><figure><video className="result-player-video" controls playsInline preload="metadata" src={'/api/jobs/'+job.id+'/result'}/><figcaption>Esta toma</figcaption></figure>{compare&&<figure><video controls playsInline preload="metadata" src={'/api/jobs/'+compare+'/result'}/><figcaption>Toma para comparar</figcaption></figure>}</div><a className="download" href={'/api/jobs/'+job.id+'/result?download=true'}>Descargar vídeo</a>{previous.length>0&&<label>Comparar con otra generación<select value={compare} onChange={e=>setCompare(e.target.value)}><option value="">Sin comparación</option>{previous.map(j=><option key={j.id} value={j.id}>{new Date(j.created_at).toLocaleString('es-MX')} · {j.id.slice(0,8)}</option>)}</select></label>}<p>5.17 s · {job.scene_resolution||job.parameters.resolution} · 24 fps</p></>}
    <details><summary>Datos de la generación</summary><p>Semilla: {job.parameters.seed} · {name} · {job.parameters.steps} pasos</p>{job.pika_effective_prompt&&<p className="qwen-notes">{job.pika_effective_prompt}</p>}<small>Versión {job.id.slice(0,8)}</small></details>
   </>:<div className="pika-empty"><strong>Tu imagen cobrará movimiento aquí</strong><p>Elige una imagen y describe una acción breve para comenzar.</p></div>}
   {previous.length>0&&<div className="pika-recent"><h3>Tomas recientes de {name}</h3>{previous.slice(0,5).map(j=><button className="secondary" key={j.id} onClick={()=>onOpen(j.id)}>{new Date(j.created_at).toLocaleString('es-MX')} · {j.id.slice(0,8)}</button>)}</div>}
  </section></StudioSlot>
 </div>;
}
