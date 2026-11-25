import React,{useEffect,useState} from 'react';
import {renderSelection,eligibleSpeechVoices,ref2vaRequest} from './render.js';
import ScenePreparation from './ScenePreparation.jsx';
import SceneImage from './SceneImage.jsx';

export default function Ref2vaComposer({api,workspace,project,disabled,onPrepared}){
 const [enabled,setEnabled]=useState(false),[images,setImages]=useState([]),[voices,setVoices]=useState([]),[selection,setSelection]=useState(''),[scene,setScene]=useState(''),[voice,setVoice]=useState(''),[sceneReviewed,setSceneReviewed]=useState(false),[voiceReviewed,setVoiceReviewed]=useState(false),[directions,setDirections]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const chosen=renderSelection(project,selection);
 const [scenePreparation,setScenePreparation]=useState(false),[sceneImage,setSceneImage]=useState(false);
 useEffect(()=>{let live=true;api.get('/speech-capabilities').then(c=>{if(live){setEnabled(c.ref2va?.enabled===true);setScenePreparation(c.ref2va?.scene_preparation===true);setSceneImage(c.ref2va?.scene_image===true);}}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[api]);
 async function refresh(){setBusy(true);setError('');try{
   const response=await fetch('/api/assets',{cache:'no-store'});
   if(!response.ok||response.headers.get('X-Gen-Studio-Workspace')!==workspace.id)throw Error('No se pudo verificar la biblioteca de este espacio.');
   const data=await response.json();setImages(data.assets.filter(a=>a.kind==='image'));
   setVoices((await api.get('/projects/'+project.id+'/voice-previews')).previews);
 }catch(e){setError(e.message);}finally{setBusy(false);}}
 if(!enabled)return null;
 return <section className="pm-render" aria-label="Ref2VA con audio nativo"><h3>Recrear un plano · Ref2VA</h3>
 <p>Usa la escena original como inspiración para la situación, el escenario y la composición. Prepara una imagen completa con los personajes y el vestuario deseados; puede tener una pose o un encuadre nuevos. Ref2VA genera una actuación y su audio juntos: 5,17 s a 768p.</p>
 <p>Dirige gestos, expresiones, reacciones y diálogos nuevos. El vídeo original sirve de referencia de la historia; Ref2VA recibe la imagen preparada y la voz, sin copiar los movimientos del vídeo. La voz orienta el timbre: revisa pronunciación, parecido y sincronización al terminar.</p>
 {error&&<p role="alert" className="pm-error">{error}</p>}
 <fieldset disabled={disabled||busy||project.archived}>
 <label>Personaje y plano<select value={selection} onChange={e=>{setSelection(e.target.value);const traits=renderSelection(project,e.target.value).character?.traits.filter(t=>t.reviewed)||[];setDirections(traits.length?('Qwen: elige los rasgos que encajen con esta réplica y conviértelos en gestos y reacciones concretos. Rasgos revisados: '+traits.map(t=>t.evidence ? t.text+' (en el clip de referencia: '+(t.evidence.start_ms/1000)+'–'+(t.evidence.end_ms/1000)+' s; adapta la entrada a la nueva réplica)' : t.text).join(' · ')).slice(0,1500):'');setScene('');setSceneReviewed(false);setVoice('');setVoiceReviewed(false);}}><option value="">Seleccionar asignación revisada</option>{(project.cast?.appearances||[]).filter(a=>a.reviewed).map(a=><option key={a.id} value={a.id}>{project.cast.characters.find(c=>c.id===a.character_id)?.name} · {project.draft.shots.find(s=>s.id===a.shot_id)?.label}</option>)}</select></label>
 {sceneImage&&selection&&<details open><summary>Preparar una escena nueva</summary><SceneImage key={project.id+selection} api={api} project={project} appearance={selection} disabled={disabled||busy||project.archived} onReady={asset=>{setImages(items=>[asset,...items.filter(i=>i.id!==asset.id)]);setScene(asset.id);setSceneReviewed(true);}}/></details>}
 {scenePreparation&&selection&&<details><summary>Opcional · reutilizar una imagen de pruebas anteriores</summary><p>Si ya tienes una toma H3, puedes rescatar un fotograma como imagen inicial. Su movimiento y su audio no se utilizan en Ref2VA. Crear una toma H3 no es un requisito.</p><ScenePreparation key={project.id+selection} api={api} project={project} appearance={selection} disabled={disabled||busy||project.archived} onReady={asset=>{setImages(items=>[asset,...items.filter(i=>i.id!==asset.id)]);setScene(asset.id);setSceneReviewed(true);}}/></details>}
 <button type="button" onClick={refresh}>Cargar imágenes y voces de mi biblioteca</button>
 <label>Fotograma completo preparado · horizontal 7:4<select value={scene} onChange={e=>{setScene(e.target.value);setSceneReviewed(false);}}><option value="">Seleccionar escena preparada</option>{images.map(i=><option key={i.id} value={i.id}>{i.name}</option>)}</select></label>
 {scene&&<img className="pm-preview" src={'/api/assets/'+scene} alt="Escena preparada para Ref2VA"/>}
 <label><input type="checkbox" disabled={!scene} checked={sceneReviewed} onChange={e=>setSceneReviewed(e.target.checked)}/>Esta imagen contiene la escena completa, el personaje deseado y su cuerpo/vestuario visibles; revisé el encuadre.</label>
 <label>Voz ficticia o autorizada preparada<select value={voice} onChange={e=>{setVoice(e.target.value);setVoiceReviewed(false);}}><option value="">Seleccionar frase · 2 a 5,17 s</option>{eligibleSpeechVoices(project,selection,voices).map(v=><option key={v.id} value={v.id}>{v.binding.line.text} · {(v.artifact.duration_ms/1000).toFixed(2)} s</option>)}</select></label>
 {voice&&<audio controls preload="metadata" src={'/api/parody/v1/projects/'+project.id+'/voice-previews/'+voice+'/audio'}/>}
 <label><input type="checkbox" disabled={!voice} checked={voiceReviewed} onChange={e=>setVoiceReviewed(e.target.checked)}/>Escuché y apruebo esta voz como referencia.</label>
 <label>Indicaciones de actuación para Qwen<textarea maxLength={1500} value={directions} onChange={e=>setDirections(e.target.value)} placeholder="Actuación nueva: quién habla, personalidad, gestos, expresiones, pausas y reacción al terminar"/></label>
 <p className="pm-help">Qwen compara la escena con la primera referencia visual de la ficha: {chosen.references[0]?.name||'falta una referencia'}. La imagen completa guía la generación. No se añade lip sync posterior ni se sustituye su audio en el montaje.</p>
 <button disabled={!selection||!chosen.references.length||!sceneReviewed||!voiceReviewed} onClick={async()=>{setBusy(true);setError('');try{
   const body=ref2vaRequest(project,selection,scene,sceneReviewed,voice,voiceReviewed,directions);
   onPrepared(await api.mutate('/projects/'+project.id+'/render-plans','POST',body));
 }catch(e){setError(e.message);}finally{setBusy(false);}}}>Preparar plan Ref2VA</button>
 </fieldset></section>;
}
