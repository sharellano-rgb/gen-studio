import {PIKA_DETAILED,PIKA_DETAIL_LABEL} from './pikaRefMethods.js';
import {VISUAL_ENGINES} from './secoursesVisualMethods.js';
import React,{useCallback,useEffect,useId,useMemo,useRef,useState} from 'react';
import {groupTakes,takeLabel,refinementStatus} from './sceneResults.js';
import {resultUrl} from './resultVersion.js';
import {activeObjectSwap} from './engineSelection.js';
import {repeatable} from './reuseTake.js';
import {sceneName,sceneChosenId,sceneRepresentative,sceneMethods,filterScenes,sceneDuration,sceneResolution} from './historyLibrary.js';
import {workspaceLocalStorage,activeWorkspaceId} from './workspaceStorage.js';
import JobCancelButton from './JobCancelButton.jsx';
import HistoryThumbnail from './HistoryThumbnail.jsx';
import CinemaPresentation from './CinemaPresentation.jsx';
import './sceneResults.css';
import './sceneHistory.css';
import {readHistoryFilters,saveHistoryFilters} from './historyPreferences.js';

const labels={completed:'Terminado',failed:'Error',cancelled:'Detenido',cancelling:'Cancelando',submission_unknown:'Envío sin confirmar',queued:'En cola'};
const methods={viggle_remake:'Viggle Remake',tessera:'Video Upscaling',[PIKA_DETAILED]:PIKA_DETAIL_LABEL,pika_ref2va:'Pika · Object Swap / Motion Transfer',h3_i2va:'H3 directo · Imagen a vídeo',pika_h3_flex:'Pika H3 Flex',h3_direct:'H3 directo',bunny:'Bunny',tara:'TARA-H3',motion_layers:'Personaje + Escenario',h3_secourses:'SECourses',...Object.fromEntries(VISUAL_ENGINES.map(method=>[method.id,method.label])),h3_alisson:'Alisson',h3_ninja:'Refinamiento facial',archive:'Métodos anteriores'};
const statusNames={completed:'Terminadas',active:'En proceso',failed:'Con errores',cancelled:'Detenidas',chosen:'Con resultado elegido'};
const terminal=new Set(['completed','failed','cancelled','submission_unknown']);
const mergeById=(previous,incoming)=>[...new Map([...previous,...incoming].map(item=>[item.id,item])).values()];
function verifyWorkspace(response){
  const workspace=response.headers.get('X-Gen-Studio-Workspace');
  if(workspace&&workspace!==activeWorkspaceId()){window.location.reload();throw Error('Actualizando tu espacio de trabajo…');}
}
async function responseData(response){
  verifyWorkspace(response);
  const data=await response.json();
  if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'No se pudieron guardar los cambios.');
  return data;
}
const dateLabel=value=>{const date=new Date(value);return Number.isNaN(date.getTime())?'':date.toLocaleDateString('es-MX',{day:'numeric',month:'short',year:'numeric'});};

