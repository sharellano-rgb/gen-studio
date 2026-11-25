import React,{useState} from 'react';
import {newCharacter,referenceRoles,removeReference} from './characters.js';

export const guideSteps=[
 ['source','Vídeo de la escena'],['cast','Reparto'],['images','Imágenes de referencia'],
 ['voices','Muestras de voz'],['acting','Vídeos de actuación'],['scene','Analizar la escena y elegir planos'],
 ['characters','Confirmar personajes y actuaciones'],['script','Guion y voces'],
 ['render','Recrear y revisar los planos'],['montage','Montar y exportar']
];
const help={
 source:'Sube la escena que inspira la parodia o elige un vídeo de tu biblioteca. Puedes usar hasta 120 segundos.',
 cast:'Identifica a quien aparece en la escena, a quien lo interpretará y a quienes conservarás.',
 images:'Añade fotos a cada ficha. La foto del sujeto original sirve para identificarlo. Las fotos del reparto definen la apariencia de la parodia.',
 voices:'Añade una muestra por cada personaje que hablará. Puedes elegir una voz ficticia después. La autorización y el intervalo de clonación se configuran en Guion y voces.',
 acting:'Añade escenas del intérprete del reemplazo donde se aprecien sus gestos, pausas o muletillas. Podrás analizar el vídeo y el audio con Qwen en su ficha.',
 scene:'Analiza el vídeo con Qwen y asigna sus observaciones al reparto, o añade los planos manualmente.',
 characters:'Confirma el sujeto de cada plano en Apariciones y reemplazos. Aquí también puedes analizar los clips y revisar los rasgos de actuación.',
 script:'Pide una propuesta de guion o escribe los diálogos. Revisa quién habla y escucha las voces antes de recrear los planos.',
 render:'Prepara la imagen y la voz de cada plano, genera su actuación con Ref2VA y revisa el resultado.',
 montage:'Ordena los planos aprobados, revisa los cortes y exporta la parodia desde el montaje.'
};
const visualRoles=Object.entries(referenceRoles).filter(([r])=>r!=='acting'&&r!=='voice');
const isImage=r=>visualRoles.some(([key])=>key===r.role);
const videoExt='.mp4,.mov,.webm',audioExt='.wav,.mp3,.m4a,.flac,.ogg,.opus,.aac';
const imageExt='.jpg,.jpeg,.png,.webp';
const validSource=a=>a.kind==='video' && Number(a.duration)>0 && Number(a.duration)<=120;

