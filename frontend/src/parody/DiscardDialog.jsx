import React,{useEffect,useRef} from 'react';

// An HTML dialog keeps confirmation accessible inside the workspace. It does
// not bypass the separate browser warning for closing an unsaved page.
export default function DiscardDialog({message,onDecision}){
 const dialog=useRef(null),cancel=useRef(null);
 useEffect(()=>{
  const element=dialog.current;
  element.showModal();
  cancel.current?.focus();
  return()=>{if(element.open)element.close();};
 },[]);
 return <dialog ref={dialog} className="pm-discard-dialog" aria-labelledby="pm-discard-title" aria-describedby="pm-discard-message" onCancel={e=>{e.preventDefault();onDecision(false);}}>
  <h2 id="pm-discard-title">Confirmar cambio</h2>
  <p id="pm-discard-message">{message}</p>
  <div className="pm-discard-actions">
   <button ref={cancel} type="button" onClick={()=>onDecision(false)}>Cancelar</button>
   <button type="button" className="primary" onClick={()=>onDecision(true)}>Descartar cambios</button>
  </div>
 </dialog>;
}
