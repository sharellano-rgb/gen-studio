export function createClient(workspace,fetcher=fetch){
 let pending=null;
 async function request(path,options={}){
  const response=await fetcher('/api/parody/v1'+path,{cache:'no-store',...options});
  const scope=response.headers.get('X-Gen-Studio-Workspace');
  if(scope!==workspace)throw Error('Cambió el espacio o no se pudo verificar. Recarga GEN STUDIO.');
  const data=await response.json();
  if(!response.ok){const error=Error(data.detail?.message||data.error?.message||(typeof data.detail==='string'?data.detail:`No se pudo completar la operación (${response.status}).`));error.status=response.status;throw error;}
  return data;
 }
 return {get:request,async mutate(path,method,body){
  const serialized=JSON.stringify(body),signature=method+path+serialized;
  if(pending?.signature!==signature)pending={signature,key:crypto.randomUUID()};
  const key=pending.key;
  const result=await request(path,{method,headers:{'Content-Type':'application/json','Idempotency-Key':key},body:serialized});
  if(pending?.key===key)pending=null;
  return result;
 }};
}
