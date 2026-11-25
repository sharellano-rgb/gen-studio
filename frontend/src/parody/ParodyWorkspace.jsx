import React,{useEffect,useMemo,useRef,useState} from 'react';
import {createClient} from './api.js';
import {leaveParody,secondsToMs,selectProject} from './navigation.js';
import './parody.css';
import DiscardDialog from './DiscardDialog.jsx';
import ActingAudio from './ActingAudio.jsx';
import SceneAnalysis from './SceneAnalysis.jsx';
import ProjectHome from './ProjectHome.jsx';
import ProjectGuide from './ProjectGuide.jsx';
import CharacterManager from './CharacterManager.jsx';
import {emptyCast,assignObservation,proposeTraits} from './characters.js';
import ScriptEditor from './ScriptEditor.jsx';
import VoicePreviews from './VoicePreviews.jsx';
import ScriptProposal from './ScriptProposal.jsx';
import RenderShot from './RenderShot.jsx';
import Montage from './Montage.jsx';
import WorkspaceStage from './WorkspaceStage.jsx';
import RequiredPhrase from './RequiredPhrase.jsx';
import {emptyScript,removeShotLines} from './script.js';

function parseSceneNotes(notes) {
 const links = [];
 const prose = [];
 for (const line of notes.split(/\r?\n/)) {
  const match = line.match(/^(.{1,120}):\s*(https?:\/\/\S+)\s*$/i);
  if (!match) { prose.push(line); continue; }
  let url;
  try { url = new URL(match[2]); } catch { prose.push(line); continue; }
  if (!/^\/api\/assets\/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(url.pathname)) { prose.push(line); continue; }
  if (!links.some(item => item.path === url.pathname)) links.push({ path: url.pathname, label: match[1] });
 }
 return {links, prose};
}

function ProjectFiles({links}) {
 if (!links.length) return null;
 return <details className="pm-project-files"><summary>Archivos del proyecto ({links.length})</summary><div className="pm-media-group">
   {links.map(({path,label}) => {
    const duration = label.match(/\s*\(([\d.,]+\s*s)\)$/);
    return <a key={path} href={path} target="_blank" rel="noreferrer"><span aria-hidden="true" className="pm-open-media">↗</span><strong>{duration ? label.slice(0,duration.index) : label}</strong>{duration && <small>{duration[1]}</small>}</a>;
   })}
 </div></details>;
}

function SceneNotes({ notes, prose, onChange }) {
 return <section className="pm-scene-notes">
  <details className="pm-subpanel"><summary>Notas de la escena</summary>
   {prose.join('\n').trim() && <p className="pm-note-body">{prose.join('\n').trim()}</p>}
   <details><summary>Editar notas</summary><label>Texto y enlaces<textarea value={notes} maxLength={8000} rows={5} onChange={e=>onChange(e.target.value)}/></label></details>
  </details>
 </section>;
}

