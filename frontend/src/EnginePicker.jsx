import React,{useId,useState} from 'react';
import {objectSwapEngines} from './engineSelection.js';
export default function EnginePicker({value,onChange,compact=false,engines=objectSwapEngines,ariaLabel='Motor de Object Swap',disabled=false}){
  const [open,setOpen]=useState(false),id=useId();
  const selected=engines.find(engine=>engine.id===value);
  return <section className={"panel direct-engine-choice engine-picker"+(compact?" engine-picker-compact":"")} aria-label={ariaLabel}>
    <button type="button" className="engine-picker-summary" disabled={disabled} aria-expanded={open} aria-controls={id} onClick={()=>setOpen(current=>!current)}>
      <span><small>Método</small><strong>{compact&&selected?.id==='h3_alisson'?'Alisson v1.1':selected?.label||'Método anterior'}</strong></span>
      <span className="engine-picker-action">{open?'Cerrar':'Cambiar'} <span aria-hidden="true">{open?'−':'+'}</span></span>
    </button>
    <div className="engine-picker-options" id={id} role="group" aria-label="Cambiar motor" hidden={!open}>
      {engines.map(engine=><button key={engine.id} type="button" disabled={disabled} aria-pressed={value===engine.id} onClick={()=>{onChange(engine.id);setOpen(false);}}>
        <strong>{engine.label}</strong><small>{engine.detail}</small>
      </button>)}
    </div>
  </section>;
}
