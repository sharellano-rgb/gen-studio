import React from 'react';

const items=[['studio','Studio'],['images','Imágenes'],['history','Historial'],['system','Sistema'],['workflow','Workflow']];
function NavIcon({name}){
  return <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    {name==='studio'&&<><rect x="3" y="5" width="18" height="14" rx="3"/><path d="m10 9 5 3-5 3Z"/></>}
    {name==='images'&&<><rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8" cy="8" r="1.5"/><path d="m3 17 5-5 4 4 4-6 5 7"/></>}
    {name==='history'&&<><path d="M3 8v4h4M3.7 8.5A9 9 0 1 1 4 16"/><path d="M12 7v5l3 2"/></>}
    {name==='system'&&<><path d="M3 6h6m4 0h8M3 12h12m4 0h2M3 18h3m4 0h11"/><circle cx="11" cy="6" r="2"/><circle cx="17" cy="12" r="2"/><circle cx="8" cy="18" r="2"/></>}
    {name==='workflow'&&<><rect x="8" y="3" width="8" height="5" rx="1.5"/><rect x="2" y="16" width="7" height="5" rx="1.5"/><rect x="15" y="16" width="7" height="5" rx="1.5"/><path d="M12 8v4M5.5 16v-4h13v4"/></>}
  </svg>;
}
export default function StudioNav({current,onNavigate,activeCount=0}){
  return <nav className="studio-nav" aria-label="Navegación principal">{items.map(([id,name])=><button type="button" key={id} className={current===id?'active':''} aria-label={name} aria-current={current===id?'page':undefined} aria-describedby={id==='history'&&activeCount>0?'studio-nav-active-jobs':undefined} onClick={()=>onNavigate(id)}>
    <span className="studio-nav-icon"><NavIcon name={id}/></span><span className="studio-nav-label">{name}</span>
    {id==='history'&&activeCount>0&&<small className="studio-nav-count" id="studio-nav-active-jobs">{activeCount}<span className="studio-nav-count-description"> trabajos en curso</span></small>}
  </button>)}</nav>;
}
