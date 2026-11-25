import React,{useRef,useState} from 'react';
import {createClient} from './api.js';

function AudioInterval({row,burst,listen,save,disabled}) {
 const event=burst||row;
 const pitch=(row.pitch_hz||row.pitch_hz_quintiles)?.map(hz=>hz??'—').join(' → ');
 const contour=pitch ? ` Tono aproximado por intervalos iguales entre ${row.start_ms/1000} y ${row.end_ms/1000} s: ${pitch} Hz.` : '';
 const [kind,setKind]=useState(burst?'laugh':'catchphrase');
 const [text,setText]=useState(burst?
  `Posible risita de ${event.count} pulsos, aproximadamente ${event.rate} por segundo${event.contour?', '+event.contour:''}. Revisar su intención en la escena.`:
  ((row.text ? `Dice «${row.text}».` : '')+contour).trim());
 const [start,setStart]=useState(String(event.start_ms/1000)),[end,setEnd]=useState(String(event.end_ms/1000));
 const a=Math.round(Number(start)*1000),b=Math.round(Number(end)*1000);
 const valid=start!==''&&end!==''&&a>=row.start_ms&&b<=row.end_ms&&b>a;
 return <details className="pm-trait"><summary>{burst?'Posible risa o sonido pulsado':row.text||'Sonido sin transcripción'} · {(event.start_ms/1000).toFixed(2)} s</summary>
  <div className="pm-character-grid"><label>Desde (s)<input type="number" step="0.01" value={start} onChange={e=>setStart(e.target.value)}/></label><label>Hasta (s)<input type="number" step="0.01" value={end} onChange={e=>setEnd(e.target.value)}/></label></div>
  <button type="button" disabled={!valid} onClick={()=>listen(a,b)}>Escuchar intervalo</button>
  <label>Rasgo<select value={kind} onChange={e=>setKind(e.target.value)}><option value="laugh">Risa</option><option value="catchphrase">Muletilla</option><option value="rhythm">Ritmo</option><option value="reaction">Reacción</option></select></label>
  <label>Descripción para Qwen<textarea maxLength={2000} value={text} onChange={e=>setText(e.target.value)}/></label>
  <button type="button" disabled={disabled||!valid||!text.trim()} onClick={()=>save({category:kind,text:text.trim(),kind:'observation',reviewed:true,start_ms:a,end_ms:b})}>Confirmar que es de este personaje</button>
 </details>;
}

export default function ActingAudio({workspace,project,reference,dirty,onTrait}){
 const [segments,setSegments]=useState(null),[busy,setBusy]=useState(false),[message,setMessage]=useState('');
 const player=useRef(null),stop=useRef(null);
 async function analyze(){setBusy(true);setMessage('');try{
  const data=await createClient(workspace.id).mutate('/projects/'+project.id+'/acting-audio/'+reference.id,'POST',{});
  if(data.sha256!==reference.sha256)throw Error('La referencia cambió. Recarga la ficha.');
  setSegments(data.segments);
 }catch(e){setMessage(e.message);}finally{setBusy(false);}}
 async function listen(a,b){try{const video=player.current;stop.current=b/1000;video.currentTime=a/1000;await video.play();}catch{setMessage('Abre el reproductor para escuchar este intervalo.');}}
 function save(trait){try{onTrait(trait);setMessage('Rasgo incorporado. Guarda el borrador.');}catch(e){setMessage(e.message);}}
 return <section><h4>Voz y sonidos del clip</h4>
 <button type="button" disabled={busy||dirty||project.archived} onClick={analyze}>{busy?'Analizando audio…':'Analizar audio del clip'}</button>
 {segments&&<><video className="pm-preview" ref={player} controls playsInline preload="metadata" src={'/api/assets/'+reference.asset_id} onTimeUpdate={()=>{const v=player.current;if(stop.current!==null&&v.currentTime>=stop.current){v.pause();stop.current=null;}}}/>
 <p>{segments.length?'Escucha antes de asignar. La transcripción es aproximada y los pulsos también pueden ser habla, música o golpes.':'No se detectaron intervalos audibles.'}</p>
 {segments.map(row=><React.Fragment key={row.start_ms}><AudioInterval row={row} listen={listen} save={save} disabled={project.archived}/>{row.burst&&<AudioInterval row={row} burst={row.burst} listen={listen} save={save} disabled={project.archived}/>}</React.Fragment>)}</>}
 {message&&<p role="status">{message}</p>}</section>;
}
