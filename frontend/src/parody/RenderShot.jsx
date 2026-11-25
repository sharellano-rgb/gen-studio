import React,{useEffect,useMemo,useRef,useState} from 'react';
import {createClient} from './api.js';
import {renderBusy,renderProgress,isSpeechPlan,isNativePlan} from './render.js';
import Ref2vaComposer from './Ref2vaComposer.jsx';

export default function RenderShot({workspace,project,dirty,enabled}){
 const api=useMemo(()=>createClient(workspace.id),[workspace.id]),base='/projects/'+project.id+'/render-plans';
 const [plans,setPlans]=useState([]),[error,setError]=useState(''),[busy,setBusy]=useState(false),[approved,setApproved]=useState('');
 const lock=useRef(false),active=plans.find(renderBusy);
 useEffect(()=>{let live=true;if(enabled)api.get(base).then(r=>{if(live)setPlans(r.plans);}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[api,base,enabled,project.revision]);
 useEffect(()=>{if(!active)return;let live=true;const timer=setInterval(()=>api.get(base+'/'+active.id).then(r=>{if(live)setPlans(rows=>rows.map(p=>p.id===r.id?r:p));}).catch(e=>{if(live)setError(e.message);}),2000);return()=>{live=false;clearInterval(timer);};},[api,base,active?.id,active?.status]);
 async function run(fn){if(lock.current)return;lock.current=true;setBusy(true);setError('');try{await fn();}catch(e){setError(e.message);}finally{lock.current=false;setBusy(false);}}
 function card(p){return <article className="pm-trait" key={p.id}><h4>{p.plan.shot.label} · {p.plan.resolution} · {(p.plan.timing.frames/24).toFixed(3)} s</h4><p role="status">{p.stage}</p><p>{isNativePlan(p)?"Personaje de la recreación":"Sujeto de la prueba anterior"}: {p.plan.appearance.target_label}. Referencias: {p.plan.references.length}. Audio: {isNativePlan(p)?'generado junto al vídeo · Ref2VA':isSpeechPlan(p)?'voz preparada como guía de H3':'original'}.</p>{isSpeechPlan(p)&&<p>{p.plan.voice.line.text} · inicio {(p.plan.voice.offset_samples/48000).toFixed(3)} s</p>}
 <details><summary>Ver fuente y referencias de este plan</summary><video className="pm-preview" controls playsInline preload="metadata" aria-label="Escena original de referencia" src={'/api/assets/'+p.plan.source.asset_id+'#t='+(p.plan.source_timing||p.plan.timing).start_ms/1000+','+(p.plan.source_timing||p.plan.timing).end_ms/1000}/><div className="pm-render-references">{isNativePlan(p)&&<figure><img src={'/api/assets/'+p.plan.scene.asset_id} alt="Escena completa que guía Ref2VA"/><figcaption>Picture 1 · escena preparada para generación</figcaption></figure>}{p.plan.references.map((r,i)=><figure key={r.id}><img src={'/api/assets/'+r.asset_id} alt={'Referencia '+(i+1)+' de '+p.plan.appearance.target_label}/><figcaption>{isNativePlan(p)?'Ficha para revisión de identidad':'Picture '+(i+1)} · {r.name}</figcaption></figure>)}</div></details>
 <details><summary>Revisar indicaciones y límites</summary><p>{isSpeechPlan(p)?p.plan.request.directions:p.plan.native_payload.prompt}</p>{p.direction&&<><p>Qwen: {p.direction.scene}</p><p>{p.direction.acting}</p>{p.direction.uncertainties.map((u,i)=><p key={i}>{u}</p>)}</>}{p.plan.warnings.map(w=><p key={w} className="pm-help">{w}</p>)}</details>
 {p.stale&&<p className="pm-help">Este plan pertenece a una revisión anterior. Su resultado se conserva.</p>}
 {isNativePlan(p)&&!p.native_job_id&&['planned','interrupted','submission_unknown'].includes(p.status)&&<><label><input type="checkbox" checked={approved===p.id} disabled={busy||dirty||p.stale||project.archived||!!active} onChange={e=>setApproved(e.target.checked?p.id:'')}/>Revisé la escena, el personaje, el diálogo y la voz; autorizo esta recreación con actuación nueva.</label><button disabled={busy||dirty||p.stale||project.archived||!!active||approved!==p.id} onClick={()=>run(async()=>{const r=await api.mutate(base+'/'+p.id+'/execute','POST',{plan_hash:p.plan_hash,approved:true});setPlans(rows=>rows.map(v=>v.id===r.id?r:v));})}>{['submission_unknown','interrupted'].includes(p.status)?'Recuperar mismo envío':'Generar este plano'}</button></>}
 {p.native_job_id&&<><a href={'/?take='+p.native_job_id} target="_blank" rel="noopener">Abrir toma y conversación con Qwen</a>{renderBusy(p)&&<button disabled={busy} onClick={()=>run(async()=>{const r=await api.mutate(base+'/'+p.id+'/cancel','POST',{});setPlans(rows=>rows.map(v=>v.id===r.id?r:v));})}>Cancelar prueba</button>}</>}
 {isSpeechPlan(p)&&renderBusy(p)&&<button disabled={busy} onClick={()=>run(async()=>{const r=await api.mutate(base+'/'+p.id+'/cancel','POST',{});setPlans(rows=>rows.map(v=>v.id===r.id?r:v));})}>Cancelar plano · espera el trabajo propio</button>}
 {p.job?.error&&<p role="alert" className="pm-error">{p.job.error}</p>}
 {renderProgress(p.job?.progress)!==null&&<p>Progreso de la etapa actual: {renderProgress(p.job.progress)}%</p>}
 {(['completed','review_pending'].includes(p.status))&&p.job?.result_available&&<><video className="pm-preview" controls playsInline preload="metadata" aria-label={isNativePlan(p)?"Resultado de la recreación Ref2VA":"Resultado de prueba anterior"} src={'/api/parody/v1'+base+'/'+p.id+'/video'}/><a href={'/api/parody/v1'+base+'/'+p.id+'/video?download=true'}>Descargar vídeo</a><p>{isSpeechPlan(p)?(p.status==='completed'?'Naturalidad y sincronización aprobadas. Disponible para el montaje.':'Revisa la boca, la actuación y la sincronización. No se aplica un desfase universal.'):'Revisión humana pendiente. No es el montaje final ni incluye las voces preparadas.'}</p>{isSpeechPlan(p)&&p.status==='review_pending'&&<button disabled={busy} onClick={()=>run(async()=>{const r=await api.mutate(base+'/'+p.id+'/review','POST',{plan_hash:p.plan_hash,approved:true});setPlans(rows=>rows.map(v=>v.id===r.id?r:v));})}>Apruebo naturalidad y sincronización</button>}</>}
 </article>;}
 if(!enabled)return null;
 const recreations=plans.filter(isNativePlan),previous=plans.filter(p=>!isNativePlan(p));
 return <><Ref2vaComposer api={api} workspace={workspace} project={project} disabled={busy||dirty||!!active} onPrepared={r=>{setPlans(rows=>[r,...rows.filter(p=>p.id!==r.id)]);setApproved('');}}/>
 {error&&<p role="alert" className="pm-error">{error}</p>}
 {dirty&&<p>Guarda el borrador para preparar o ejecutar una recreación.</p>}
 <section className="pm-render" aria-label="Recreaciones Ref2VA"><h3>Planos recreados</h3>
 <p>La escena original inspira la situación y el escenario. Cada plano genera su propia actuación, expresiones y diálogo con Ref2VA.</p>
 {recreations.length?recreations.map(card):<p>Prepara arriba tu primera recreación con una imagen de escena y una voz de referencia.</p>}
 </section>
 {!!previous.length&&<details className="pm-render" open={active&&!isNativePlan(active)?true:undefined}><summary>Historial de pruebas anteriores · H3 Directo ({previous.length})</summary>
 <p>Estas pruebas pertenecen a la investigación anterior. Su sincronización labial no es la solución elegida para Parody Maker. Se conservan para consultar o descargar resultados; las recreaciones nuevas usan Ref2VA.</p>
 {previous.map(card)}</details>}
 </>;
}
