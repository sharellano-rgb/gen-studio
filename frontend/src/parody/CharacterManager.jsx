import React,{useState} from 'react';
import {referenceRoles,traitCategories,newCharacter,allowedReference,removeCharacter,removeReference} from './characters.js';
import CharacterImageUpload from './CharacterImageUpload.jsx';
import {secondsToMs} from './navigation.js';

function IdentityPanel({initiallyOpen, children}) {
 const [open] = useState(initiallyOpen);
 return <details open={open}><summary>Identidad y vestuario</summary>{children}</details>;
}
export default function CharacterManager({cast,onChange,assets,shots,source,disabled,confirmDiscard,toolsForCharacter,onUploaded,workspaceId,withUploadLock,originalTarget}){
 const [selected,setSelected]=useState(''),[role,setRole]=useState('front'),[assetId,setAssetId]=useState(''),[error,setError]=useState('');
 const [traitCategory,setTraitCategory]=useState('gesture'),[traitText,setTraitText]=useState(''),[traitKind,setTraitKind]=useState('creative'),[evidenceRef,setEvidenceRef]=useState(''),[from,setFrom]=useState('0'),[to,setTo]=useState('1');
 const [shotId,setShotId]=useState(''),[target,setTarget]=useState(null);
 const character=cast.characters.find(c=>c.id===selected)||cast.characters[0];
 const subject=target??((character?.parody_role==='supporting'?character.name:originalTarget)||'');
 function change(patch){onChange({...cast,characters:cast.characters.map(c=>c.id===character.id?{...c,...patch}:c)});}
 function attempt(callback){try{callback();setError('');}catch(e){setError(e.message);}}
 function addTrait(){attempt(()=>{
  if(!traitText.trim())throw Error('Describe el rasgo de actuación.');
  let evidence=null;
  if(traitKind!=='creative'){
   const ref=character.references.find(r=>r.id===evidenceRef),asset=assets.find(a=>a.id===ref?.asset_id);
   const start_ms=secondsToMs(from),end_ms=secondsToMs(to);
   if(!ref||!asset?.duration||end_ms<=start_ms||end_ms>Math.round(asset.duration*1000))throw Error('Elige un clip de evidencia y un intervalo dentro de su duración.');
   evidence={reference_id:ref.id,start_ms,end_ms};
  }
  change({traits:[...character.traits,{id:crypto.randomUUID(),category:traitCategory,text:traitText.trim(),kind:traitKind,evidence,reviewed:false}]});setTraitText('');
 });}
 const media=assets.filter(a=>allowedReference(a,role));
 const refs=character?.references||[];
 return <section className="pm-characters" aria-label="Personajes y actuaciones"><h3>Personajes y actuaciones</h3>
  <p className="pm-help">La ficha define al personaje de la recreación. Reutiliza su identidad en distintos planos. Los rasgos son observaciones de una actuación o propuestas creativas, no atributos permanentes de una persona.</p>
  {error&&<p className="pm-error" role="alert">{error}</p>}
  <fieldset disabled={disabled}>
   <div className="pm-character-tabs">{cast.characters.map(c=><button type="button" key={c.id} aria-pressed={c.id===character?.id} onClick={()=>{setSelected(c.id);setTarget(null);setError('');setEvidenceRef('');setTraitText('');}}>{c.name||'Sin nombre'}</button>)}<button type="button" disabled={cast.characters.length>=32} onClick={()=>{const c=newCharacter();onChange({...cast,characters:[...cast.characters,c]});setSelected(c.id);setTarget(null);setTraitText('');setEvidenceRef('');}}>Añadir personaje</button></div>
   {character&&<div className="pm-character-editor" key={character.id}>
    <IdentityPanel initiallyOpen={!character.name || !character.appearance}>
    <div className="pm-character-grid"><label>Nombre del personaje<input maxLength={120} value={character.name} onChange={e=>change({name:e.target.value})}/></label><label>Papel en la parodia<select value={character.parody_role||"replacement"} onChange={e=>change({parody_role:e.target.value})}><option value="replacement">Personaje de reemplazo · diálogo principal</option><option value="supporting">Personaje de apoyo</option></select></label><label>Estilo visual<select value={character.style} onChange={e=>change({style:e.target.value})}><option value="realistic">Realista</option><option value="stylized_2d">Estilizado 2D</option><option value="stylized_3d">Estilizado 3D</option><option value="other">Otro</option></select></label></div>
    <label>Apariencia e identidad<textarea rows={3} maxLength={4000} value={character.appearance} onChange={e=>change({appearance:e.target.value})}/></label>
    <div className="pm-character-grid"><label>Vestuario<textarea maxLength={2000} value={character.wardrobe} onChange={e=>change({wardrobe:e.target.value})}/></label><label>Accesorios<textarea maxLength={2000} value={character.accessories} onChange={e=>change({accessories:e.target.value})}/></label></div>
    </IdentityPanel>
    <details><summary>Imágenes de referencia y clips · {refs.length}</summary>
     <CharacterImageUpload character={character} workspaceId={workspaceId} withUploadLock={withUploadLock} onAttach={(characterId,imageRole,asset)=>{
      const reference={id:crypto.randomUUID(),asset_id:asset.id,role:imageRole,sha256:null,name:asset.name};
      onUploaded(asset);
      onChange(current=>({...current,characters:current.characters.map(c=>c.id===characterId?{...c,references:[...c.references,reference]}:c)}));
     }}/>
     <div className="pm-reference-grid">{refs.map(r=>{const mediaUrl='/api/assets/'+encodeURIComponent(r.asset_id);return <article key={r.id} className="pm-reference"><strong>{referenceRoles[r.role]}</strong><small>{r.name}</small>{r.role==='voice'?<audio controls preload="metadata" style={{maxWidth:'100%'}} src={mediaUrl} aria-label={'Muestra de voz de '+character.name}/>:r.role==='acting'?<video controls playsInline preload="metadata" src={mediaUrl} aria-label={'Referencia '+r.name}/>:<img loading="lazy" src={mediaUrl} alt={referenceRoles[r.role]+' de '+character.name}/>}<small>{r.sha256?'Archivo verificado al guardar':'Pendiente de guardar'}</small><button type="button" onClick={async()=>{if(character.traits.some(t=>t.evidence?.reference_id===r.id)&&!await confirmDiscard('Al quitar este clip, sus rasgos pasarán a propuestas creativas sin revisar. ¿Continuar?'))return;change(removeReference(character,r.id));}}>Quitar referencia</button></article>;})}</div>
     <details className="pm-add-reference"><summary>Añadir desde mi biblioteca</summary>
     <div className="pm-character-grid"><label>Uso de la referencia<select value={role} onChange={e=>{setRole(e.target.value);setAssetId('');}}>{Object.entries(referenceRoles).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label><label>Archivo de mi biblioteca<select value={assetId} onChange={e=>setAssetId(e.target.value)}><option value="">Seleccionar archivo</option>{media.map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></label></div>
     {['acting','voice'].includes(role)&&<label>{role==='voice'?'Subir audio o vídeo de voz':'Subir vídeo de actuación'}<input type="file" accept={role==='voice'?'.wav,.mp3,.m4a,.flac,.ogg,.opus,.aac,.mp4,.mov,.webm':'video/mp4,video/quicktime,video/webm'} onChange={async e=>{
       const input=e.currentTarget,file=input.files?.[0];if(!file)return;
       try{const form=new FormData();form.append('file',file);const response=await fetch(role==='voice'&&/\.(wav|mp3|m4a|flac|ogg|opus|aac)$/i.test(file.name)?'/api/parody/v1/voice-assets':'/api/assets',{method:'POST',body:form});if(!response.ok){const problem=await response.json().catch(()=>({}));throw Error(problem.detail||'No se pudo subir la referencia.');}const uploaded=await response.json();onUploaded(uploaded);setAssetId(uploaded.id);setError('');}catch(err){setError(err.message);}finally{input.value='';}
     }}/></label>}
     {role==='voice'&&<small>Audio de hasta 25 MB. Para clonar, selecciona después una muestra de 3 a 25 segundos.</small>}
     <button type="button" disabled={!assetId||refs.length>=24} onClick={()=>{if(!media.some(a=>a.id===assetId))return;change({references:[...refs,{id:crypto.randomUUID(),asset_id:assetId,role,sha256:null,name:media.find(a=>a.id===assetId).name}]});setAssetId('');}}>Añadir referencia</button>
     </details>
    </details>
    {toolsForCharacter?.(character)}
    <details><summary>Ficha de actuación · {character.traits.length} rasgos</summary><p className="pm-help">Las observaciones e interpretaciones necesitan evidencia con tiempos. Puedes proponer rasgos desde un análisis guardado de este mismo clip de actuación. Se incorporan sin aprobar; revisa cada rasgo antes de marcarlo.</p>
     {character.traits.map(t=><article className="pm-trait" key={t.id}><strong>{traitCategories[t.category]}</strong><small>{t.kind==='observation'?'Observación del clip':t.kind==='interpretation'?'Interpretación contextual':'Propuesta creativa'}{t.evidence?.observation?' · propuesta de Qwen':''}</small><label>Descripción del rasgo<textarea maxLength={2000} value={t.text} onChange={e=>change({traits:character.traits.map(v=>v.id===t.id?{...v,text:e.target.value,reviewed:false}:v)})}/></label>{t.evidence&&<><p>Evidencia: {refs.find(r=>r.id===t.evidence.reference_id)?.name} · {t.evidence.start_ms/1000}–{t.evidence.end_ms/1000} s</p><a href={'/api/assets/'+encodeURIComponent(refs.find(r=>r.id===t.evidence.reference_id)?.asset_id||'')+'#t='+t.evidence.start_ms/1000+','+t.evidence.end_ms/1000} target="_blank" rel="noopener noreferrer">Ver intervalo de evidencia</a></>}<label className="pm-check"><input type="checkbox" checked={t.reviewed} onChange={e=>change({traits:character.traits.map(v=>v.id===t.id?{...v,reviewed:e.target.checked}:v)})}/>Rasgo revisado por mí</label><button type="button" onClick={()=>change({traits:character.traits.filter(v=>v.id!==t.id)})}>Quitar rasgo</button></article>)}
     <div className="pm-character-grid"><label>Tipo de rasgo<select value={traitCategory} onChange={e=>setTraitCategory(e.target.value)}>{Object.entries(traitCategories).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label><label>Origen del rasgo<select value={traitKind} onChange={e=>setTraitKind(e.target.value)}><option value="creative">Propuesta creativa</option><option value="observation">Observación del clip</option><option value="interpretation">Interpretación contextual</option></select></label></div>
     <label>Nuevo rasgo<textarea maxLength={2000} value={traitText} onChange={e=>setTraitText(e.target.value)} placeholder="Situación, gesto, ritmo o reacción que quieres dirigir…"/></label>
     {traitKind!=='creative'&&<><label>Clip de evidencia<select value={evidenceRef} onChange={e=>setEvidenceRef(e.target.value)}><option value="">Seleccionar referencia</option>{refs.filter(r=>['acting','voice'].includes(r.role)).map(r=><option key={r.id} value={r.id}>{r.name}</option>)}</select></label><div className="pm-character-grid"><label>Evidencia desde (s)<input inputMode="decimal" value={from} onChange={e=>setFrom(e.target.value)}/></label><label>Evidencia hasta (s)<input inputMode="decimal" value={to} onChange={e=>setTo(e.target.value)}/></label></div></>}
     <button type="button" disabled={!traitText.trim()||character.traits.length>=64} onClick={addTrait}>Añadir rasgo</button>
    </details>
    <details><summary>Apariciones y reemplazos</summary><p className="pm-help">Identifica al sujeto original y confirma la correspondencia en cada plano. La ficha se reutiliza; no se considera seguimiento automático.</p>
     {cast.appearances.filter(a=>a.character_id===character.id).map(a=><article className="pm-trait" key={a.id}><strong>{shots.find(s=>s.id===a.shot_id)?.label||'Plano no disponible'}</strong><label>Sujeto original a reemplazar<input maxLength={500} value={a.target_label} onChange={e=>onChange({...cast,appearances:cast.appearances.map(v=>v.id===a.id?{...v,target_label:e.target.value,reviewed:false}:v)})}/></label>{a.observation&&<small>Propuesta vinculada al análisis de Qwen, intervalo {a.observation.window_index+1}. Corrige la etiqueta si es necesario.</small>}<label className="pm-check"><input type="checkbox" checked={a.reviewed} onChange={e=>onChange({...cast,appearances:cast.appearances.map(v=>v.id===a.id?{...v,reviewed:e.target.checked}:v)})}/>Confirmo el sujeto de este plano</label><button type="button" onClick={()=>onChange({...cast,appearances:cast.appearances.filter(v=>v.id!==a.id)})}>Quitar asignación</button></article>)}
     <label>Plano para reutilizar este personaje<select value={shotId} onChange={e=>setShotId(e.target.value)}><option value="">Seleccionar plano</option>{shots.map(s=><option key={s.id} value={s.id}>{s.label} · {s.start_ms/1000}–{s.end_ms/1000} s</option>)}</select></label><label>Descripción del sujeto original<input maxLength={500} value={subject} onChange={e=>setTarget(e.target.value)} placeholder="Ej. pasajero a la derecha del encuadre"/></label>
     <button type="button" disabled={!source||!shotId||!subject.trim()} onClick={()=>attempt(()=>{if(cast.appearances.some(a=>a.shot_id===shotId&&(a.character_id===character.id||a.target_label.toLowerCase()===subject.trim().toLowerCase())))throw Error('Ya existe una asignación para este personaje o sujeto en el plano.');onChange({...cast,appearances:[...cast.appearances,{id:crypto.randomUUID(),character_id:character.id,shot_id:shotId,target_label:subject.trim(),source_sha256:source.sha256,observation:null,reviewed:false}]});setTarget('');})}>Asignar a este plano</button>
    </details>
    <button className="pm-remove-character" type="button" onClick={async()=>{if(await confirmDiscard('¿Quitar esta ficha y sus asignaciones del borrador? Las revisiones guardadas se conservarán.'))onChange(removeCharacter(cast,character.id));}}>Quitar personaje del borrador</button>
   </div>}
  </fieldset>
 </section>;
}
