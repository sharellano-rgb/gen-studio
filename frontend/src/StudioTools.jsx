import React,{useEffect,useRef} from 'react';

// Native disclosure keeps its keyboard behavior; dismiss without altering the editor.
export default function StudioTools({children}){
  const container=useRef(null),trigger=useRef(null);
  function close(restoreFocus=false){
    if(container.current)container.current.open=false;
    if(restoreFocus)trigger.current?.focus({preventScroll:true});
  }
  useEffect(()=>{
    const outside=event=>{if(container.current?.open&&!container.current.contains(event.target))close();};
    document.addEventListener('pointerdown',outside);
    return()=>document.removeEventListener('pointerdown',outside);
  },[]);
  return <details className="studio-tools" ref={container}
    onKeyDown={event=>{if(event.key==='Escape'&&container.current?.open){event.preventDefault();event.stopPropagation();close(true);}}}
    onBlur={event=>{if(event.relatedTarget&&!event.currentTarget.contains(event.relatedTarget))close();}}>
    <summary ref={trigger} aria-label="Opciones del estudio" title="Borradores, ayuda y opciones de la toma">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg><span>Opciones</span>
    </summary>
    <div className="studio-tools-panel">{children(()=>close(true))}</div>
  </details>;
}
