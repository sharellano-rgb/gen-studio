import React,{useEffect,useState} from 'react';
const active=r=>['running','cancelling','queued','preparing','submitting','downloading','refining'].includes(r?.status);
export default function SceneImage({api,project,appearance,disabled,onReady}){
 const [record,setRecord]=useState(null),[reference,setReference]=useState(''),[directions,setDirections]=useState(''),[prompt,setPrompt]=useState(''),[candidate,setCandidate]=useState(0),[reviewed,setReviewed]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const base='/projects/'+project.id+'/scene-images';
 const assignment=project.cast.appearances.find(a=>a.id===appearance), character=project.cast.characters.find(c=>c.id===assignment?.character_id);
 const refs=(character?.references||[]).filter(r=>['front','profile','body','wardrobe','accessory'].includes(r.role));
 function select(r){setRecord(r);setPrompt(r.generation_review?.prompt??r.direction?.edit_prompt??'');setCandidate(r.generation_review?.candidate??r.direction?.candidate??0);setReviewed(false);}
 useEffect(()=>{let live=true;setReference(refs[0]?.id||'');api.get(base).then(data=>{if(live){const latest=data.images.find(r=>r.binding.appearance.id===appearance);if(latest)select(latest);}}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[api,base,appearance]);
 useEffect(()=>{if(!active(record))return;let live=true,timer;
  async function poll(){try{const value=await api.get(base+'/'+record.id);if(!live)return;setRecord(value);if(!active(value)){setPrompt(value.generation_review?.prompt??value.direction?.edit_prompt??'');setCandidate(value.generation_review?.candidate??value.direction?.candidate??0);setReviewed(false);return;}}catch(e){if(live)setError(e.message);}if(live)timer=setTimeout(poll,2000);}
  timer=setTimeout(poll,1500);return()=>{live=false;clearTimeout(timer);};
 },[api,base,record?.id,active(record)]);
 async function act(fn){setBusy(true);setError('');try{await fn();}catch(e){setError(e.message+' Si se perdió la respuesta, reintenta la misma operación.');}finally{setBusy(false);}}
 const sample=record?.samples?.[candidate];
 const editable=record?.status==='plan_ready'&&!record.image_request;
 return <section className="pm-render" aria-label="Crear imagen de escena"><h4>1. Crear imagen desde el original y la ficha</h4>
 <p>Qwen compara tres fotogramas completos y propone cómo recrear al personaje. Puedes cambiar su pose y expresión. La imagen se revisa antes de generar vídeo; no hace falta una toma H3 previa.</p>
 {error&&<p role="alert" className="pm-error">{error}</p>}
 <label>Referencia de identidad y vestuario<select disabled={disabled||busy||active(record)} value={reference} onChange={e=>setReference(e.target.value)}><option value="">Seleccionar imagen de la ficha</option>{refs.map(r=><option key={r.id} value={r.id}>{r.name}</option>)}</select></label>
 <label>Cómo quieres recrear la escena<textarea disabled={disabled||busy||active(record)} maxLength={1500} value={directions} onChange={e=>setDirections(e.target.value)} placeholder="Personaje completo, vestuario, expresión y escenario deseados…"/></label>
 <button type="button" disabled={disabled||busy||active(record)||!reference} onClick={()=>act(async()=>select(await api.mutate(base,'POST',{expected_revision:project.revision,appearance_id:appearance,reference_id:reference,directions})))}>Qwen: preparar nueva imagen</button>
 {record&&<><p role="status">{record.stage}</p>{record.error&&<p role="alert">{record.error}</p>}
 {active(record)&&<button type="button" disabled={busy} onClick={()=>act(async()=>setRecord(await api.mutate(base+'/'+record.id+'/cancel','POST',{})))}>Cancelar imagen</button>}
 {record.direction&&<details><summary>Observaciones de Qwen</summary><p>{record.direction.source_observation}</p><p>{record.direction.reference_identity_and_clothing}</p>{record.direction.uncertainties.map((u,i)=><p key={i}>Por revisar: {u}</p>)}</details>}
 {record.status==='needs_review'&&<p>Qwen no pudo confirmar al personaje. Revisa la asignación o el plano y prepara otra versión.</p>}
 {(record.status==='plan_ready'||record.image_request)&&<details open={editable}><summary>2. Revisar fotograma e indicaciones</summary>
 <label>Fotograma original<select disabled={!editable||disabled||busy} value={candidate} onChange={e=>{setCandidate(Number(e.target.value));setReviewed(false);}}>{record.samples.map((s,i)=><option key={s.frame_index} value={i}>{(s.time_ms/1000).toFixed(2)} s{i===record.direction.candidate?' · propuesta de Qwen':''}</option>)}</select></label>
 {sample&&<img className="pm-preview" src={'/api/parody/v1'+base+'/'+record.id+'/frames/'+sample.frame_index} alt="Escena original completa para recrear"/>}
 <label>Prompt de imagen · editable<textarea disabled={!editable||disabled||busy} maxLength={4000} value={prompt} onChange={e=>{setPrompt(e.target.value);setReviewed(false);}}/></label>
 {record.status==='plan_ready'&&<><label><input type="checkbox" disabled={disabled||busy} checked={reviewed} onChange={e=>setReviewed(e.target.checked)}/>Revisé el personaje, el fotograma y las indicaciones.</label>
 <button type="button" disabled={disabled||busy||!reviewed||prompt.trim().length<30||!record.vram_released} onClick={()=>act(async()=>setRecord(await api.mutate(base+'/'+record.id+'/generate','POST',{expected_revision:project.revision,candidate,prompt,reviewed:true})))}>Generar imagen · Qwen Image · 768p</button></>}
 </details>}
 {['review_pending','completed'].includes(record.status)&&<><h4>3. Revisar la imagen recreada</h4><img className="pm-preview" src={'/api/parody/v1'+base+'/'+record.id+'/image'} alt="Imagen recreada pendiente de revisión"/>
 {!record.accepted&&<><label><input type="checkbox" checked={reviewed} onChange={e=>setReviewed(e.target.checked)}/>Revisé identidad, cuerpo y vestuario, otras personas y escenario.</label><button type="button" disabled={disabled||busy||!reviewed} onClick={()=>act(async()=>{const r=await api.mutate(base+'/'+record.id+'/accept','POST',{expected_revision:project.revision,sha256:record.result_sha256,reviewed:true});setRecord(r);onReady(r.accepted.asset);})}>Usar imagen revisada en Ref2VA</button></>}
 {record.accepted&&<button type="button" disabled={disabled||busy} onClick={()=>onReady(record.accepted.asset)}>Seleccionar imagen guardada</button>}
 </>}
 </>}
 </section>;
}
