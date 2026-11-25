import React,{useEffect,useRef,useState} from 'react';
import {flattenCharacters,planMatches} from './directEditor.js';
import VisualTargetPicker from './VisualTargetPicker.jsx';
import {applyShotSelection,missingShotSelection,shotCharacters,toggleShotCharacter} from './visualTargets.js';
async function api(path,body){const r=await fetch('/api'+path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:undefined);const d=await r.json();if(!r.ok){const error=Error(typeof d.detail==='string'?d.detail:'No se pudieron analizar los planos.');error.status=r.status;throw error;}return d;}
export default function ShotPlanReview({videoId,frames,characters,planId,edits,busy,onChange,onPlan,onError,onVisualActivity,onCharacterResolved}){
  const [plan,setPlan]=useState(null),[pendingId,setPendingId]=useState(planId||''),[sending,setSending]=useState(false),[failure,setFailure]=useState(''),[visualShot,setVisualShot]=useState(null);
  const source=useRef(videoId+'-'+frames),sequence=useRef(0);
  useEffect(()=>{const key=videoId+'-'+frames;if(source.current!==key){source.current=key;sequence.current++;setSending(false);setPendingId('');setPlan(null);setVisualShot(null);setFailure('');onPlan(null);}},[videoId,frames]);
  useEffect(()=>{if(planId&&planId!==pendingId)setPendingId(planId);},[planId]);
  useEffect(()=>{
    if(!pendingId)return;let live=true,timer;
    async function poll(){try{const next=await api('/direct/shot-plan/'+pendingId);if(!live)return;setFailure('');setPlan(next);onPlan(next);if(['queued','running'].includes(next.status))timer=setTimeout(poll,1500);else if(next.status==='failed')setFailure(next.error||'No se pudo completar el análisis.');}catch(e){if(live){setFailure(e.message);if(e.status>=400&&e.status<500){setPlan({status:'failed'});onPlan(null);}else timer=setTimeout(poll,3000);}}}
    poll();return()=>{live=false;clearTimeout(timer);};
  },[pendingId]);
  async function analyze(){const current=++sequence.current;setSending(true);setFailure('');onChange(null,[]);setPlan(null);onPlan(null);try{const next=await api('/direct/shot-plan',{video_id:videoId,frames});if(current!==sequence.current)return;setPlan(next);onPlan(next);setPendingId(next.id);}catch(e){if(current===sequence.current){setFailure(e.message);onError(e.message);}}finally{if(current===sequence.current)setSending(false);}}
  const running=sending||['queued','running'].includes(plan?.status),matching=planMatches(plan,videoId,frames),flat=flattenCharacters(characters);
  function editFor(index){return edits.find(e=>e.index===index)||{index,passthrough:false,targets:[],target_selections:[],background:'inherit',background_description:''};}
  function saveEdit(next){onChange(null,[...edits.filter(e=>e.index!==next.index),next].sort((a,b)=>a.index-b.index));}
  function change(index,field,value){const next={...editFor(index),[field]:value,...(field==='background'&&value!=='replace'?{background_description:''}:{}),...(field==='passthrough'&&value?{targets:[],target_selections:[],background:'inherit',background_description:''}:{}),...(field==='targets'?{target_selections:[]}:{} )};if(field==='passthrough'&&value)delete next.disabled_character_ids;saveEdit(next);}
  function target(index,characterIndex,value){const current=editFor(index),targets=current.targets.length?[...current.targets]:[...flat.targets];let offset=0;characters.forEach((c,i)=>{if(i===characterIndex)c.reference_ids.forEach((_,j)=>targets[offset+j]=value);offset+=c.reference_ids.length;});const next={...current,targets,target_selections:(current.target_selections||[]).filter(v=>v.character_id!==characters[characterIndex].id)};onChange(null,[...edits.filter(e=>e.index!==index),next].sort((a,b)=>a.index-b.index));}
  function assign(index,record){const next=applyShotSelection(editFor(index),characters,record);onCharacterResolved?.(record);onChange(null,[...edits.filter(e=>e.index!==index),next].sort((a,b)=>a.index-b.index));}
  const invalid=matching&&plan.segments.some(s=>{const e=editFor(s.index);const active=shotCharacters(characters,e);return !e.passthrough&&(s.keep>345||(e.background==='replace'&&!e.background_description.trim())||!active.length||active.some(c=>!c.target.trim())||missingShotSelection(characters,edits,e));});
  return <section className="shot-plan-review" aria-label="Revisión de planos">
    <p>Analiza los cortes y activa en cada plano los personajes que quieras reemplazar.</p>
    <button type="button" className="secondary" disabled={busy||running||!videoId} onClick={analyze}>{running?'Analizando los cortes…':plan?'Volver a analizar planos':'Analizar planos'}</button>
    {running&&<p role="status">Preparando las miniaturas y los límites de cada plano…</p>}{failure&&<p className="error" role="alert">{failure}</p>}
    {matching&&<><div className="shot-strip" aria-label="Planos detectados">{plan.segments.map(s=><a key={s.index} href={'#edit-shot-'+s.index} className="shot-strip-item"><img src={s.thumbnail_url} alt={`Plano ${s.index+1}`}/><span>{s.index+1} · {(s.start/24).toFixed(2)} s</span></a>)}</div>
      {plan.segments.map(s=>{const edit=editFor(s.index),active=shotCharacters(characters,edit);let offset=0;return <article className="shot-card" id={'edit-shot-'+s.index} key={s.index}><div className="shot-card-heading"><img src={s.thumbnail_url} alt={`Inicio del plano ${s.index+1}`}/><div><strong>Plano {s.index+1}</strong><span>{(s.start/24).toFixed(2)}–{((s.start+s.keep)/24).toFixed(2)} s</span></div></div>
        <label className="check-option"><input type="checkbox" checked={edit.passthrough} disabled={busy} onChange={e=>change(s.index,'passthrough',e.target.checked)}/>Conservar este plano sin cambios</label>
        {s.keep>345&&!edit.passthrough&&<p className="error" role="status">Este plano continuo supera 14,38 s. Conserva el original en este plano o acorta el vídeo antes de generar.</p>}
        {!edit.passthrough&&<>{characters.map((c,i)=><label className="check-option" key={c.id}><input type="checkbox" checked={!edit.disabled_character_ids?.includes(c.id)} disabled={busy} onChange={e=>saveEdit(toggleShotCharacter(edit,c.id,e.target.checked))}/> Reemplazar con {c.name||`Personaje ${i+1}`}</label>)}
        {!active.length&&<p className="editor-status" role="status">Activa un personaje o conserva este plano sin cambios.</p>}
        <button type="button" className="secondary shot-visual-toggle" disabled={busy||!active.length} onClick={()=>setVisualShot(current=>current===s.index?null:s.index)}>{visualShot===s.index?'Cerrar selección visual':'Seleccionar personas en este plano'}</button>
        {missingShotSelection(characters,edits,edit)&&<p className="editor-status" role="status">Confirma en este plano a los personajes activos que marcaste visualmente en otros planos.</p>}
        {visualShot===s.index&&active.length>0&&<VisualTargetPicker videoId={videoId} startFrame={s.start} frames={s.keep} characters={active} disabled={busy} onAssign={record=>assign(s.index,record)} onActivity={onVisualActivity} label={'Selección visual · plano '+(s.index+1)}/>}
        <details><summary>Asignación y fondo de este plano</summary>{characters.map((c,i)=>{const position=offset;offset+=c.reference_ids.length;if(edit.disabled_character_ids?.includes(c.id))return null;return <label key={c.id+'-'+i}>{c.name||`Personaje ${i+1}`} reemplaza a<input aria-label={`Persona del plano ${s.index+1}, personaje ${i+1}`} maxLength={180} disabled={busy} value={edit.targets.length?edit.targets[position]||'':c.target} onChange={e=>target(s.index,i,e.target.value)}/></label>;})}
          {edit.targets.length>0&&<button type="button" className="secondary" disabled={busy} onClick={()=>change(s.index,'targets',[])}>Usar asignaciones generales</button>}
          <label>Fondo de este plano<select aria-label={`Fondo del plano ${s.index+1}`} disabled={busy} value={edit.background} onChange={e=>change(s.index,'background',e.target.value)}><option value="inherit">Seguir la instrucción general</option><option value="source">Conservar el fondo original de este plano</option><option value="replace">Usar un fondo diferente en este plano</option></select></label>
          {edit.background==='replace'&&<label>Describe este fondo<textarea aria-label={`Descripción del fondo del plano ${s.index+1}`} maxLength={500} disabled={busy} value={edit.background_description} onChange={e=>change(s.index,'background_description',e.target.value)}/></label>}
        </details></>}
      </article>;})}
      <label className="check-option shot-confirm"><input type="checkbox" aria-label="Confirmar planos revisados" checked={planId===plan.id} disabled={busy||invalid} onChange={e=>onChange(e.target.checked?plan.id:null,edits)}/>Revisé los cortes, las asignaciones y los fondos</label>
      {invalid&&<p role="status" className="editor-status">Resuelve las indicaciones de los planos antes de confirmar.</p>}
    </>}
  </section>;
}
