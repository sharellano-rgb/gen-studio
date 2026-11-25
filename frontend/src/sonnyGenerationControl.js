import {activeWorkspaceId} from './workspaceStorage.js';

const endpoint=targetId=>targetId==='sonny'?'/api/admin/sonny-generation-limit':`/api/admin/workspaces/${targetId}/generation-limit`;
export const canManageSonny=workspaceId=>workspaceId==='owner';

export function sonnyGenerationPolicy(value){
  if(typeof value?.enabled!=='boolean'||value.interval_seconds!==10800||value.effective_interval_seconds!==(value.enabled?10800:0))throw Error('No se pudo verificar el límite de generación.');
  return {enabled:value.enabled,interval_seconds:10800,effective_interval_seconds:value.effective_interval_seconds};
}

export async function sonnyGenerationRequest(enabled,options={}){
  const {signal,timeoutMs=8000,fetchImpl=fetch,workspaceId=activeWorkspaceId(),targetId='sonny'}=options;
  if(!/^[a-z][a-z0-9_-]{0,39}$/.test(targetId)||targetId==='owner')throw Error('Espacio no válido.');
  if(!canManageSonny(workspaceId)){const error=Error('Este control sólo está disponible para el propietario.');error.status=403;throw error;}
  if(enabled!==undefined&&typeof enabled!=='boolean')throw Error('El límite debe estar activado o desactivado.');
  if(signal?.aborted)throw new DOMException('Solicitud cancelada.','AbortError');
  const controller=new AbortController(),abort=()=>controller.abort(),timer=setTimeout(abort,timeoutMs);
  signal?.addEventListener('abort',abort,{once:true});
  try{
    const response=await fetchImpl(endpoint(targetId),{method:enabled===undefined?'GET':'PUT',cache:'no-store',credentials:'same-origin',signal:controller.signal,
      ...(enabled===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled})})});
    if(response.headers.get('X-Gen-Studio-Workspace')!=='owner'){
      const error=Error('No se pudo verificar el acceso del propietario.');error.status=403;throw error;
    }
    const value=await response.json();
    if(!response.ok){const error=Error(typeof value.detail==='string'?value.detail:'No se pudo actualizar el límite de generación.');error.status=response.status;throw error;}
    return sonnyGenerationPolicy(value);
  }finally{clearTimeout(timer);signal?.removeEventListener('abort',abort);}
}

// A lost PUT response may still mean the setting was saved. Read it back once;
// never repeat the mutation or assume that the previous state is still current.
export async function changeSonnyGenerationLimit(enabled,options={}){
  try{
    const policy=await sonnyGenerationRequest(enabled,options);
    return {policy,error:policy.enabled===enabled?'':'El servidor confirmó un estado diferente. Revisa el interruptor antes de volver a cambiarlo.'};
  }catch(error){
    if(options.signal?.aborted)throw error;
    if(error.status===403)return {policy:null,error:error.message};
    try{
      const policy=await sonnyGenerationRequest(undefined,options);
      return {policy,error:policy.enabled===enabled?'':'No se aplicó el cambio. Se muestra el estado actual del servidor.'};
    }catch{
      if(options.signal?.aborted)throw error;
      return {policy:null,error:'No se pudo confirmar el cambio. Comprueba el estado antes de volver a intentarlo.'};
    }
  }
}