export function MediaPicker({kind,assets,workspace,action,onAsset,onUploaded,slots=1}){
 const [status,setStatus]=useState('');
 const source=kind==='source',image=kind==='image',voice=kind==='voice';
 const accept=image?imageExt:voice?audioExt+','+videoExt:videoExt;
 const allowed=a=>source?validSource(a):image?a.kind==='image':voice?['audio','video'].includes(a.kind):a.kind==='video';
 function upload(event){
  const files=Array.from(event.currentTarget.files||[]);event.currentTarget.value='';
  if(!files.length)return;
  action(async()=>{
   if(files.length>slots)throw Error('Puedes añadir hasta '+slots+' archivos más aquí.');
   try{
    for(const [index,file] of files.entries()){
     const ext='.'+file.name.split('.').pop().toLowerCase();
     if(!accept.split(',').includes(ext))throw Error('Formato no admitido: '+file.name);
     setStatus('Subiendo '+(index+1)+' de '+files.length+' · '+file.name);
     const form=new FormData();form.append('file',file);
     const url=voice&&audioExt.split(',').includes(ext)?'/api/parody/v1/voice-assets':'/api/assets';
     const res=await fetch(url,{method:'POST',body:form});
     if(res.headers.get('X-Gen-Studio-Workspace')!==workspace.id)throw Error('No se pudo verificar el espacio de la carga.');
     const asset=await res.json();
     if(!res.ok)throw Error(asset.detail?.message||asset.detail||'No se pudo subir el archivo.');
     onUploaded(asset);
     if(!allowed(asset))throw Error(source?'Selecciona una escena de hasta 120 segundos.':'El archivo no corresponde a este paso.');
     await onAsset(asset);
    }
   }finally{setStatus('');}
  });
 }
 return <div className="pm-character-grid"><label>Subir {source?'vídeo de referencia':image?'imágenes':voice?'audio o vídeo':'vídeo de actuación'}<input type="file" accept={accept} multiple={!source} disabled={!slots} onChange={upload}/></label>
 <label>Elegir de mi biblioteca<select value="" disabled={!slots} onChange={e=>{const asset=assets.find(a=>a.id===e.target.value);if(asset)action(()=>onAsset(asset));}}><option value="">Seleccionar archivo</option>{assets.filter(allowed).map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
 {status&&<small role="status">{status}</small>}</div>;
}

function References({character,kind,original=false,refs,assets,onAttach,onRemove,...picker}){
 const [role,setRole]=useState('front');
 const shown=refs.filter(r=>kind==='image'?isImage(r):r.role===kind);
 return <section className="pm-guide-person"><h4>{character.name}</h4>
 {kind==='image'&&!original&&<label>Tipo de imagen<select value={role} onChange={e=>setRole(e.target.value)}>{visualRoles.map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label>}
 <MediaPicker {...picker} kind={kind} assets={assets} slots={24-refs.length} onAsset={asset=>onAttach(asset,kind==='image'?role:kind)}/>
 {shown.length>0&&<ul className="pm-guide-media">{shown.map(r=><li key={r.id}>{kind==='image'&&<img src={'/api/assets/'+r.asset_id} alt={character.name} loading="lazy"/>}<span>{r.name}</span>{onRemove&&<button type="button" onClick={()=>onRemove(r)}>Quitar</button>}</li>)}</ul>}
 </section>;
}

export default function ProjectGuide({phraseEditor,guide,cast,source,assets,workspace,action,onUploaded,onSource,onCast,onGuide,onAdvance,onManual,busy,shots,script,confirmDiscard}){
 const index=guideSteps.findIndex(([key])=>key===guide.step);
 if(index<0)return null;
 const [step,title]=guideSteps[index];
 const leads=cast.characters.filter(c=>c.parody_role!=='supporting');
 const mediaProps={assets,workspace,action,onUploaded};
 function changeCharacter(id,patch){onCast(c=>({...c,characters:c.characters.map(ch=>ch.id===id?{...ch,...patch}:ch)}));}
 function insertCharacter(role){onCast(c=>({...c,characters:[...c.characters,{...newCharacter(),name:'',parody_role:role}]}));}
 function attach(id,asset,role){
  const reference={id:crypto.randomUUID(),asset_id:asset.id,role,name:asset.name,sha256:null};
  onCast(c=>({...c,characters:c.characters.map(ch=>ch.id!==id||ch.references.some(r=>r.asset_id===asset.id&&r.role===role)?ch:{...ch,references:[...ch.references,reference]})}));
 }
 const original=guide.original_images.map(id=>({asset_id:id,id,role:'front',name:assets.find(a=>a.id===id)?.name||'Imagen del sujeto original'}));
 const ready=step==='source'?!!source:step==='cast'?guide.target.trim()&&leads.length>0&&cast.characters.every(c=>c.name.trim()):step==='scene'?shots.length>0:step==='characters'?cast.appearances.some(a=>a.reviewed&&leads.some(c=>a.character_id===c.id)):step==='script'?script.lines.length>0:true;
 return <section className="pm-guide" aria-label="Preparación guiada">
 <div className="pm-guide-top"><small>Paso {index+1} de {guideSteps.length}</small><button type="button" disabled={busy} onClick={onManual}>Ver editor completo</button></div>
 <progress max={guideSteps.length} value={index+1} aria-label="Progreso de la guía"/>
 <h3>{title}</h3><p>{help[step]}</p>
 <fieldset disabled={busy}>
 {step==='script'&&phraseEditor}
 {step==='source'&&<><MediaPicker {...mediaProps} kind="source" onAsset={onSource}/>{source&&<video key={source} src={'/api/assets/'+source} className="pm-preview" playsInline controls preload="metadata"/>}</>}
 {step==='cast'&&<><video className="pm-preview" controls playsInline preload="metadata" src={'/api/assets/'+source}/><label>¿A quién quieres reemplazar en el vídeo original?<input value={guide.target} maxLength={500} placeholder="Ej. John Connor, el pasajero" onChange={e=>onGuide({...guide,target:e.target.value})}/></label>
 <label>¿Quién lo interpretará en la parodia?<input maxLength={120} placeholder="Ej. Ace Ventura" value={leads[0]?.name||''} onChange={e=>{const name=e.target.value;if(leads.length)changeCharacter(leads[0].id,{name});else onCast(c=>({...c,characters:[{...newCharacter(),name,parody_role:'replacement'},...c.characters]}));}}/></label>
 {cast.characters.filter(c=>c.parody_role==='supporting').map(c=><div key={c.id}><label>Secundario que conservarás<input maxLength={120} value={c.name} placeholder="Ej. T-800" onChange={e=>changeCharacter(c.id,{name:e.target.value})}/></label>{!c.references.length&&!cast.appearances.some(a=>a.character_id===c.id)&&!script.lines.some(l=>l.character_id===c.id)&&<button type="button" onClick={()=>onCast(v=>({...v,characters:v.characters.filter(ch=>ch.id!==c.id)}))}>Quitar secundario</button>}</div>)}
 <button type="button" disabled={cast.characters.length>=32} onClick={()=>insertCharacter('supporting')}>Añadir secundario que se conserva</button></>}
 {step==='images'&&<References {...mediaProps} original character={{name:'Original · '+guide.target}} refs={original} kind="image" onRemove={r=>onGuide(v=>({...v,original_images:v.original_images.filter(id=>id!==r.asset_id)}))} onAttach={asset=>onGuide(v=>({...v,original_images:[...new Set([...v.original_images,asset.id])]}))}/>}
 {['images','voices','acting'].includes(step)&&(step==='acting'?leads:cast.characters).map(c=><References key={step+c.id} {...mediaProps} character={c} kind={step==='images'?'image':step==='voices'?'voice':'acting'} refs={c.references} onRemove={async r=>{
  if((c.voice_profile?.reference_id===r.id||c.traits.some(t=>t.evidence?.reference_id===r.id))&&!await confirmDiscard('Al quitar esta referencia tendrás que revisar la voz o los rasgos que la usan. ¿Continuar?'))return;
  onCast(v=>({...v,characters:v.characters.map(ch=>ch.id===c.id?removeReference(ch,r.id):ch)}));
 }} onAttach={(asset,role)=>attach(c.id,asset,role)}/>)}
 </fieldset>
 {!ready&&<small>{({source:'Elige el vídeo para continuar.',cast:'Indica el sujeto original y el nombre de cada personaje.',scene:'Añade al menos un plano o utiliza una observación del análisis.',characters:'Confirma al protagonista en al menos un plano.',script:'Añade o acepta al menos una frase.'})[step]}</small>}
 <div className="pm-guide-actions">{index>0&&<button type="button" disabled={busy} onClick={()=>onAdvance(guideSteps[index-1][0])}>Atrás</button>}
 <button type="button" disabled={busy||!ready} className="primary" onClick={()=>onAdvance(guideSteps[index+1]?.[0]||'done')}>{busy?'Un momento…':index===guideSteps.length-1?'Terminar guía':'Guardar y continuar'}</button></div>
 </section>;
}
