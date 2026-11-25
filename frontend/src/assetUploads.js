import {uploadWithProgress} from './uploadTransport.js';

const extensions={video:['mp4','mov','webm'],image:['png','jpg','jpeg','webp']};
export async function uploadAsset(file,kind,fetcher=fetch,options={}){
  if(!extensions[kind]?.includes(file.name.split('.').at(-1).toLowerCase()))
    throw Error(kind==='video'?'Usa un vídeo MP4, MOV o WebM.':'Usa una imagen PNG, JPG o WebP.');
  const max=(kind==='video'?200:25)*1024**2;
  if(file.size>max)throw Error(`La carga supera ${max/1024**2} MB.`);
  if(!file.size)throw Error('El archivo está vacío. Selecciona otro archivo.');
  const data=new FormData();data.append('file',file);
  const response=options.onProgress&&fetcher===fetch&&typeof XMLHttpRequest!=='undefined'
    ?await uploadWithProgress(data,options)
    :await fetcher('/api/assets',{method:'POST',body:data,...(options.signal?{signal:options.signal}:{})});
  let asset;try{asset=await response.json();}catch{throw Error(`No se pudo cargar el archivo (HTTP ${response.status}).`);}
  if(!response.ok)throw Error(typeof asset.detail==='string'?asset.detail:`No se pudo cargar el archivo (HTTP ${response.status}).`);
  if(asset.kind!==kind||!asset.id)throw Error('El archivo no coincide con el tipo de entrada.');
  return asset;
}
