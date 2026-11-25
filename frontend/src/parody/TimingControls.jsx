import React from 'react';
import {timingModePatch,timingDelaySamples} from './timing.js';

export default function TimingControls({row,take,capabilities,videoUrl,onChange}){
 const timing=row.timing,mode=timing?.mode||'none';
 const [width,height]=(take?.plan.resolution||'0x0').split('x').map(Number);
 if(!capabilities?.enabled&&!timing)return null;
 const oneVoice=row.voice_preview_ids.length===1;
 return <section aria-label="Ajuste de voz por plano" className="pm-trait">
  <label>Ajuste de voz<select value={mode} onChange={e=>onChange(timingModePatch(e.target.value,width,height))}>
   <option value="none">Conservar los tiempos del guion</option>
   <option value="auto" disabled={!oneVoice||!capabilities?.automatic||width<128||height<128}>Medir y ajustar con Qwen · experimental</option>
   <option value="manual" disabled={!oneVoice||!capabilities?.enabled}>Ajustar manualmente</option>
  </select></label>
  {!oneVoice&&<p>Selecciona una sola frase para ajustar este plano.</p>}
  {mode==='manual'&&<><label>Desplazamiento de voz (ms)<input type="number" min="-1000" max="1000" step="0.001" value={Math.round(timing.delay_samples/48*1000)/1000}
    onChange={e=>{const samples=timingDelaySamples(e.target.value);if(samples!==null)onChange({timing:{...timing,delay_samples:samples}});}}/></label>
    <p>Un valor positivo retrasa la voz; uno negativo la adelanta respecto al guion. La frase completa debe caber en el plano.</p></>}
  {mode==='auto'&&<>
   <p>Selecciona al personaje que habla. Las imágenes no se regeneran; se mide el desfase y se comprueba el ajuste. Si la evidencia no basta, el plano queda pendiente de revisión.</p>
   <div className="pm-roi-preview"><video aria-label="Seleccionar hablante para medir" controls playsInline preload="metadata" src={videoUrl}/>
    <div className="pm-roi-box" style={{left:timing.region.x/width*100+'%',top:timing.region.y/height*100+'%',width:timing.region.width/width*100+'%',height:timing.region.height/height*100+'%'}}/>
   </div>
   <div className="pm-script-grid">{['x','y','width','height'].map(k=><label key={k}>{({x:'Izquierda (px)',y:'Arriba (px)',width:'Ancho (px)',height:'Alto (px)'})[k]}
    <input type="number" step="1" min={k==='width'||k==='height'?128:0} value={timing.region[k]}
      onChange={e=>onChange({timing:{...timing,target_confirmed:false,region:{...timing.region,[k]:Number(e.target.value)}}})}/></label>)}</div>
   <label><input type="checkbox" checked={timing.target_confirmed} onChange={e=>onChange({timing:{...timing,target_confirmed:e.target.checked}})}/>Revisé que sólo el personaje que habla permanece dentro de esta región durante todo el plano.</label>
   <p>Prueba de investigación. Los casos sin calibración validada requieren revisión; no se habilita todavía para encargos comerciales.</p>
  </>}
 </section>;
}
