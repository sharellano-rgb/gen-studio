import React,{createContext,useCallback,useContext,useEffect,useRef,useState} from 'react';
import {qwenLabel,qwenRequest,newerSelection} from './qwenModels.js';
import './qwenModelSelector.css';

const Context=createContext(null);
export const useQwenModels=()=>useContext(Context);

export function QwenModelsProvider({children}){
  const [catalog,setCatalog]=useState(null),[selection,setSelection]=useState(null),[error,setError]=useState(''),[saving,setSaving]=useState(false);
  const alive=useRef(true),writing=useRef(false);
  const refresh=useCallback(async(full=true)=>{
    try{
      const data=await qwenRequest(full?'models':'selection');
      if(!alive.current)return;
      if(full)setCatalog(data);
      setSelection(current=>newerSelection(current,full?data.selection:data));
    }catch(e){if(alive.current)setError(e.name==='AbortError'?'Qwen no respondió. Puedes volver a comprobarlo.':e.message);}
  },[]);
  useEffect(()=>{
    alive.current=true;refresh();
    const selectionTimer=setInterval(()=>{if(!document.hidden)refresh(false);},5000);
    const catalogTimer=setInterval(()=>{if(!document.hidden)refresh();},30000);
    const focus=()=>refresh();window.addEventListener('focus',focus);
    return()=>{alive.current=false;clearInterval(selectionTimer);clearInterval(catalogTimer);window.removeEventListener('focus',focus);};
  },[refresh]);
  async function select(model_id){
    if(writing.current||!selection||model_id===selection.model_id)return;
    writing.current=true;setSaving(true);setError('');
    try{
      const value=await qwenRequest('selection',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({model_id,revision:selection.revision})});
      if(alive.current)setSelection(current=>newerSelection(current,value));
    }catch(e){if(alive.current){setError(e.name==='AbortError'?'No se confirmó el cambio. Comprobando la selección guardada…':e.message);if(e.selection)setSelection(current=>newerSelection(current,e.selection));}await refresh(false);}
    finally{writing.current=false;if(alive.current)setSaving(false);}
  }
  return <Context.Provider value={{catalog,selection,error,saving,select,refresh:()=>{setError('');return refresh();}}}>{children}</Context.Provider>;
}

const reasons={model_missing:'Modelo no instalado',runtime_unavailable:'Ollama no está disponible',model_changed:'El modelo cambió; requiere verificación',runtime_changed:'Ollama cambió; requiere verificación',model_metadata_unavailable:'No se pudo verificar el modelo',capabilities_changed:'Capacidades distintas de las comprobadas',loaded_model_changed:'La copia cargada no coincide'};
export default function QwenModelSelector(){
  const box=useRef(null),[menuTop,setMenuTop]=useState(170);
  useEffect(()=>{
    const outside=event=>{if(box.current&&!box.current.contains(event.target))box.current.open=false;};
    const escape=event=>{if(event.key==='Escape'&&box.current?.open){box.current.open=false;box.current.querySelector('summary')?.focus();}};
    const reposition=()=>{if(box.current?.open)setMenuTop(box.current.querySelector('summary').getBoundingClientRect().bottom+8);};
    document.addEventListener('pointerdown',outside);document.addEventListener('keydown',escape);window.addEventListener('resize',reposition);
    return()=>{document.removeEventListener('pointerdown',outside);document.removeEventListener('keydown',escape);window.removeEventListener('resize',reposition);};
  },[]);
  const state=useQwenModels();if(!state)return null;
  const {catalog,selection,saving,error,select,refresh}=state;
  const model=catalog?.models.find(item=>item.id===selection?.model_id);
  return <details ref={box} className="qwen-model-selector" style={{'--qwen-menu-top':menuTop+'px'}} onToggle={event=>{if(event.currentTarget.open)setMenuTop(event.currentTarget.querySelector('summary').getBoundingClientRect().bottom+8);}}>
    <summary aria-label="Seleccionar modelo Qwen"><span className={'qwen-model-dot '+(model?.available?'ready':'')} aria-hidden="true"/>{selection?qwenLabel(selection.model_id):'Qwen · Conectando'}<span aria-hidden="true">⌄</span></summary>
    <div className="qwen-model-menu">
      <label htmlFor="qwen-model-choice">Qwen para nuevas tomas y análisis</label>
      <select id="qwen-model-choice" value={selection?.model_id||''} disabled={saving||!selection||!catalog} onChange={event=>select(event.target.value)}>
        {!selection&&<option value="">Conectando…</option>}
        {catalog?.models.map(item=><option key={item.id} value={item.id} disabled={!item.available}>{qwenLabel(item.id)} · 27B · {item.quantization}{!item.available?' · No disponible':''}</option>)}
      </select>
      <p className="qwen-model-status" role="status">{saving?'Guardando selección…':model?.available?'Disponible · Texto, imágenes y herramientas':reasons[model?.unavailable_reason]||'Comprobando disponibilidad…'}</p>
      <p>La selección se comparte entre tus dispositivos en este espacio. Cada toma conserva el modelo con el que se creó, también en su chat y reintentos.</p>
      {model&&<small>Ollama · {model.loaded?'Cargado en memoria':model.available?'Se carga al analizar':'Sin disponibilidad confirmada'}.<br/>Contexto configurado comprobado hasta {model.context.validated_setting_tokens.toLocaleString('es-MX')} tokens. Revisa las interpretaciones visuales.</small>}
      {error&&<p className="qwen-model-error" role="alert">{error}</p>}
      <button type="button" className="secondary" disabled={saving} onClick={refresh}>Comprobar disponibilidad</button>
    </div>
  </details>;
}
