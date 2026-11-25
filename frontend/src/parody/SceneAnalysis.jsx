import React,{useEffect,useMemo,useState} from 'react';
import {createClient} from './api.js';
import {analysisBusy as active} from './analysisState.js';

export default function SceneAnalysis({workspace,project,dirty,enabled,characters=[],onAssign,onTraits,actingRef,actorId}){
 const api=useMemo(()=>createClient(workspace.id),[workspace.id]);
 const [record,setRecord]=useState(null),[history,setHistory]=useState([]),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const [characterId,setCharacterId]=useState(actorId||'');
 const base='/projects/'+project.id+'/analyses';
 useEffect(()=>{let live=true;setRecord(null);setHistory([]);setError('');if(enabled)api.get(base).then(r=>{if(live){const rows=r.analyses.filter(v=>v.source.asset_id===(actingRef?.asset_id||project.source?.asset_id));setHistory(rows);setRecord(rows[0]||null);}}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[api,base,enabled,actingRef?.asset_id,project.source?.asset_id]);
 useEffect(()=>{if(!active(record))return;let live=true;const timer=setInterval(()=>{api.get(base+'/'+record.id).then(r=>{if(live){setRecord(r);setError('');}}).catch(e=>{if(live)setError(e.message);});},2000);return()=>{live=false;clearInterval(timer);};},[api,base,record?.id,record?.status,record?.vram_released]);
 async function start(){setBusy(true);setError('');try{const r=await api.mutate(base,'POST',{expected_revision:project.revision,...(actingRef?{acting_reference_id:actingRef.id}:{})});setRecord(r);setHistory(rows=>[r,...rows.filter(v=>v.id!==r.id)]);}catch(e){setError(e.message);}finally{setBusy(false);}}
 async function choose(id){setBusy(true);setError('');try{setRecord(await api.get(base+'/'+id));}catch(e){setError(e.message);}finally{setBusy(false);}}
 async function cancel(){setBusy(true);try{setRecord(await api.mutate(base+'/'+record.id+'/cancel','POST',{}));}catch(e){setError(e.message);}finally{setBusy(false);}}
 return <section className="pm-analysis" aria-label="Análisis visual de la escena"><h3>{actingRef?'Actuación de referencia':'Análisis de la escena'}</h3>
  <p className="pm-help">Qwen observa el encuadre completo a lo largo del vídeo. Los cortes, personajes y gestos son propuestas para revisar. No transcribe audio ni identifica hablantes.</p>
  <button disabled={!enabled||busy||active(record)||dirty||project.archived||!project.source} onClick={start}>{actingRef?'Analizar clip con Qwen':'Analizar escena'}</button>
  {dirty&&<p>Guarda el borrador antes de analizar.</p>}
  {!enabled&&<p className="pm-help">Análisis pendiente de activación.</p>}
  {error&&<p role="alert" className="pm-error">{error}</p>}
  {history.length>1&&<label>Análisis guardados<select value={record?.id||''} disabled={busy||active(record)} onChange={e=>choose(e.target.value)}>{history.map((r,i)=><option key={r.id} value={r.id}>Análisis {history.length-i} · revisión {r.project_revision} · {new Date(r.created_at).toLocaleString()}</option>)}</select></label>}
  {record&&<><p role="status">{record.stage}{record.progress!=null?' · '+record.progress+'%':''}</p>
   {active(record)&&<button disabled={busy||record.cancel_requested} onClick={cancel}>Cancelar análisis</button>}
   {record.project_revision!==project.revision&&<p className="pm-error">Este análisis corresponde a la revisión {record.project_revision}. Revisa sus diferencias con el borrador actual.</p>}
   {record.error&&<p role="alert" className="pm-error">{record.error}</p>}
   <p className="pm-help">Revisión humana requerida. Las etiquetas de personajes no confirman identidad entre planos. {record.vram_released?'Recursos liberados.':''}</p>
   {!actorId&&characters.length>0&&<label>Personaje de reemplazo para las propuestas<select value={characterId} onChange={e=>setCharacterId(e.target.value)}><option value="">Seleccionar ficha</option>{characters.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</select></label>}
   {record.plan&&<p>{record.plan.cuts_ms.length} cortes candidatos · {record.windows.length}/{record.plan.windows.length} intervalos observados · Audio: {record.plan.audio_present?'presente, sin transcribir':'no presente'}.</p>}
   {(record.windows||[]).map(w=><article className="pm-observation" key={w.index}>
    <h4>Plano {w.shot+1} · {(w.start_ms/1000).toFixed(2)}–{(w.end_ms/1000).toFixed(2)} s</h4>
    <div className="pm-evidence">{record.plan?.windows[w.index]?.samples.map(s=><figure key={s.frame_index}><img loading="lazy" src={'/api/parody/v1'+base+'/'+record.id+'/frames/'+s.frame_index} alt={'Evidencia completa a '+s.time_ms/1000+' segundos'}/><figcaption>{(s.time_ms/1000).toFixed(3)} s</figcaption></figure>)}</div>
    <p>{w.observation.summary}</p><p><strong>Escenario: </strong>{w.observation.environment}</p><p><strong>Cámara: </strong>{w.observation.camera}</p>
    <ul>{w.observation.actors.map((a,i)=><li key={i}><strong>{a.label}: </strong>{a.appearance} · {a.action} {a.expression}<small>Confianza declarada por Qwen: {a.confidence}. Evidencia: {a.evidence_times_ms.map(t=>(t/1000).toFixed(3)+' s').join(', ')}.</small>{characters.length>0&&<><button type="button" disabled={!characterId||!onAssign||busy||active(record)} onClick={()=>onAssign(record,w,i,characterId)}>Proponer reemplazo de {a.label}</button> <button type="button" disabled={!characterId||!onTraits||busy||active(record)} onClick={()=>onTraits(record,w,i,characterId)}>Proponer rasgos de {a.label}</button></>}</li>)}</ul>
    <p><strong>Conservar: </strong>{w.observation.preserve.join(' · ')}</p>
    {w.observation.uncertainties.length>0&&<p><strong>Por confirmar: </strong>{w.observation.uncertainties.join(' · ')}</p>}
   </article>)}
  </>}
 </section>;
}
