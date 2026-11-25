import React,{useEffect,useRef,useState} from 'react';
import {compatibleSceneTakes} from './scene-preparation.js';

const active=r=>['running','cancelling'].includes(r?.status);
export default function ScenePreparation({api,project,appearance,disabled,onReady}){
 const [takes,setTakes]=useState([]),[take,setTake]=useState(''),[record,setRecord]=useState(null),[index,setIndex]=useState(null),[reviewed,setReviewed]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const [refresh,setRefresh]=useState(0);
 const base='/projects/'+project.id+'/scene-preparations';
 const mounted=useRef(true);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
 function selectRecord(r){setRecord(r);setIndex(r.accepted?.frame_index??r.selection?.frame_index??null);setReviewed(false);}
 useEffect(()=>{let live=true;
  Promise.all([api.get('/projects/'+project.id+'/render-plans'),api.get(base)]).then(([renders,stored])=>{
   if(!live)return;const eligible=compatibleSceneTakes(renders.plans,appearance);setTakes(eligible);setTake(eligible[0]?.id||'');
   const latest=stored.preparations.find(r=>r.binding?.appearance.id===appearance);if(latest)selectRecord(latest);
  }).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};
 },[api,base,project.revision,appearance,refresh]);
 useEffect(()=>{if(!active(record))return;let live=true;let timer;
  async function poll(){try{const value=await api.get(base+'/'+record.id);if(!live)return;
   setRecord(value);if(!active(value)){setIndex(value.selection?.frame_index??null);return;}
  }catch(e){if(live)setError(e.message);}if(live)timer=setTimeout(poll,2000);}
  timer=setTimeout(poll,1500);return()=>{live=false;clearTimeout(timer);};
 },[api,base,record?.id,active(record)]);
 const sample=record?.samples?.find(s=>s.frame_index===index);
 return <section aria-label="Preparar escena desde H3"><h4>Preparar imagen desde una toma H3</h4>
 <p>Qwen compara cinco fotogramas completos con la ficha del personaje. Revisa el cuerpo, vestuario y escenario antes de usar uno. No se vuelve a generar vídeo.</p>
 {error&&<p role="alert" className="pm-error">{error}</p>}
 <label>Toma del personaje y plano<select disabled={disabled||busy||active(record)} value={take} onChange={e=>setTake(e.target.value)}>
 <option value="">Seleccionar toma H3 terminada</option>{takes.map(r=><option key={r.id} value={r.id}>{r.plan.shot.label} · {r.created_at} · {r.id.slice(0,8)}</option>)}</select></label>
 <button type="button" disabled={disabled||busy||active(record)} onClick={()=>{setError('');setRefresh(n=>n+1);}}>Actualizar tomas H3</button>
 {!takes.length&&<p>Primero prepara una toma visual con H3 en este proyecto y asignación. También puedes seguir usando una imagen ya preparada de tu biblioteca.</p>}
 <button type="button" disabled={disabled||busy||active(record)||!take} onClick={async()=>{setBusy(true);setError('');try{
  selectRecord(await api.mutate(base,'POST',{expected_revision:project.revision,render_plan_id:take,appearance_id:appearance}));
 }catch(e){setError(e.message+' Si falta confirmar el envío, pulsa de nuevo con la misma toma.');}finally{setBusy(false);}}}>Qwen: proponer fotograma completo</button>
 {record&&<><p role="status">{record.stage}</p>{record.error&&<p role="alert">{record.error}</p>}
 {active(record)&&<button type="button" onClick={async()=>{try{setRecord(await api.mutate(base+'/'+record.id+'/cancel','POST',{}));}catch(e){setError(e.message);}}}>Cancelar preparación</button>}
 {record.selection&&<><p>{record.selection.reason}</p>{!record.selection.usable&&<p>Qwen no encontró una opción adecuada. Puedes revisar las muestras o preparar otra toma.</p>}{record.selection.uncertainties.map((u,i)=><p key={i}>Por revisar: {u}</p>)}</>}
 {!!record.samples?.length&&<label>Comparar fotogramas<select disabled={busy||active(record)||!!record.accepted} value={index??''} onChange={e=>{setIndex(e.target.value===''?null:Number(e.target.value));setReviewed(false);}}>
 <option value="">Seleccionar muestra</option>{record.samples.map(s=><option key={s.frame_index} value={s.frame_index}>{(s.time_ms/1000).toFixed(2)} s · fotograma {s.frame_index}{record.selection?.frame_index===s.frame_index?' · propuesta de Qwen':''}</option>)}</select></label>}
 {sample&&<img className="pm-preview" src={'/api/parody/v1'+base+'/'+record.id+'/frames/'+index} alt="Fotograma H3 completo pendiente de revisión"/>}
 {record.status==='review_pending'&&<><label><input type="checkbox" checked={reviewed} disabled={!sample||busy||!record.vram_released} onChange={e=>setReviewed(e.target.checked)}/>Revisé el personaje completo visible, ropa, manos, otras personas y escenario.</label>
 <button type="button" disabled={disabled||busy||!sample||!reviewed||!record.vram_released} onClick={async()=>{setBusy(true);setError('');try{
  const value=await api.mutate(base+'/'+record.id+'/accept','POST',{expected_revision:project.revision,frame_index:index,frame_sha256:sample.sha256,reviewed:true});
  if(mounted.current){setRecord(value);onReady(value.accepted.asset);}
 }catch(e){setError(e.message);}finally{setBusy(false);}}}>Usar este fotograma en Ref2VA</button></>}
 {record.accepted&&<button type="button" disabled={disabled||busy} onClick={()=>onReady(record.accepted.asset)}>Seleccionar escena guardada</button>}
 </>}
 </section>;
}
