import React,{useEffect,useRef,useState} from 'react';
import {referenceRoles} from './characters.js';

const imageRoles=Object.entries(referenceRoles).filter(([role])=>role!=='acting'&&role!=='voice');

export default function CharacterImageUpload({character,workspaceId,withUploadLock,onAttach}){
 const [role,setRole]=useState('front');
 const [status,setStatus]=useState(''),[error,setError]=useState('');
 const controller=useRef(null);
 useEffect(()=>()=>controller.current?.abort(),[]);
 const available=24-character.references.length;
 function selectImages(event){
  const files=Array.from(event.currentTarget.files||[]);
  event.currentTarget.value='';
  if(!files.length)return;
  setError('');setStatus('');
  if(files.length>available){setError('La ficha admite hasta 24 referencias. Puedes añadir '+available+' más.');return;}
  for(const file of files){
   if(!/\.(png|jpe?g|webp)$/i.test(file.name)){setError(file.name+' · Usa JPG, PNG o WebP.');return;}
   if(file.size>25*1024*1024){setError(file.name+' supera los 25 MB.');return;}
  }
  withUploadLock(async()=>{
   const request=new AbortController();controller.current=request;
   let count=0;
   try{
    for(const file of files){
     setStatus('Subiendo '+(count+1)+' de '+files.length+' · '+file.name);
     const form=new FormData();form.append('file',file);
     const response=await fetch('/api/assets',{method:'POST',body:form,signal:request.signal});
     if(response.headers.get('X-Gen-Studio-Workspace')!==workspaceId)throw Error('No se pudo verificar tu espacio. Recarga GEN STUDIO.');
     const asset=await response.json();
     if(!response.ok)throw Error(typeof asset.detail==='string'?asset.detail:'No se pudo cargar '+file.name+'.');
     if(asset.kind!=='image'||!asset.id)throw Error('El archivo recibido no es una imagen.');
     if(request.signal.aborted)return;
     onAttach(character.id,role,asset);
     count++;
    }
   }catch(err){if(!request.signal.aborted)setError(err.message);}
   finally{
    if(!request.signal.aborted)setStatus(count?(count===1?'1 imagen añadida':'Imágenes añadidas: '+count)+'. Guarda el borrador para conservar la selección.':'');
    controller.current=null;
   }
  });
 }
 return <div className="pm-subpanel">
  <div className="pm-character-grid">
   <label>Tipo de imagen<select value={role} onChange={e=>setRole(e.target.value)}>{imageRoles.map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label>
   <label>Subir imágenes de referencia<input aria-label={'Subir imágenes de referencia de '+character.name} type="file" accept=".jpg,.jpeg,.png,.webp" multiple disabled={available<=0} onChange={selectImages}/></label>
  </div>
  <small>{available>0?'JPG, PNG o WebP · Hasta 25 MB por imagen.':'Esta ficha ya tiene 24 referencias.'}</small>
  {status&&<p role="status">{status}</p>}{error&&<p className="pm-error" role="alert">{error}</p>}
 </div>;
}
