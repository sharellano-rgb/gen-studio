import React,{useEffect,useRef,useState} from 'react';
import {SwapStage} from './ObjectSwapGuide.jsx';
import {emptyCharacter} from './directEditor.js';
const VIEWS=[['front','Frente'],['profile','Perfil'],['body','Cuerpo completo'],['back','Espalda'],['other','Otra vista']];
async function api(path,body,method='POST'){const r=await fetch('/api'+path,body?{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:undefined);const d=await r.json();if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:'No se pudo guardar la ficha del personaje.');return d;}
export default function CharacterCards({characters,images,disabled,onChange,onUpload,onActivity,onError,assigned=true}){
  const [library,setLibrary]=useState([]),[savedId,setSavedId]=useState(''),[working,setWorking]=useState(''),[notice,setNotice]=useState('');
  useEffect(()=>{let live=true;api('/characters').then(d=>{if(live)setLibrary(d.characters||[]);}).catch(e=>{if(live)setNotice(e.message);});return()=>{live=false;};},[]);
  useEffect(()=>{onActivity?.(Boolean(working));return()=>onActivity?.(false);},[working,onActivity]);
  const total=characters.reduce((n,c)=>n+c.reference_ids.length,0),locked=disabled||Boolean(working);
  const nameFields=useRef(new Map()),focusNew=useRef(null);
  useEffect(()=>{
    if(focusNew.current){
      nameFields.current.get(focusNew.current)?.focus();
      focusNew.current=null;
    }
  },[characters]);
  function createCard(){
    const blank=characters.find(c=>!library.some(item=>item.id===c.id)&&!c.target.trim()&&c.reference_ids.every(id=>!id));
    if(blank){nameFields.current.get(blank.id)?.focus();return;}
    const card={...emptyCharacter(),name:`Personaje ${characters.length+1}`};
    focusNew.current=card.id;
    onChange([...characters,card]);
  }
  function replace(index,next){onChange(characters.map((c,i)=>i===index?next:c));setNotice('');}
  function update(index,field,value){replace(index,{...characters[index],[field]:value});}
  function changeImage(index,viewIndex,id){const c=characters[index];replace(index,{...c,reference_ids:c.reference_ids.map((v,i)=>i===viewIndex?id:v)});}
  async function upload(event,index,viewIndex){const input=event.currentTarget,file=input.files?.[0];if(!file)return;setWorking(`${index}-${viewIndex}`);try{const asset=await onUpload(file,'image');changeImage(index,viewIndex,asset.id);}catch(e){onError(e.message);}finally{setWorking('');input.value='';}}
  async function save(index){const c=characters[index];if(!c.name.trim()||!c.target.trim()||c.reference_ids.some(id=>!id)){setNotice('Completa nombre, persona de la fuente e imágenes antes de guardar la ficha.');return;}setWorking('save-'+index);try{const body={name:c.name.trim(),target:c.target.trim(),reference_ids:c.reference_ids,views:c.views};const exists=library.some(item=>item.id===c.id);const saved=await api('/characters'+(exists?'/'+c.id:''),body,exists?'PUT':'POST');setLibrary(items=>[saved,...items.filter(item=>item.id!==saved.id)]);replace(index,{id:saved.id,name:saved.name,target:saved.target,reference_ids:[...saved.reference_ids],views:[...saved.views]});setNotice(`Ficha «${saved.name}» guardada para otros vídeos.`);}catch(e){onError(e.message);}finally{setWorking('');}}
  function load(){const c=library.find(item=>item.id===savedId);if(!c)return;const empty=characters.length===1&&characters[0].reference_ids.every(id=>!id);if(!empty&&characters.some(item=>item.id===c.id)){setNotice('Este personaje ya está en la escena. Describe todas sus apariciones en su asignación.');return;}if((empty?0:total)+c.reference_ids.length>9){setNotice('Esta ficha superaría el máximo de 9 imágenes. Quita primero alguna vista.');return;}const next={id:c.id,name:c.name,target:c.target,reference_ids:[...c.reference_ids],views:[...c.views]};onChange(empty?[next]:[...characters,next]);setSavedId('');setNotice('Ficha cargada. Revisa a quién reemplaza en este vídeo.');}
  return <div className="character-cards">
    <SwapStage step="cast">{library.length>0&&<div className="character-library"><label>Personajes guardados<select aria-label="Personajes guardados" value={savedId} disabled={locked} onChange={e=>setSavedId(e.target.value)}><option value="">Elige una ficha</option>{library.map(c=><option key={c.id} value={c.id}>{c.name} · {c.reference_ids.length} vistas</option>)}</select></label><button type="button" className="secondary" disabled={locked||!savedId} onClick={load}>Usar personaje guardado</button></div>}</SwapStage>
    <SwapStage step="cast"><button type="button" className="secondary" disabled={locked||total>=9} onClick={createCard}>Crear nueva ficha</button></SwapStage>
    {characters.map((c,index)=><article className="character-card" key={c.id+'-'+index} aria-label={`Personaje ${index+1}`}><div className="character-card-title"><strong>{c.name||`Personaje ${index+1}`}</strong><SwapStage step="cast"><button type="button" className="secondary" disabled={locked||characters.length===1} onClick={()=>onChange(characters.filter((_,i)=>i!==index))}>Quitar personaje</button></SwapStage></div>
      <SwapStage step="cast"><label>Nombre de la ficha<input ref={node=>{if(node)nameFields.current.set(c.id,node);else nameFields.current.delete(c.id);}} aria-label={`Nombre del personaje ${index+1}`} maxLength={80} disabled={locked} value={c.name} placeholder="Ej.: Luis · camiseta turquesa" onChange={e=>update(index,'name',e.target.value)}/></label>
      {assigned&&<label>A quién reemplaza en este vídeo{c.target_selection_id?' · selección visual confirmada':''}<input aria-label={`Persona fuente del personaje ${index+1}`} maxLength={180} disabled={locked} value={c.target} placeholder="Marca a la persona en el vídeo o descríbela aquí" onChange={e=>update(index,'target',e.target.value)}/></label>}
      </SwapStage><SwapStage step={['cast','references']}><div className="character-views">{c.reference_ids.map((id,viewIndex)=><div className="character-view" key={viewIndex}>
        {id&&<img src={'/api/assets/'+id} alt={`${c.name||'Personaje '+(index+1)} · ${VIEWS.find(([value])=>value===c.views[viewIndex])?.[1]||'Referencia'}`}/>}
        <label>Vista<select aria-label={`Vista ${viewIndex+1} del personaje ${index+1}`} disabled={locked} value={c.views[viewIndex]||'other'} onChange={e=>update(index,'views',c.reference_ids.map((_,i)=>i===viewIndex?e.target.value:c.views[i]||'other'))}>{VIEWS.map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>
        <label className="direct-upload upload-tile image-upload"><strong>{working===`${index}-${viewIndex}`?'Cargando…':id?'Cambiar foto':'Añadir foto'}</strong><input aria-label={`Subir vista ${viewIndex+1} del personaje ${index+1}`} type="file" accept=".png,.jpg,.jpeg,.webp" disabled={locked} onChange={e=>upload(e,index,viewIndex)}/><small>PNG, JPG o WebP</small></label>
        <label className="library-choice">Biblioteca de imágenes<select aria-label={`Imagen ${viewIndex+1} del personaje ${index+1}`} disabled={locked} value={id} onChange={e=>changeImage(index,viewIndex,e.target.value)}><option value="">Selecciona una imagen</option>{images.map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
        {c.reference_ids.length>1&&<button type="button" className="secondary" disabled={locked} onClick={()=>replace(index,{...c,reference_ids:c.reference_ids.filter((_,i)=>i!==viewIndex),views:c.views.filter((_,i)=>i!==viewIndex)})}>Quitar vista</button>}
      </div>)}</div>
      <div className="character-card-actions"><button type="button" className="secondary" disabled={locked||total>=9} onClick={()=>replace(index,{...c,reference_ids:[...c.reference_ids,''],views:[...c.views,'other']})}>Añadir otra vista</button><button type="button" className="secondary" disabled={locked} onClick={()=>save(index)}>{working==='save-'+index?'Guardando…':'Guardar ficha'}</button></div>
      </SwapStage></article>)}
    <SwapStage step="cast"><button type="button" className="secondary" disabled={locked||total>=9} onClick={()=>onChange([...characters,{...emptyCharacter(),name:`Personaje ${characters.length+1}`}])}>Añadir personaje</button></SwapStage>
    <SwapStage step={['cast','references']}><p className="composer-hint">{total} de 9 imágenes. Agrupa aquí las distintas vistas de cada personaje; su asignación se comparte automáticamente.</p></SwapStage>{notice&&<p role="status" className="editor-status">{notice}</p>}
  </div>;
}
