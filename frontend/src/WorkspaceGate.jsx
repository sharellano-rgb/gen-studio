import React,{useEffect,useState} from 'react';
import SonnyGallery from './SonnyGallery.jsx';
import {configureWorkspaceStorage} from './workspaceStorage.js';

export default function WorkspaceGate({children}){
  const [profile,setProfile]=useState(null),[error,setError]=useState(''),[retry,setRetry]=useState(0);
  useEffect(()=>{
    let live=true;const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);
    fetch('/api/workspace',{cache:'no-store',signal:controller.signal}).then(async response=>{
      const data=await response.json();
      if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'No se pudo abrir tu espacio.');
      if(live){configureWorkspaceStorage(data);document.title='GEN STUDIO · '+data.name;setProfile(data);}
    }).catch(e=>{if(live)setError(e.name==='AbortError'?'Gen Studio no respondió a tiempo. Vuelve a comprobar la conexión.':e.message);}).finally(()=>clearTimeout(timer));
    return()=>{live=false;clearTimeout(timer);controller.abort();};
  },[retry]);
  if(!profile)return <main className="workspace-gate"><section className="panel"><p className="tiny">GEN STUDIO · ESPACIO PRIVADO</p><h1>{error?'No se pudo abrir tu espacio':'Abriendo tu espacio'}</h1><p role={error?'alert':'status'}>{error||'Comprobando tu acceso…'}</p>{error&&<><p>Conéctate a Tailscale con tu cuenta autorizada y vuelve a intentar.</p><button className="secondary" onClick={()=>{setError('');setRetry(value=>value+1);}}>Volver a intentar</button></>}</section></main>;
  const query=new URLSearchParams(window.location.search);
  if(query.get('view')==='sonny')return <SonnyGallery workspace={profile} media={query.get('media')}/>;
  if(query.get('view')==='jafet')return <SonnyGallery workspace={profile} media={query.get('media')} targetId="jafet" targetName="Jafet"/>;
  return children(profile);
}