export default function ParodyWorkspace({workspace}){
 const api=useMemo(()=>createClient(workspace.id),[workspace.id]);
 const [cap,setCap]=useState(null),[projects,setProjects]=useState([]),[assets,setAssets]=useState([]),[project,setProject]=useState(null);
 const [name,setName]=useState(''),[source,setSource]=useState(''),[notes,setNotes]=useState(''),[shots,setShots]=useState([]);
 const {links,prose}=useMemo(()=>parseSceneNotes(notes),[notes]);
 const [error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(true),[dirty,setDirty]=useState(false);
 const [start,setStart]=useState('0'),[end,setEnd]=useState('5'),[label,setLabel]=useState('Plano 1');
 const [cast,setCast]=useState(emptyCast);
 const [stage,setStage]=useState('');
 const [guide,setGuide]=useState(null),[manual,setManual]=useState(false);
 const guided=!!guide&&guide.step!=='done'&&!manual&&!project?.archived;
 const workingStage=guided?guide.step:stage;
 useEffect(()=>{setStage(project?.source?'':'scene');},[project?.id]);
 const toggleStage=id=>setStage(current=>current===id?'':id);
 const [script,setScript]=useState(emptyScript);
 const lock=useRef(true),alive=useRef(true);
 const [confirmation,setConfirmation]=useState(null),confirmationResolve=useRef(null);
 function confirmDiscard(message){
  if(confirmationResolve.current)return Promise.resolve(false);
  return new Promise(resolve=>{confirmationResolve.current=resolve;setConfirmation(message);});
 }
 function finishConfirmation(accepted){const resolve=confirmationResolve.current;confirmationResolve.current=null;setConfirmation(null);resolve?.(accepted);}
 useEffect(()=>()=>{confirmationResolve.current?.(false);confirmationResolve.current=null;},[]);
 const videos=assets.filter(a=>a.kind==='video');
 const visibleAssets=source&&project?.source?.asset_id===source&&!videos.some(a=>a.id===source)?[...videos,{id:source,name:project.source.name,duration:project.source.duration_ms/1000}]:videos;
 function apply(value){if(!alive.current)return;if(value.id!==project?.id)setManual(false);setGuide(value.guide||null);setProject(value);setName(value.name);setSource(value.source?.asset_id||'');setNotes(value.draft.notes);setShots(value.draft.shots);setCast(value.cast||emptyCast());setScript(value.script||emptyScript());setDirty(false);selectProject(value.id);}
 async function list(){const rows=[];let offset=0;do{const page=await api.get('/projects?limit=100&offset='+offset);rows.push(...page.projects);offset=page.next_offset;}while(offset!==null);if(alive.current)setProjects(rows);}
 async function action(callback){if(lock.current)return;lock.current=true;setBusy(true);setError('');setNotice('');try{await callback();}catch(e){if(alive.current)setError(e.message);}finally{lock.current=false;if(alive.current)setBusy(false);}}
 useEffect(()=>{alive.current=true;const controller=new AbortController();
  (async()=>{try{const c=await api.get('/capabilities',{signal:controller.signal});if(!alive.current)return;setCap(c);if(!c.module_enabled)return;
   await list();const r=await fetch('/api/assets',{signal:controller.signal,cache:'no-store'});
   if(!r.ok||r.headers.get('X-Gen-Studio-Workspace')!==workspace.id)throw Error('No se pudieron consultar los vídeos de tu espacio.');
   const data=await r.json();if(!alive.current)return;setAssets(data.assets);
   const id=new URLSearchParams(location.search).get('parody_project');if(id){const p=await api.get('/projects/'+encodeURIComponent(id),{signal:controller.signal});if(alive.current)apply(p);}
  }catch(e){if(alive.current&&e.name!=='AbortError')setError(e.message);}finally{lock.current=false;if(alive.current)setBusy(false);}})();
  return()=>{alive.current=false;controller.abort();};
 },[api,workspace.id]);
 useEffect(()=>{if(!dirty)return;const warn=e=>{e.preventDefault();e.returnValue='';};window.addEventListener('beforeunload',warn);return()=>window.removeEventListener('beforeunload',warn);},[dirty]);
 const mayLeave=()=>!dirty||confirmDiscard('Hay cambios sin guardar. ¿Descartarlos?');
 function edit(setter,value){setter(value);setDirty(true);}
 function recordAsset(asset){setAssets(rows=>[asset,...rows.filter(r=>r.id!==asset.id)]);}
 async function changeSource(next){
  if(next===source)return;
  if((shots.length||cast.appearances.length||script.lines.length)&&!await confirmDiscard('Cambiar la fuente vaciará los planos, el guion y sus asignaciones del borrador. Las fichas se conservarán. ¿Continuar?'))return;
  edit(setSource,next);setShots([]);setCast(v=>({...v,appearances:[]}));setScript(emptyScript());
  if(guide&&source)setGuide(v=>({...v,target:'',original_images:[],step:'source'}));
 }
 async function saveDraft(nextGuide=guide,nextCast=cast){
  const saved=await api.mutate('/projects/'+project.id+'/draft','PUT',{expected_revision:project.revision,source_asset_id:source||null,notes,shots,...(cap.character_editing?{cast:nextCast}:{}),...(cap.script_editing?{script}:{}),...(nextGuide?{guide:nextGuide}:{})});
  if(!alive.current)return;apply(saved);await list();
 }
 function advance(step){action(async()=>{
  if(guide.step==='render'&&step==='montage'){
   const {plans}=await api.get('/projects/'+project.id+'/render-plans');
   if(!plans.some(plan=>plan.status==='completed'))throw Error('Genera un plano y aprueba su actuación y sincronización antes de pasar al montaje.');
  }
  if(step==='done'){
   const {montages}=await api.get('/projects/'+project.id+'/montages');
   if(!montages.some(m=>['review_pending','completed'].includes(m.status)))throw Error('Prepara y exporta el montaje para revisar el vídeo antes de terminar la guía.');
  }
  if(dirty)await saveDraft({...guide,step});
  else apply(await api.mutate('/projects/'+project.id+'/guide-position','PUT',{expected_revision:project.revision,previous:guide.step,step}));
  if(step==='done'){setStage('montage');setNotice('Guía terminada. Tu proyecto está guardado.');}
  requestAnimationFrame(()=>document.querySelector('.pm-guide, .pm-workspace')?.scrollIntoView({block:'start'}));
 });}
 function useObservation(record,window,actorIndex,characterId){try{if(source!==project.source?.asset_id)throw Error('Guarda primero el cambio de fuente.');const result=assignObservation({cast,shots,source:project.source,record,window,actorIndex,characterId});setCast(result.cast);setShots(result.shots);setDirty(true);setStage('characters');setNotice('Asignación propuesta. Revisa y confirma el sujeto en la ficha antes de guardar.');setError('');}catch(e){setError(e.message);}}
 function useTraits(record,window,actorIndex,characterId){try{edit(setCast,proposeTraits({cast,record,window,actorIndex,characterId}));setStage('characters');setNotice('Observaciones de Qwen añadidas sin aprobar. Revisa la evidencia, corrige el texto y confirma cada rasgo.');setError('');}catch(e){setError(e.message);}}
 function move(index,delta){const next=[...shots];[next[index],next[index+delta]]=[next[index+delta],next[index]];edit(setShots,next);}
 function addShot(){try{const a=secondsToMs(start),b=secondsToMs(end),asset=visibleAssets.find(a=>a.id===source);if(!source||!asset)throw Error('Selecciona el vídeo fuente.');if(!label.trim()||b<=a||b>Math.round(asset.duration*1000))throw Error('Comprueba el nombre y los tiempos del plano.');edit(setShots,[...shots,{id:crypto.randomUUID(),label:label.trim(),start_ms:a,end_ms:b}]);setLabel('Plano '+(shots.length+2));setError('');}catch(e){setError(e.message);}}
 const guidedScript=guided&&guide.step==='script';
 const phraseEditor=cap?.script_editing&&project&&<RequiredPhrase key={project.id} guided={guidedScript} script={script} cast={cast} workspace={workspace} assets={assets} action={action} onUploaded={recordAsset} onCast={v=>edit(setCast,v)} onChange={rule=>edit(setScript,{...script,required_phrase:rule})} disabled={busy||project.archived}/>;
 return <main className="pm-root"><header className="pm-header"><div><p className="pm-eyebrow">GEN STUDIO / {workspace.name}</p><h1>Parody Maker</h1><p>Una escena como inspiración. Personajes, diálogos y actuaciones nuevos.</p></div><button type="button" className="secondary" disabled={busy} onClick={async()=>{if(await mayLeave())leaveParody();}}>Volver a GEN STUDIO</button></header>
 {confirmation&&<DiscardDialog message={confirmation} onDecision={finishConfirmation}/>}
 {error&&<p className="pm-error" role="alert">{error} {project&&<button disabled={busy} onClick={async()=>{if(await mayLeave())action(async()=>{apply(await api.get('/projects/'+project.id));await list();});}}>Recargar versión guardada</button>}</p>}
 {notice&&<p role="status" className="pm-notice">{notice}</p>}
 {!cap?<p role="status">Comprobando disponibilidad…</p>:!cap.module_enabled?<section className="pm-card"><h2>Módulo pendiente de activación</h2><p>La instalación debe validarse antes de habilitar esta herramienta.</p></section>:<div className="pm-main-content">
 {!project?<ProjectHome projects={projects} busy={busy} onCreate={name=>action(async()=>{
  const p=await api.mutate('/projects','POST',{name,...(cap.guided_setup?{guided:true}:{})});
  if(!alive.current)return;apply(p);await list();setNotice('Proyecto creado.');
 })} onOpen={id=>action(async()=>apply(await api.get('/projects/'+id)))}/>:<section className="pm-card pm-workspace">
 <button type="button" className="pm-back-projects" disabled={busy} onClick={async()=>{
  if(!await mayLeave())return;
  setDirty(false);setProject(null);setGuide(null);setError('');setNotice('');selectProject('');
 }}>← Mis proyectos</button>
 <div className="pm-title"><h2>{project.name}</h2><span className={dirty?'pm-unsaved':''}>{dirty?'Cambios sin guardar':'Revisión '+project.revision+' guardada'}</span></div>
 <ProjectFiles key={project.id} links={links}/>
 <details className="pm-project-settings"><summary>Ajustes del proyecto</summary><fieldset disabled={busy}><form className="pm-rename" onSubmit={e=>{e.preventDefault();action(async()=>{const p=await api.mutate('/projects/'+project.id,'PATCH',{expected_revision:project.revision,name});setProject(p);await list();setNotice('Nombre guardado.');});}}><label>Nombre<input value={name} maxLength={120} required onChange={e=>setName(e.target.value)}/></label><button className="secondary" disabled={!name.trim()||name===project.name}>Renombrar</button></form>
    <button className="secondary" onClick={async()=>{if(await mayLeave())action(async()=>{apply(await api.mutate('/projects/'+project.id,'PATCH',{expected_revision:project.revision,archived:!project.archived}));await list();setNotice(project.archived?'Proyecto restaurado.':'Proyecto archivado; sus versiones se conservan.');});}}>{project.archived?'Restaurar proyecto':'Archivar proyecto'}</button>
   </fieldset></details>
 {project.archived&&<p>Proyecto archivado. Restáuralo en Ajustes del proyecto para continuar editando.</p>}
 {guide&&guide.step!=='done'&&!project.archived&&(manual?<button type="button" disabled={busy} onClick={()=>setManual(false)}>Volver a la guía</button>:<ProjectGuide phraseEditor={phraseEditor} guide={guide} cast={cast} source={source} assets={assets} workspace={workspace} action={action} confirmDiscard={confirmDiscard} onUploaded={recordAsset} busy={busy} shots={shots} script={script} onCast={value=>edit(setCast,value)} onGuide={value=>edit(setGuide,value)} onSource={a=>changeSource(a.id)} onManual={()=>{setManual(true);setStage(['source','scene'].includes(guide.step)?'scene':['cast','images','voices','acting'].includes(guide.step)?'characters':guide.step);}} onAdvance={advance}/>)}
 <div className="pm-stages" data-guided-stage={guided?guide.step:undefined}>
 <WorkspaceStage id="scene" number="01" title="Escena y planos" description="Referencia original, notas, planos y análisis visual." summary={(source?'Referencia elegida':'Sin vídeo de referencia')+' · '+shots.length+' planos'} open={workingStage==='scene'} onToggle={()=>toggleStage('scene')}>
 <fieldset disabled={busy||project.archived}><label>Vídeo fuente<select value={source} onChange={e=>changeSource(e.target.value)}><option value="">Seleccionar de mi espacio</option>{visibleAssets.map(a=><option key={a.id} value={a.id}>{a.name} · {Number(a.duration).toFixed(2)} s</option>)}</select></label><p className="pm-help">Selecciona un vídeo ya cargado en GEN STUDIO. Esta fase admite fuentes de hasta 120 segundos.</p>
    {source&&<video key={source} className="pm-preview" src={'/api/assets/'+encodeURIComponent(source)} controls playsInline preload="metadata" aria-label="Vista previa de la fuente"/>}
    <SceneNotes notes={notes} prose={prose} onChange={value=>edit(setNotes,value)}/>
    <h3>Planos manuales</h3><p className="pm-help">Los tiempos corresponden al vídeo fuente. Ordena los planos para proponer el montaje.</p>
    <details className="pm-subpanel" open={!shots.length}><summary>Añadir un plano de referencia</summary><div className="pm-shot-input"><label>Nombre<input value={label} maxLength={120} onChange={e=>setLabel(e.target.value)}/></label><label>Inicio (s)<input inputMode="decimal" value={start} onChange={e=>setStart(e.target.value)}/></label><label>Final (s)<input inputMode="decimal" value={end} onChange={e=>setEnd(e.target.value)}/></label><button type="button" className="secondary" disabled={!source||shots.length>=240} onClick={addShot}>Añadir plano</button></div></details>
    <ol className="pm-shots pm-manual-shots">{shots.map((s,i)=><li key={s.id}><span className="pm-shot-position" aria-hidden="true">{i+1}</span><div className="pm-shot-caption"><strong>{s.label}</strong><small>{(s.start_ms/1000).toLocaleString('es-MX')} – {(s.end_ms/1000).toLocaleString('es-MX')} s</small></div><div className="pm-shot-actions"><button disabled={i===0} aria-label={'Subir '+s.label} onClick={()=>move(i,-1)}>↑</button><button disabled={i===shots.length-1} aria-label={'Bajar '+s.label} onClick={()=>move(i,1)}>↓</button><button aria-label={'Quitar '+s.label} onClick={async()=>{if((cast.appearances.some(a=>a.shot_id===s.id)||script.lines.some(l=>l.shot_id===s.id))&&!await confirmDiscard('Quitar este plano también quitará sus frases y asignaciones del borrador. ¿Continuar?'))return;edit(setShots,shots.filter(v=>v.id!==s.id));setCast(v=>({...v,appearances:v.appearances.filter(a=>a.shot_id!==s.id)}));setScript(v=>removeShotLines(v,s.id));}}>Quitar</button></div></li>)}</ol></fieldset>
 {cap?.analysis==='visual_review'&&<details className="pm-subpanel"><summary>Analizar la escena con Qwen</summary><SceneAnalysis key={project.id} workspace={workspace} project={project} dirty={dirty} enabled={cap?.analysis==='visual_review'} characters={cap?.character_editing?cast.characters:[]} onAssign={busy||project.archived?null:useObservation} onTraits={busy||project.archived?null:useTraits}/></details>}
 </WorkspaceStage>
 <WorkspaceStage id="characters" number="02" title="Personajes" description="Identidad, referencias, personalidad y apariciones." summary={cast.characters.length+(cast.characters.length===1?' personaje · ':' personajes · ')+cast.appearances.length+(cast.appearances.length===1?' asignación':' asignaciones')} open={workingStage==='characters'} onToggle={()=>toggleStage('characters')}>
 {guided&&guide.target&&<aside className="pm-guide-person"><strong>Sujeto original · {guide.target}</strong><div className="pm-guide-thumbs">{guide.original_images.map(id=><img key={id} src={'/api/assets/'+id} alt={'Referencia de '+guide.target}/>)}</div></aside>}
 <fieldset disabled={busy||project.archived}>{cap?.character_editing&&<CharacterManager key={project.id} cast={cast} workspaceId={workspace.id} withUploadLock={action} onUploaded={recordAsset} originalTarget={guide?.target} toolsForCharacter={character=><>
 <details><summary>Analizar clips de actuación</summary>{character.references.filter(r=>r.role==='acting').length===0&&<p>Añade un vídeo como referencia de actuación y guarda el borrador.</p>}{character.references.filter(r=>r.role==='acting').map(r=><details key={r.id}><summary>{r.name}</summary><SceneAnalysis workspace={workspace} project={project} dirty={dirty} enabled={cap.analysis==='visual_review'} characters={[character]} actorId={character.id} actingRef={r} onTraits={useTraits}/><ActingAudio workspace={workspace} project={project} reference={r} dirty={dirty} onTrait={({start_ms,end_ms,...trait})=>{
  if(character.traits.length>=64)throw Error('La ficha admite hasta 64 rasgos.');
  if(character.traits.some(t=>t.category===trait.category&&t.evidence?.reference_id===r.id&&t.evidence.start_ms===start_ms&&t.evidence.end_ms===end_ms))throw Error('Este intervalo ya está registrado en esa categoría.');
  const evidence={reference_id:r.id,start_ms,end_ms};
  edit(setCast,{...cast,characters:cast.characters.map(c=>c.id===character.id?{...c,traits:[...c.traits,{...trait,id:crypto.randomUUID(),evidence}]}:c)});
 }}/></details>)}</details>
 <details><summary>Voz del personaje · VoiceStudio</summary>{character.voice_profile?.mode==='clone'&&<p>Voz de referencia configurada para las nuevas frases.</p>}<p>Elige una frase, abre «Voz y pausas» y selecciona «Clonar muestra autorizada». Guarda y escucha la prueba aquí.</p><ScriptEditor script={{...script,lines:script.lines.filter(l=>l.character_id===character.id)}} onSaveVoice={profile=>edit(setCast,{...cast,characters:cast.characters.map(c=>c.id===character.id?{...c,voice_profile:{...profile}}:c)})} cast={{characters:[character]}} shots={shots} disabled={busy} onChange={next=>{const own=new Map(next.lines.map(l=>[l.id,l]));const lines=script.lines.flatMap(l=>l.character_id!==character.id?[l]:own.has(l.id)?[own.get(l.id)]:[]);edit(setScript,{...script,lines:[...lines,...next.lines.filter(l=>!script.lines.some(x=>x.id===l.id))]});}}/><VoicePreviews workspace={workspace} project={project} dirty={dirty} enabled={cap.voice==='cpu_preview'} characterId={character.id}/></details></>} onChange={v=>edit(setCast,v)} assets={assets} shots={shots} source={source===project.source?.asset_id?project.source:null} disabled={busy} confirmDiscard={confirmDiscard}/>}</fieldset>
 {!cap?.character_editing&&<p>Las fichas de personajes todavía no están habilitadas.</p>}
 </WorkspaceStage>
 <WorkspaceStage id="script" number="03" title="Guion y voces" description="Proponer diálogos, dirigir la actuación y escuchar las voces." summary={script.lines.length+' frases · diálogos, actuación y audio'} open={workingStage==='script'} onToggle={()=>toggleStage('script')}>
 {!guidedScript&&phraseEditor}
 {cap?.script_proposals&&<details className="pm-subpanel"><summary>Proponer un guion con Qwen</summary><ScriptProposal key={'proposal-'+project.id} workspace={workspace} project={project} dirty={dirty} enabled={cap?.script_proposals} onApply={async value=>{if(script.lines.length&&!await confirmDiscard('La propuesta reemplazará las frases de este borrador. Los audios y revisiones anteriores se conservarán. ¿Continuar?'))return;edit(setScript,value);setNotice('Propuesta incorporada al borrador. Revisa y guarda antes de preparar voces.');}}/></details>}
 <fieldset disabled={busy||project.archived}>{cap?.script_editing&&<ScriptEditor key={project.id} script={script} onChange={v=>edit(setScript,v)} cast={cast} shots={shots} disabled={busy}/>}</fieldset>
 {cap?.voice==='cpu_preview'&&<details className="pm-subpanel"><summary>Escuchar y generar voces</summary><VoicePreviews key={'voice-'+project.id} workspace={workspace} project={project} dirty={dirty} enabled={cap?.voice==='cpu_preview'}/></details>}
 </WorkspaceStage>
 <WorkspaceStage id="render" number="04" title="Recrear planos" description="Imagen de escena y voz de referencia → actuación y audio nuevos." summary="Ref2VA · preparar, generar y revisar" open={workingStage==='render'} onToggle={()=>toggleStage('render')}>
 <RenderShot key={'render-'+project.id} workspace={workspace} project={project} dirty={dirty} enabled={cap?.rendering==='h3_single_shot'}/>
 {cap?.rendering!=='h3_single_shot'&&<p>La generación de planos todavía no está habilitada.</p>}
 </WorkspaceStage>
 <WorkspaceStage id="montage" number="05" title="Montaje y exportación" description="Ordenar recreaciones aprobadas, revisar y descargar." summary="Secuencia final · resultados y descargas" open={workingStage==='montage'} onToggle={()=>toggleStage('montage')}>
 <Montage key={'montage-'+project.id} workspace={workspace} project={project} dirty={dirty} enabled={cap?.montage===true}/>
 {!cap?.montage&&<p>El montaje todavía no está habilitado.</p>}
 </WorkspaceStage>
 </div>
 <div className={"pm-draft-bar"+(dirty?" has-changes":"")}><div><strong>{dirty?'Tienes cambios sin guardar':'Borrador guardado'}</strong><small>Escena, personajes y guion · guardar no inicia una generación.</small></div><button type="button" className="primary pm-save-draft" disabled={!dirty||busy||project.archived} onClick={()=>action(async()=>{await saveDraft();setNotice('Borrador guardado. No se inició ninguna generación.');})}>{busy?'Guardando…':'Guardar borrador'}</button></div>
 </section>}
 </div>}
 </main>;
}
