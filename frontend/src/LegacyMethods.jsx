import React,{useState} from 'react';

export default function LegacyMethods({children}) {
  const [open,setOpen]=useState(false);
  return <details className="legacy-corrections" onToggle={event=>{
    if(event.target===event.currentTarget)setOpen(event.currentTarget.open);
  }}><summary>Métodos anteriores y ensayos</summary>
    {open&&<><p>Corrección CPU, afinado experimental y pruebas anteriores. Los vídeos y sus revisiones se conservan.</p>{children}</>}
  </details>;
}
