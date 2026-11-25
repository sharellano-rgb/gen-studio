let namespace='';
let workspaceId='owner';
export function configureWorkspaceStorage(profile){
  if(!profile||typeof profile.id!=='string'||!/^[a-z][a-z0-9_-]{0,39}$/.test(profile.id)||profile.storage_namespace!==(profile.id==='owner'?'':profile.id))throw Error('No se pudo verificar tu espacio.');
  namespace=profile.storage_namespace;workspaceId=profile.id;
}
export const activeWorkspaceId=()=>workspaceId;
export function createWorkspaceStorage(storage,space){
  const prefix=space?'gen-workspace:'+space+':':'';
  return {getItem:key=>storage.getItem(prefix+key),setItem:(key,value)=>storage.setItem(prefix+key,value),removeItem:key=>storage.removeItem(prefix+key)};
}
function scopedStorage(kind){return {getItem:key=>createWorkspaceStorage(window[kind],namespace).getItem(key),setItem:(key,value)=>createWorkspaceStorage(window[kind],namespace).setItem(key,value),removeItem:key=>createWorkspaceStorage(window[kind],namespace).removeItem(key)};}
export const workspaceLocalStorage=scopedStorage('localStorage');
export const workspaceSessionStorage=scopedStorage('sessionStorage');
