import React,{useEffect,useState} from 'react';
import {workspaceLocalStorage as storage} from './workspaceStorage.js';
import {DRAFT_EVENT,listStudioDrafts,draftTitle} from './studioDrafts.js';
import './studioDrafts.css';
export function DraftSaveStatus({status}){
 return status?<p className={'draft-save-status '+(status==='error'?'draft-save-error':'')} role={status==='error'?'alert':undefined}>{status==='error'?'No se pudo guardar el borrador en este navegador. Mantén abierta esta toma hasta liberar almacenamiento.':'Borrador guardado en este navegador · Sólo en tu espacio.'}</p>:null;
}
export default function DraftShelf({onResume,disabled=false}){
 const [drafts,setDrafts]=useState(()=>listStudioDrafts(storage)),[selected,setSelected]=useState('');
 useEffect(()=>{const refresh=()=>setDrafts(listStudioDrafts(storage));window.addEventListener(DRAFT_EVENT,refresh);window.addEventListener('storage',refresh);return()=>{window.removeEventListener(DRAFT_EVENT,refresh);window.removeEventListener('storage',refresh);};},[]);
 if(!drafts.length)return null;
 return <details className="draft-shelf"><summary>Borradores de este navegador <span>{drafts.length}</span></summary><p>Retoma tus ajustes. Cada toma conserva su propio borrador dentro de tu espacio.</p><div className="draft-shelf-controls"><label>Borrador guardado<select aria-label="Borrador guardado" value={selected} disabled={disabled} onChange={e=>setSelected(e.target.value)}><option value="">Selecciona un borrador</option>{drafts.map(d=><option key={d.id} value={d.id}>{d.engine==='bunny'?'Bunny':'H3 directo'} · {draftTitle(d)} · {new Date(d.updated_at).toLocaleString('es-MX',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'})}{d.job_id?' · '+d.job_id.slice(0,8):d.id.endsWith(':new')?' · Nueva toma':' · Nueva '+d.id.split(':').at(-1).slice(0,8)}</option>)}</select></label><button className="secondary" disabled={disabled||!drafts.some(d=>d.id===selected)} onClick={()=>onResume(drafts.find(d=>d.id===selected))}>Retomar borrador</button></div></details>;
}
