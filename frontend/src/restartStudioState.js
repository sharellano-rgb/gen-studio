export function restartOutcome(status,beforeBoot,operationId,previousOperationId){
  const operation=status?.operation;
  if(operationId&&operation?.id!==operationId)return 'waiting';
  if(!operationId&&(!operation?.id||operation.id===previousOperationId))return 'waiting';
  if(operation?.state==='failed')return 'failed';
  if(operation?.state==='completed'&&beforeBoot&&status.boot_id&&status.boot_id!==beforeBoot)return 'completed';
  return 'waiting';
}
export async function restartJson(path,options={},timeoutMs=4000){
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),timeoutMs);
  try{
    const response=await fetch(path,{cache:'no-store',...options,signal:controller.signal});
    const data=await response.json();
    if(!response.ok){const error=Error(typeof data.detail==='string'?data.detail:'No se pudo solicitar el reinicio.');error.status=response.status;throw error;}
    return data;
  }finally{clearTimeout(timer);}
}
