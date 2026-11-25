import React,{useState} from 'react';
import {reframePatch,validReframe} from './montage.js';

export default function ReframeControls({row,source,shot,onChange}){
 const [size,setSize]=useState(null),c=row.reframe,enabled=row.fit==='reframe';
 if(!source||!shot)return null;
 const start=shot.start_ms/1000+row.start_frame/24,end=shot.start_ms/1000+row.end_frame/24;
 const width=size?.[0]||c?.source_width||0,height=size?.[1]||c?.source_height||0;
 return <section aria-label="Reencuadre del plano original">
  <label><input type="checkbox" checked={enabled} disabled={!size&&!enabled} onChange={e=>onChange(reframePatch(e.target.checked,width,height))}/>Reencuadrar este plano</label>
  <p>Conserva la zona que aporta a la parodia. El recorte es fijo; revisa todo el intervalo para no cortar una reacción importante. El audio y la duración se conservan.</p>
  <div style={{position:'relative',width:'100%',maxWidth:640}}>
   <video key={source.asset_id+':'+start+':'+end} controls playsInline preload="metadata" style={{width:'100%',display:'block'}}
    src={'/api/assets/'+encodeURIComponent(source.asset_id)+'#t='+start+','+end}
    onLoadedMetadata={e=>{setSize([e.currentTarget.videoWidth,e.currentTarget.videoHeight]);e.currentTarget.currentTime=start;}}
    onTimeUpdate={e=>{if(e.currentTarget.currentTime>end)e.currentTarget.pause();}}/>
   {enabled&&validReframe(c)&&<div aria-label="Área que se conservará" style={{position:'absolute',pointerEvents:'none',boxSizing:'border-box',border:'2px solid #baff39',left:c.x/c.source_width*100+'%',top:c.y/c.source_height*100+'%',width:c.width/c.source_width*100+'%',height:c.height/c.source_height*100+'%'}}/>}
  </div>
  {size&&<p>Vídeo original: {width} × {height} píxeles. Intervalo: {start.toFixed(3)}–{end.toFixed(3)} s.</p>}
  {enabled&&<><p>El marco marca la zona conservada. Se ajustará a la resolución del montaje, con bandas si hace falta; ampliar no recupera detalle.</p>
   <div className="pm-script-grid">{['x','y','width','height'].map(k=><label key={k}>{({x:'Izquierda (px)',y:'Arriba (px)',width:'Ancho (px)',height:'Alto (px)'})[k]}
    <input type="number" step="1" min={k==='x'||k==='y'?0:16} max={k==='x'||k==='width'?width:height} value={c?.[k]??''} onChange={e=>onChange({reframe:{...c,[k]:e.target.value===''?null:Number(e.target.value)}})}/></label>)}</div>
   {!validReframe(c)&&<p role="alert">El rectángulo debe quedar dentro del vídeo y medir al menos 16 × 16 píxeles.</p>}
   <button type="button" onClick={()=>onChange(reframePatch(false))}>Restaurar encuadre completo</button>
  </>}
 </section>;
}
