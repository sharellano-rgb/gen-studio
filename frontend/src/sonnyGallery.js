import {activeWorkspaceId} from './workspaceStorage.js';

export const galleryBase=(targetId,media)=>{if(!/^[a-z][a-z0-9_-]{0,39}$/.test(targetId)||targetId==='owner')throw Error('Espacio no válido.');return targetId==='sonny'?`/api/sonny-${media}`:`/api/admin/workspaces/${targetId}/${media}`;};
export const canReadSonnyGallery=workspaceId=>workspaceId==='owner';
export const sonnyGalleryMedia=value=>value==='images'?'images':'videos';

export function sonnyGalleryPage(value,media,targetId='sonny'){
 const kind=sonnyGalleryMedia(media),items=value?.[kind],next=value?.next_before;
 if(!Array.isArray(items)||(next!==null&&(!Number.isSafeInteger(next)||next<1)))throw Error('No se pudo verificar el historial solicitado.');
 const cleaned=items.map(item=>{
  if(!item||typeof item.id!=='string'||!/^[a-zA-Z0-9_-]+$/.test(item.id)||item.url!==`${galleryBase(targetId,kind)}/${item.id}/result`||typeof item.name!=='string'||typeof item.method!=='string'||typeof item.created_at!=='string'||!Number.isFinite(Date.parse(item.created_at))||(item.resolution!=null&&typeof item.resolution!=='string'))throw Error('No se pudo verificar un resultado del espacio solicitado.');
  return {id:item.id,name:item.name,method:item.method,created_at:item.created_at,resolution:item.resolution||'',url:item.url,...(kind==='videos'&&typeof item.seconds==='number'&&Number.isFinite(item.seconds)?{seconds:item.seconds}:{})};
 });
 return {items:cleaned,next_before:next};
}

export function mergeSonnyGalleryItems(current,incoming,append){
 return [...new Map([...(append?current:[]),...incoming].map(item=>[item.id,item])).values()];
}

export async function requestSonnyGallery(media,options={}){
 const {workspaceId=activeWorkspaceId(),before=null,signal,timeoutMs=15000,fetchImpl=fetch,targetId='sonny'}=options;
 if(!canReadSonnyGallery(workspaceId)){const error=Error('Este acceso está reservado para la cuenta del propietario.');error.status=403;throw error;}
 if(before!==null&&(!Number.isSafeInteger(before)||before<1))throw Error('La página del historial no es válida.');
 if(signal?.aborted)throw new DOMException('Solicitud cancelada.','AbortError');
 const kind=sonnyGalleryMedia(media),controller=new AbortController(),abort=()=>controller.abort(),timer=setTimeout(abort,timeoutMs);
 signal?.addEventListener('abort',abort,{once:true});
 try{
  const response=await fetchImpl(galleryBase(targetId,kind)+(before===null?'':'?before='+before),{method:'GET',cache:'no-store',credentials:'same-origin',signal:controller.signal});
  if(controller.signal.aborted)throw new DOMException('Solicitud cancelada.','AbortError');
  if(response.headers.get('X-Gen-Studio-Workspace')!=='owner'){const error=Error('No se pudo verificar el acceso del propietario.');error.status=403;throw error;}
  const data=await response.json();
  if(controller.signal.aborted)throw new DOMException('Solicitud cancelada.','AbortError');
  if(!response.ok){const error=Error(typeof data.detail==='string'?data.detail:'No se pudo cargar el historial solicitado.');error.status=response.status;throw error;}
  return sonnyGalleryPage(data,kind,targetId);
 }finally{clearTimeout(timer);signal?.removeEventListener('abort',abort);}
}