export default function SceneHistory({jobs,assets,busy,onOpen,onReuse,onCancel,active=true,currentJobId}) {
  const [cinema,setCinema]=useState(null);
  useEffect(()=>{if(!active)setCinema(null);},[active]);
  const [selected,setSelected]=useState(()=>{try{return JSON.parse(workspaceLocalStorage.getItem('gen-selected-versions'))||{};}catch{return {};}});
  const [archive,setArchive]=useState([]),[archiveAssets,setArchiveAssets]=useState([]),[metadata,setMetadata]=useState({});
  const [cursor,setCursor]=useState(null),[loading,setLoading]=useState(false),[error,setError]=useState(''),[ready,setReady]=useState(false);
  const [savedFilters]=useState(()=>readHistoryFilters(workspaceLocalStorage));
  const [query,setQuery]=useState(savedFilters.query),[method,setMethod]=useState(savedFilters.method),[status,setStatus]=useState(savedFilters.status);
  const [filtersOpen,setFiltersOpen]=useState(savedFilters.method!=='all'||savedFilters.status!=='all'),filterId=useId();
  const filterCount=Number(method!=='all')+Number(status!=='all');
  useEffect(()=>{saveHistoryFilters(workspaceLocalStorage,{query,method,status});},[query,method,status]);
  const [editing,setEditing]=useState(null),[draft,setDraft]=useState(''),[saving,setSaving]=useState(null),[saveError,setSaveError]=useState(null);
  const request=useRef(null),mutation=useRef(null),mounted=useRef(false);
  const load=useCallback(async (before,{refresh=false}={})=>{
    if(request.current)return;
    const controller=new AbortController();request.current=controller;setLoading(true);
    try{
      const r=await fetch('/api/scene-history'+(before?'?before='+before:''),{signal:controller.signal});
      const d=await responseData(r);if(!mounted.current)return;
      setArchive(current=>mergeById(current,[...(d.ancestor_jobs||[]),...(d.selected_jobs||[]),...(d.jobs||[])]));
      setArchiveAssets(current=>mergeById(current,d.assets||[]));
      setMetadata(current=>({...current,...d.scene_metadata}));
      if(!refresh)setCursor(d.next_before&&(!before||d.next_before<before)?d.next_before:null);
      setError('');setReady(true);
    }catch(e){if(e.name!=='AbortError'&&mounted.current)setError(e.message);}
    finally{if(request.current===controller)request.current=null;if(mounted.current)setLoading(false);}
  },[]);
  useEffect(()=>{mounted.current=true;return ()=>{mounted.current=false;request.current?.abort();mutation.current?.abort();};},[]);
  // Refresh recent names/status on return, retaining older pages and their cursor.
  useEffect(()=>{if(active)load(undefined,{refresh:ready});},[active,load]);
  const allAssets=useMemo(()=>mergeById(archiveAssets,assets),[archiveAssets,assets]);
  const groups=useMemo(()=>groupTakes(mergeById(archive,jobs)),[archive,jobs]);
  const filtersActive=Boolean(query.trim()||method!=='all'||status!=='all');
  const visible=useMemo(()=>filterScenes(groups,{query,method,status},allAssets,metadata,selected),[groups,query,method,status,allAssets,metadata,selected]);
  // Search continues through older pages, without downloading their result videos.
  useEffect(()=>{
    if(!active||!filtersActive||!cursor||loading||error)return;
    const timer=setTimeout(()=>load(cursor),250);return ()=>clearTimeout(timer);
  },[active,filtersActive,cursor,loading,error,load]);
  async function save(group,changes){
    if(mutation.current)return false;
    const controller=new AbortController();mutation.current=controller;setSaving(group.id);setSaveError(null);
    try{
      const d=await responseData(await fetch('/api/scenes/'+encodeURIComponent(group.id),{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(changes),signal:controller.signal}));
      if(!mounted.current)return false;
      setMetadata(current=>({...current,[d.scene_id]:{name:d.name,selected_job_id:d.selected_job_id}}));
      setSelected(current=>{const next={...current,[d.scene_id]:d.selected_job_id};try{workspaceLocalStorage.setItem('gen-selected-versions',JSON.stringify(next));}catch{}return next;});
      return true;
    }catch(e){if(e.name!=='AbortError'&&mounted.current)setSaveError({id:group.id,message:e.message});return false;}
    finally{if(mutation.current===controller)mutation.current=null;if(mounted.current)setSaving(null);}
  }
  async function rename(event,group){
    event.preventDefault();
    const changes={name:draft};
    if(!Object.hasOwn(metadata[group.id]||{},'selected_job_id')){
      const chosen=sceneChosenId(group,metadata,selected);if(chosen)changes.selected_job_id=chosen;
    }
    if(await save(group,changes))setEditing(null);
  }
  return <section className="scene-library" hidden={!active} aria-labelledby="scene-library-title">
    <div className="library-heading"><div><h2 id="scene-library-title">Tus escenas</h2><p>Encuentra una toma, revisa sus versiones y elige tu resultado favorito.</p></div><span className="library-count">{visible.length} {visible.length===1?'escena':'escenas'}{filtersActive?' encontrada'+(visible.length===1?'':'s'):''}{cursor?' · cargadas':''}</span></div>
    <div className="library-filters" role="search" aria-label="Buscar en el historial">
      <div className="library-search-row">
        <label className="library-search">Buscar<input type="search" maxLength={200} value={query} onChange={e=>setQuery(e.target.value)} placeholder="Nombre del vídeo o escena"/></label>
        <button type="button" className="secondary library-filter-toggle" aria-label="Filtros del historial" aria-expanded={filtersOpen} aria-controls={filterId} onClick={()=>setFiltersOpen(open=>!open)}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" aria-hidden="true"><path d="M3 6h6m4 0h8M3 12h12m4 0h2M3 18h3m4 0h11"/><circle cx="11" cy="6" r="2"/><circle cx="17" cy="12" r="2"/><circle cx="8" cy="18" r="2"/></svg>
          Filtros{filterCount>0&&<span aria-hidden="true">{filterCount}</span>}
        </button>
      </div>
      <div className="library-filter-fields" id={filterId} hidden={!filtersOpen}>
        <label>Método<select value={method} onChange={e=>setMethod(e.target.value)}><option value="all">Todos</option>{Object.entries(methods).map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>
        <label>Estado<select value={status} onChange={e=>setStatus(e.target.value)}><option value="all">Todos</option>{Object.entries(statusNames).map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>
      </div>
      {filtersActive&&<div className="library-filter-summary"><div aria-label="Filtros activos">{method!=='all'&&<span>{methods[method]}</span>}{status!=='all'&&<span>{statusNames[status]}</span>}</div><button type="button" className="secondary library-clear" onClick={()=>{setQuery('');setMethod('all');setStatus('all');}}>Limpiar filtros</button></div>}
    </div>
    {filtersActive&&cursor&&!error&&<p className="library-loading" role="status">Buscando también en escenas anteriores…</p>}
    {!visible.length&&<div className="library-empty">{loading||!ready&&!error?'Cargando tus escenas…':filtersActive?(cursor?'Buscando coincidencias en escenas anteriores…':'No encontramos escenas con estos filtros.'):'Aún no hay escenas. Crea tu primer vídeo desde Studio.'}</div>}
    <div className="library-grid">
      {visible.map(group=>{
        const item=sceneRepresentative(group,metadata,selected);if(!item)return null;
        const name=sceneName(group,allAssets,metadata),chosen=sceneChosenId(group,metadata,selected);
        const info=[sceneDuration(item),sceneResolution(item)].filter(Boolean).join(' · ');
        const otherActive=group.jobs.some(job=>job.id!==item.id&&!terminal.has(job.status));
        const otherError=group.jobs.some(job=>job.id!==item.id&&['failed','submission_unknown'].includes(job.status));
        return <article className={'library-card'+(chosen?' library-card-chosen':'')} key={group.id} aria-label={name} data-current-scene={group.jobs.some(version=>version.id===currentJobId)||undefined}>
          <button className="library-preview" onClick={()=>onOpen(item.id,{name,job:item,assets:allAssets})} aria-label={'Abrir '+name}><HistoryThumbnail job={item} assets={allAssets} label={name}/><span className={'library-status library-status-'+item.status}>{labels[item.status]||'En proceso'}</span><span className="library-preview-open">Abrir toma ↗</span></button>
          <div className="library-card-body">
            {editing===group.id?<form className="library-rename" onSubmit={event=>rename(event,group)}><label>Nombre de la escena<input autoFocus maxLength={100} value={draft} onChange={e=>setDraft(e.target.value)} placeholder="Usar nombre del vídeo" disabled={Boolean(saving)}/></label><div><button type="submit" className="secondary" disabled={Boolean(saving)}>{saving===group.id?'Guardando…':'Guardar nombre'}</button><button type="button" className="secondary" onClick={()=>setEditing(null)} disabled={Boolean(saving)}>Cancelar</button></div></form>:<div className="library-name"><h3>{name}</h3><button className="library-rename-button" aria-label={'Renombrar '+name} onClick={()=>{setEditing(group.id);setDraft(name);setSaveError(null);}} disabled={Boolean(saving)} title="Renombrar escena"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m14 5 5 5M4 20l4-1L20 7a2.8 2.8 0 0 0-4-4L4 15Z"/></svg></button></div>}
            <div className="library-methods">{sceneMethods(group).map(id=><span key={id}>{methods[id]}</span>)}{chosen&&<span className="library-chosen">★ Resultado elegido</span>}</div>
            <p className="library-card-info">{info||takeLabel(item)}<span>{dateLabel(item.created_at)}</span></p>
            {item.status==='completed'&&item.result_available&&<button type="button" className="cinema-launch library-cinema" onClick={()=>setCinema({job:item,assets:allAssets})} aria-label={'Ver en Cine · '+name}>Ver en Cine <span aria-hidden="true">↗</span></button>}
            {(otherActive||otherError)&&<div className="library-statuses">{otherActive&&<span>Otra versión en proceso</span>}{otherError&&<span>Contiene una versión con error</span>}</div>}
            {saveError?.id===group.id&&<p role="alert" className="error">{saveError.message}</p>}
          </div>
          <details className="library-versions"><summary>Ver versiones <span>{group.jobs.length}{cursor?(group.jobs.length===1?' cargada':' cargadas'):''}</span></summary>
            {[...group.jobs].sort((a,b)=>Date.parse(b.created_at)-Date.parse(a.created_at)).map(version=><article className={'scene-version '+(chosen===version.id?'chosen-version':'')} key={version.id} data-current-version={version.id===currentJobId||undefined}>
              {version.id===currentJobId&&<span className="library-current">Toma abierta en Studio</span>}
              <div className="history-heading"><strong>{takeLabel(version)}{chosen===version.id?' · Elegida':''}</strong><span>{labels[version.status]||'En proceso'}</span></div>
              <small>{dateLabel(version.created_at)} · {version.id.slice(0,8)}</small><p>{version.stage}</p>
              {version.result_available&&<small>{refinementStatus(version)}</small>}{version.error&&<p role="alert" className="error">{version.error}</p>}
              <div className="history-actions">
                <button className="secondary" onClick={()=>onOpen(version.id,{name,job:version,assets:allAssets})} aria-current={version.id===currentJobId?true:undefined}>Abrir</button>
                {activeObjectSwap(version.parameters)&&repeatable(version)&&<button className="secondary" disabled={busy} onClick={()=>onReuse(version,allAssets,mergeById(archive,jobs))}>{version.parameters.direct_test?'Recuperar configuración completa':'Reutilizar configuración'}</button>}
                {version.status==='completed'&&version.result_available&&<><button className="secondary" disabled={Boolean(saving)||version.parameters.tessera_segment_index!=null} title={version.parameters.tessera_segment_index!=null?'Incorpora el tramo a un nuevo montaje antes de elegirlo.':undefined} onClick={()=>save(group,{selected_job_id:chosen===version.id?null:version.id})} aria-pressed={chosen===version.id}>{saving===group.id?'Guardando…':chosen===version.id?'Quitar elección':'Elegir resultado'}</button><a className="secondary" href={resultUrl(version,{download:true})}>Descargar</a></>}
                {version.status==='completed'&&version.result_available&&<button type="button" className="secondary cinema-launch" onClick={()=>setCinema({job:version,assets:allAssets})} aria-label={'Ver en Cine · '+takeLabel(version)}>Ver en Cine <span aria-hidden="true">↗</span></button>}
                <JobCancelButton job={version} onCancel={onCancel}/>
              </div>
              <details><summary>Detalles del trabajo</summary><small>{version.id}</small>{(version.vram_events||[]).map((event,index)=><p key={index}>{event}</p>)}</details>
            </article>)}
          </details>
        </article>;
      })}
    </div>
    <div className="library-footer">{error&&<p role="alert" className="error">{error}</p>}{(cursor||error)&&<button className="secondary" disabled={loading} onClick={()=>load(cursor)}>{loading?'Cargando escenas…':error?'Reintentar cargar historial':'Cargar escenas anteriores'}</button>}{ready&&<p>Los nombres y la versión elegida se guardan en tu espacio privado. Todas las versiones originales se conservan.</p>}</div>
    {active&&cinema&&<CinemaPresentation key={cinema.job.id} job={cinema.job} assets={cinema.assets} method={takeLabel(cinema.job)} onClose={()=>setCinema(null)}/>}
  </section>;
}
