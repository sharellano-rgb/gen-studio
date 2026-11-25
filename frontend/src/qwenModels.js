import {activeWorkspaceId} from './workspaceStorage.js';

export const ORIGINAL_QWEN='qwen3.8:27b';
export function qwenLabel(id){return id===ORIGINAL_QWEN?'Qwen Original':id==='qwen38-27b-hauhau-aggressive:latest'?'Qwen Aggressive':'Qwen';}
export function newerSelection(current,incoming){return !current||incoming.revision>=current.revision?incoming:current;}
export async function qwenRequest(path,options={}){
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
  try{
    const response=await fetch('/api/qwen/'+path,{cache:'no-store',...options,signal:controller.signal});
    const space=response.headers.get('X-Gen-Studio-Workspace');
    if(space&&space!==activeWorkspaceId())throw Error('El espacio cambió. Recarga la página para continuar.');
    const data=await response.json();
    if(!response.ok){const error=Error(data.detail?.message||data.detail||'No se pudo consultar Qwen.');error.selection=data.detail?.selection;throw error;}
    return data;
  }finally{clearTimeout(timer);}
}
