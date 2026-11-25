import React,{useEffect,useRef,useState} from 'react';
import {resultUrl} from './resultVersion.js';

function previewSources(job,assets) {
  const sources=[],parameters=job?.parameters||{};
  if(job?.id&&job.status==='completed'&&job.result_available)
    sources.push({url:resultUrl(job).replace('/result','/thumbnail'),type:'image',kind:'Resultado'},
      {url:resultUrl(job)+'#t=0.001',type:'video',kind:'Resultado'});
  const video=assets.find(asset=>asset.id===parameters.video_id&&asset.kind==='video');
  if(video)sources.push({url:'/api/assets/'+encodeURIComponent(video.id)+'#t=0.001',type:'video',kind:'Original'});
  const referenceIds=[parameters.reference_id,...(Array.isArray(parameters.direct_reference_ids)?parameters.direct_reference_ids:[]),parameters.face_reference_id,parameters.h3_face_reference_id];
  const reference=referenceIds.map(id=>id&&assets.find(asset=>asset.id===id&&asset.kind==='image')).find(Boolean);
  if(reference)sources.push({url:'/api/assets/'+encodeURIComponent(reference.id),type:'image',kind:'Referencia'});
  return sources;
}

export default function HistoryThumbnail({job,assets=[],label='Vista previa'}) {
  const container=useRef(null);
  const [visible,setVisible]=useState(false),[failed,setFailed]=useState({}),[loaded,setLoaded]=useState('');
  const source=previewSources(job,assets).find(item=>!failed[item.url]);
  useEffect(()=>{
    if(visible)return;
    if(typeof IntersectionObserver==='undefined'){setVisible(true);return;}
    const observer=new IntersectionObserver(entries=>{
      if(entries.some(entry=>entry.isIntersecting)){setVisible(true);observer.disconnect();}
    },{rootMargin:'200px'});
    if(container.current)observer.observe(container.current);
    return ()=>observer.disconnect();
  },[visible]);
  const ready=source&&loaded===source.url;
  const placeholder=!source?'Sin vista previa':visible?'Cargando vista previa…':'Vista previa';
  const description=label+(source?' · '+source.kind:' · Sin vista previa')+(source&&visible&&!ready?' · Cargando vista previa':'');
  const mediaProps=source?{className:'history-thumbnail-media',src:source.url,'aria-hidden':true,onError:()=>setFailed(current=>({...current,[source.url]:true}))}:{};
  return <span ref={container} className="history-thumbnail" role="img" aria-label={description}>
    {visible&&source&&(source.type==='video'
      ?<video key={source.url} {...mediaProps} muted playsInline preload="metadata" tabIndex={-1} onLoadedData={()=>setLoaded(source.url)}/>
      :<img key={source.url} {...mediaProps} alt="" loading="lazy" decoding="async" onLoad={()=>setLoaded(source.url)}/>)}
    {(!visible||!ready)&&<span className="history-thumbnail-placeholder" aria-hidden="true">{placeholder}</span>}
    {source&&<span className="history-thumbnail-kind" aria-hidden="true">{source.kind}</span>}
  </span>;
}
