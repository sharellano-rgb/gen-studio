import React,{useEffect,useMemo,useRef,useState} from 'react';
import {createClient} from './api.js';
import {previewBusy} from './script.js';

export default function VoicePreviews({workspace,project,dirty,enabled,characterId}){
 const api=useMemo(()=>createClient(workspace.id),[workspace.id]);
 const [records,setRecords]=useState([]),[provider,setProvider]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const lock=useRef(false),base='/projects/'+project.id+'/voice-previews';
 const active=records.find(previewBusy);
 useEffect(()=>{let live=true;if(enabled){api.get(base).then(r=>{if(live)setRecords(r.previews);}).catch(e=>{if(live)setError(e.message);});api.get('/voice-capabilities').then(r=>{if(live)setProvider(r);}).catch(e=>{if(live)setError(e.message);});}return()=>{live=false;};},[api,base,enabled,project.revision]);
 useEffect(()=>{if(!active)return;let live=true;const timer=setInterval(()=>api.get(base+'/'+active.id).then(r=>{if(live)setRecords(rows=>rows.map(v=>v.id===r.id?r:v));}).catch(e=>{if(live)setError(e.message);}),1500);return()=>{live=false;clearInterval(timer);};},[api,base,active?.id,active?.status,active?.resources_released]);
 async function run(fn){if(lock.current)return;lock.current=true;setBusy(true);setError('');try{await fn();}catch(e){setError(e.message);}finally{lock.current=false;setBusy(false);}}
 if(!enabled)return null;
 return <section className="pm-voices" aria-label="Vistas previas de voz"><h3>Escuchar frases</h3><p className="pm-phase">PARODIA SINTÉTICA · vistas previas de audio</p><p className="pm-help">{provider?.reason||'Comprobando entorno local de voz…'} Las voces conservan su duración natural; revisa si caben en el intervalo. La sincronización labial y mezcla final pertenecen a otra fase.</p>
 {error&&<p className="pm-error" role="alert">{error}</p>}{dirty&&<p>Guarda el borrador antes de generar o regenerar una frase.</p>}
 {(project.script?.lines||[]).filter(l=>!characterId||l.character_id===characterId).map((line,i)=>{const history=records.filter(r=>r.line_id===line.id);return <article className="pm-trait" key={line.id}><h4>Frase {i+1} · {line.kind==='speech'?'Voz generada':line.kind==='original'?'Audio original':'Silencio'}</h4><p>{line.text||'Intervalo sin diálogo escrito'}</p><button disabled={busy||!!active||dirty||project.archived||(line.kind==='speech'&&!provider?.available)} onClick={()=>run(async()=>{const r=await api.mutate(base,'POST',{expected_revision:project.revision,line_id:line.id});setRecords(rows=>[r,...rows.filter(v=>v.id!==r.id)]);})}>{history.length?'Regenerar solo esta frase':'Preparar vista previa'}</button>
 {history.map(r=><details key={r.id} open={history[0].id===r.id}><summary>Versión de audio · {new Date(r.created_at).toLocaleString()}</summary><p role="status">{r.stage}</p>{r.stale&&<p className="pm-error">Audio de una versión anterior de esta frase o sus referencias. Puedes escucharlo, pero requiere regeneración para el guion actual.</p>}{r.error&&<p role="alert" className="pm-error">{r.error}</p>}{previewBusy(r)&&<button disabled={busy||r.cancel_requested} onClick={()=>run(async()=>{const result=await api.mutate(base+'/'+r.id+'/cancel','POST',{});setRecords(rows=>rows.map(v=>v.id===r.id?result:v));})}>Cancelar voz</button>}{r.artifact&&r.status==='review_pending'&&<><audio controls preload="metadata" src={'/api/parody/v1'+base+'/'+r.id+'/audio'} aria-label={'Vista previa frase '+(i+1)}/><p>{(r.artifact.duration_ms/1000).toFixed(2)} s · {r.artifact.fits_interval?'Cabe en el intervalo guardado':'Supera el intervalo: ajusta texto o tiempos'}. Escucha y revisión humana pendientes.</p><a href={'/api/parody/v1'+base+'/'+r.id+'/audio'} download={'parodia-sintetica-frase-'+(i+1)+'.wav'}>Descargar pista WAV</a></>}</details>)}
 </article>;})}
 </section>;
}
