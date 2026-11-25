const methodNames={natural:'Natural · Lanczos'};

export function upscaleMethods(options){
  return (options?.methods||[]).filter(method=>Object.hasOwn(methodNames,method.id));
}

export function defaultUpscaleMethod(){
  return 'natural';
}

export function upscaleMethodLabel(value){
  return methodNames[value]||value||'Real-ESRGAN ×2';
}

export function canUpscale(options,method,{busy=false,sending=false}={}){
  return Boolean(options?.enabled&&upscaleMethods(options).find(item=>item.id===method)?.enabled&&!busy&&!sending);
}

// A transport error may follow a successful enqueue. Keep its key for retries,
// but never reuse that key for a different source.
export function createUpscaleRequest(makeKey=()=>crypto.randomUUID()){
  let target=null,key=null;
  function select(jobId){
    if(target!==jobId){target=jobId;key=null;}
  }
  return {
    select,
    body(jobId){
      select(jobId);
      if(!key)key=makeKey();
      return {request_key:key,method:'natural'};
    },
    complete(completedKey){if(key===completedKey)key=null;},
  };
}
