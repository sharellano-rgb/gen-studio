import React,{useMemo,useState} from 'react';

const dateFormat=new Intl.DateTimeFormat('es-MX',{day:'numeric',month:'short',year:'numeric'});
const normalize=text=>text.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLocaleLowerCase('es');
const modified=p=>Date.parse(p.updated_at)||Date.parse(p.created_at)||0;
const PAGE_SIZE=6;

function ProjectCard({project,onOpen,busy}){
 const timestamp=modified(project);
 const names=project.cast?.characters?.map(c=>c.name).filter(Boolean)||[];
 return <li><button type="button" className="pm-project-card" disabled={busy} onClick={()=>onOpen(project.id)}>
  <span className="pm-project-state">{project.archived?'Archivado':'Borrador'}<span aria-hidden="true">↗</span></span>
  <strong>{project.name}</strong>
  {names.length>0&&<span className="pm-project-cast">{names.join(' · ')}</span>}
  <small>{timestamp?dateFormat.format(timestamp)+' · ':''}Revisión {project.revision}</small>
 </button></li>;
}

export default function ProjectHome({projects,busy,onOpen,onCreate}){
 const [name,setName]=useState(''),[history,setHistory]=useState(false);
 const [query,setQuery]=useState(''),[page,setPage]=useState(0);
 const ordered=useMemo(()=>[...projects].sort((a,b)=>modified(b)-modified(a)||a.name.localeCompare(b.name,'es')),[projects]);
 const recent=ordered.filter(p=>!p.archived).slice(0,3);
 const matches=history?ordered.filter(p=>normalize(p.name).includes(normalize(query.trim()))):recent;
 const pages=Math.ceil(matches.length/PAGE_SIZE),current=Math.min(page,Math.max(0,pages-1));
 const shown=history?matches.slice(current*PAGE_SIZE,(current+1)*PAGE_SIZE):recent;
 return <div className="pm-home">
  <section className="pm-new-project">
   <h2>Crear una parodia</h2>
   <form onSubmit={e=>{e.preventDefault();if(name.trim())onCreate(name.trim());}}>
    <label>Nombre del proyecto<input value={name} onChange={e=>setName(e.target.value)} required maxLength={120} placeholder="Nombre de tu parodia" disabled={busy}/></label>
    <button type="submit" className="primary" disabled={busy||!name.trim()}>Crear proyecto</button>
   </form>
  </section>
  <section className="pm-library" aria-label={history?'Historial de proyectos':'Proyectos recientes'}>
   <div className="pm-library-title"><h2>{history?'Historial de proyectos':'Proyectos recientes'}</h2>
   {(projects.length>0||history)&&<button type="button" onClick={()=>{setHistory(!history);setQuery('');setPage(0);}}>{history?'Volver a recientes':'Ver historial ('+projects.length+')'}</button>}</div>
   {history&&<label>Buscar por nombre<input type="search" value={query} placeholder="Buscar proyecto" onChange={e=>{setQuery(e.target.value);setPage(0);}}/></label>}
   {shown.length>0?<ul className="pm-library-cards">{shown.map(p=><ProjectCard key={p.id} project={p} busy={busy} onOpen={onOpen}/>)}</ul>:<p role="status">{busy?'Cargando proyectos…':history&&query?'Ningún proyecto coincide con tu búsqueda.':projects.length?'Tus proyectos archivados están en el historial.':'Aquí aparecerán tus proyectos cuando crees la primera parodia.'}</p>}
   {history&&pages>1&&<nav aria-label="Páginas del historial" className="pm-library-pages"><button disabled={current===0} onClick={()=>setPage(current-1)}>Anterior</button><span>{current+1} de {pages}</span><button disabled={current+1===pages} onClick={()=>setPage(current+1)}>Siguiente</button></nav>}
  </section>
 </div>;
}
