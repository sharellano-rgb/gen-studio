import React,{useEffect,useState} from 'react';
import ParodyWorkspace from './ParodyWorkspace.jsx';
import {isParody,navigationEvent,openParody} from './navigation.js';

export function ParodyLauncher({disabled=false}){
 const [enabled,setEnabled]=useState(false);
 useEffect(()=>{let live=true;const controller=new AbortController();
  fetch('/api/parody/v1/capabilities',{signal:controller.signal,cache:'no-store'}).then(r=>r.ok?r.json():null).then(v=>{if(live)setEnabled(v?.module_enabled===true);}).catch(()=>{});
  return()=>{live=false;controller.abort();};
 },[]);
 if(!enabled)return null;
 return <button type="button" className="mode" disabled={disabled} onClick={openParody}><span className="hg-mode-copy"><strong>Parody Maker</strong><small>Preparar una parodia</small></span></button>;
}

export default function ParodyBoundary({workspace,children}){
 const [active,setActive]=useState(()=>isParody(location.href));
 useEffect(()=>{const update=()=>setActive(isParody(location.href));window.addEventListener('popstate',update);window.addEventListener(navigationEvent,update);
  return()=>{window.removeEventListener('popstate',update);window.removeEventListener(navigationEvent,update);};
 },[]);
 return active?<ParodyWorkspace key={workspace.id} workspace={workspace}/>:children;
}
