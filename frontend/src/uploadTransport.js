// Same-origin multipart upload with real browser progress. Never retries a POST.
export function uploadWithProgress(data,{onProgress,signal,xhrFactory=()=>new XMLHttpRequest()}={}){
  return new Promise((resolve,reject)=>{
    const xhr=xhrFactory();let finished=false;
    const cleanup=()=>signal?.removeEventListener('abort',abort);
    const fail=error=>{if(finished)return;finished=true;cleanup();reject(error);};
    const abort=()=>{xhr.abort();fail(new Error('Carga cancelada. Si ya se había enviado, revisa la biblioteca antes de repetir.'));};
    xhr.open('POST','/api/assets',true);
    xhr.upload.onprogress=event=>{
      if(!finished)onProgress?.({phase:'sending',loaded:event.loaded,total:event.lengthComputable?event.total:null,updatedAt:Date.now()});
    };
    xhr.upload.onload=()=>{if(!finished)onProgress?.({phase:'verifying',updatedAt:Date.now()});};
    xhr.onload=()=>{
      if(finished)return;finished=true;cleanup();
      resolve({ok:xhr.status>=200&&xhr.status<300,status:xhr.status,json:async()=>JSON.parse(xhr.responseText)});
    };
    xhr.onerror=()=>fail(new Error('Se interrumpió la conexión durante la carga. Comprueba Tailscale y la conexión del celular.'));
    xhr.onabort=()=>fail(new Error('Carga cancelada. Si ya se había enviado, revisa la biblioteca antes de repetir.'));
    xhr.ontimeout=()=>fail(new Error('La carga no recibió respuesta a tiempo. Revisa la biblioteca antes de repetir.'));
    if(signal?.aborted){abort();return;}
    signal?.addEventListener('abort',abort,{once:true});
    onProgress?.({phase:'sending',loaded:0,total:null,updatedAt:Date.now()});
    try{xhr.send(data);}catch(error){fail(error);}
  });
}

export function uploadSummary(progress,now=Date.now()){
  const loaded=Math.min(progress.fileSize||Infinity,Math.max(0,progress.loaded||0));
  const fraction=progress.total>0?Math.min(1,(progress.loaded||0)/progress.total):null;
  const idle=Math.max(0,Math.floor((now-progress.updatedAt)/1000));
  return {loadedMB:(loaded/1e6).toFixed(1),totalMB:((progress.fileSize||0)/1e6).toFixed(1),
    percent:progress.phase==='verifying'?100:fraction===null?null:Math.floor(fraction*100),
    stalled:idle>=20,idle,verifying:progress.phase==='verifying'};
}
