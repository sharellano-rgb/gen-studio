import React,{useEffect,useRef,useState} from 'react';
import {pointInImage,selectionMatches} from './visualTargets.js';
import {workspaceSessionStorage as sessionStorage} from './workspaceStorage.js';
async function api(path,body){const r=await fetch('/api/direct/'+path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:undefined);const d=await r.json();if(!r.ok){const e=Error(typeof d.detail==='string'?d.detail:'No se pudo analizar la persona marcada.');e.status=r.status;throw e;}return d;}
export default function VisualTargetPicker({videoId,startFrame,frames,characters,disabled,onAssign,onActivity,label='Seleccionar persona en el vídeo'}){
  const activeStorageKey='gen-visual-character:'+JSON.stringify([videoId,startFrame,frames]);
  const [activeId,setActiveId]=useState(()=>{try{return sessionStorage.getItem(activeStorageKey)||characters[0]?.id||'';}catch{return characters[0]?.id||'';}}),[frame,setFrame]=useState(startFrame),[previewFrame,setPreviewFrame]=useState(startFrame),[point,setPoint]=useState(null),[record,setRecord]=useState(null),[pendingId,setPendingId]=useState(''),[sending,setSending]=useState(false),[error,setError]=useState(''),[loadedFrameUrl,setLoadedFrameUrl]=useState('');
  const imageRef=useRef(null),sequence=useRef(0),character=characters.find(c=>c.id===activeId)||characters[0];
  useEffect(()=>{if(character?.id)try{sessionStorage.setItem(activeStorageKey,character.id);}catch{}},[activeStorageKey,character?.id]);
  const contextKey=JSON.stringify([videoId,startFrame,frames,character?.id,character?.reference_ids]);
  const frameUrl='/api/direct/target-frame?'+new URLSearchParams({video_id:videoId,frame:String(previewFrame)});
  const imageReady=previewFrame===frame&&loadedFrameUrl===frameUrl;
  const storageKey='gen-visual-target:'+contextKey;
  const rangeValid=Boolean(videoId)&&Number.isInteger(startFrame)&&startFrame>=0&&Number.isInteger(frames)&&frames>=1;
  useEffect(()=>{sequence.current++;let saved=null;try{saved=JSON.parse(sessionStorage.getItem(storageKey)||'null');}catch{}
    if(!saved?.id||!Number.isInteger(saved.frame)||saved.frame<startFrame||saved.frame>=startFrame+frames||!Number.isFinite(saved.x)||saved.x<0||saved.x>1||!Number.isFinite(saved.y)||saved.y<0||saved.y>1)saved=null;
    setFrame(saved?.frame??startFrame);setPoint(saved?{x:saved.x,y:saved.y}:null);setRecord(null);setPendingId(saved?.id||'');setSending(false);setError('');},[contextKey]);
  useEffect(()=>{const timer=setTimeout(()=>setPreviewFrame(frame),250);return()=>clearTimeout(timer);},[frame,videoId]);
  const running=sending||Boolean(pendingId),locked=disabled||running;
  useEffect(()=>{onActivity?.(running);return()=>onActivity?.(false);},[running,onActivity]);
  useEffect(()=>{if(!pendingId)return;let live=true,timer;
    async function poll(){try{const next=await api('target-selection/'+pendingId);if(!live)return;setRecord(next);setError('');if(['queued','running'].includes(next.status))timer=setTimeout(poll,1500);else{setPendingId('');if(next.status==='failed')setError(next.error||'No se pudo distinguir la persona. Marca otro fotograma.');}}
      catch(e){if(!live)return;setError(e.message);if(e.status>=400&&e.status<500)setPendingId('');else timer=setTimeout(poll,3000);}}
    poll();return()=>{live=false;clearTimeout(timer);};
  },[pendingId]);
  function forgetCandidate(){try{sessionStorage.removeItem(storageKey);}catch{}}
  function choosePoint(next){if(locked||!imageReady||!next)return;sequence.current++;forgetCandidate();setPoint(next);setRecord(null);setError('');}
  function changeFrame(next){forgetCandidate();setFrame(next);setPoint(null);setRecord(null);setError('');}
  function keyboard(event){if(locked||!imageReady)return;const p=point||{x:.5,y:.5},step=event.shiftKey ? .05 : .01;let next;
    if(event.key==='ArrowLeft')next={...p,x:Math.max(0,p.x-step)};if(event.key==='ArrowRight')next={...p,x:Math.min(1,p.x+step)};if(event.key==='ArrowUp')next={...p,y:Math.max(0,p.y-step)};if(event.key==='ArrowDown')next={...p,y:Math.min(1,p.y+step)};if(event.key==='Enter'||event.key===' ')next=p;if(next){event.preventDefault();choosePoint(next);}}
  async function analyze(){if(locked||!point||!character||!imageReady)return;const serial=++sequence.current;setSending(true);setError('');setRecord(null);
    try{const next=await api('target-selection',{video_id:videoId,start_frame:startFrame,frames,frame,x:point.x,y:point.y,character_id:character.id,reference_ids:character.reference_ids});if(serial!==sequence.current)return;try{sessionStorage.setItem(storageKey,JSON.stringify({id:next.id,frame,x:point.x,y:point.y}));}catch{}setRecord(next);if(['queued','running'].includes(next.status))setPendingId(next.id);}
    catch(e){if(serial===sequence.current)setError(e.message);}finally{if(serial===sequence.current)setSending(false);}}
  const matches=point&&selectionMatches(record,{videoId,startFrame,frames,character,frame,...point});
  const accepted=Boolean(record?.id&&character?.target_selection_id===record.id);
  const usable=matches&&record.status==='completed'&&!record.needs_clarification&&Boolean(record.target?.trim());
  return <section className="visual-target-picker" aria-label={label}>
    <h4>{label}</h4><p>Elige tu personaje y toca el rostro o el centro de la persona que reemplazará. Usa un fotograma donde se vea con claridad.</p>
    <label>Personaje de referencia<select aria-label={label+' · personaje'} disabled={locked} value={character?.id||''} onChange={e=>setActiveId(e.target.value)}>{characters.map((c,i)=><option value={c.id} key={c.id}>{c.name||'Personaje '+(i+1)}{c.target_selection_id?' · selección confirmada':''}</option>)}</select></label>
    {character?.reference_ids?.[0]&&<img className="visual-target-reference" src={'/api/assets/'+character.reference_ids[0]} alt={'Referencia de '+(character.name||'tu personaje')}/>}
    {rangeValid&&<><label>Fotograma para marcar · {(frame/24).toFixed(2)} s<input aria-label={label+' · fotograma'} type="range" min={startFrame} max={startFrame+frames-1} step="1" value={frame} disabled={locked} onChange={e=>changeFrame(Number(e.target.value))}/></label>
    <div className="visual-target-frame" role="button" tabIndex={locked?-1:0} aria-disabled={locked||!imageReady} aria-label={label+' · toca a la persona; usa las flechas para ajustar la marca'} onKeyDown={keyboard} onClick={e=>choosePoint(pointInImage(e.clientX,e.clientY,imageRef.current?.getBoundingClientRect()))}>
      <img ref={imageRef} key={frameUrl} src={frameUrl} alt={'Fotograma fuente en '+(frame/24).toFixed(2)+' segundos'} draggable="false" onLoad={()=>setLoadedFrameUrl(frameUrl)} onError={()=>{setLoadedFrameUrl('');setError('No se pudo cargar este fotograma. Prueba otro momento del intervalo.');}}/>
      {point&&<span className="visual-target-marker" style={{left:(point.x*100)+'%',top:(point.y*100)+'%'}} aria-hidden="true">+</span>}
    </div></>}
    {!rangeValid&&<p role="status">Elige un intervalo válido dentro del vídeo.</p>}
    <button type="button" className="secondary" disabled={locked||!rangeValid||!imageReady||!point||!character?.reference_ids?.length||character.reference_ids.some(id=>!id)} onClick={analyze}>{running?'Identificando la persona…':'Revisar persona marcada'}</button>
    {running&&<p role="status">{record?.stage||'Qwen está revisando el fotograma marcado…'}</p>}
    {error&&<p role="alert" className="error">{error}</p>}
    {matches&&record.status==='completed'&&<div className="visual-target-proposal">
      <p><strong>Persona detectada:</strong> {record.observations||record.target}</p>
      {record.target&&<details><summary>Descripción para la generación</summary><p>{record.target}</p></details>}
      {record.warnings?.length>0&&<p role="status">{record.warnings.join(' ')}</p>}
      {record.needs_clarification?<p role="status">No se distinguió con suficiente claridad. Cambia la marca o el fotograma y repite la revisión.</p>:<button type="button" className="secondary" disabled={disabled||running||!usable||accepted} onClick={()=>onAssign(record)}>{accepted?'Persona confirmada':'Confirmar: reemplazar con '+(character?.name||'este personaje')}</button>}
    </div>}
    {character?.target_selection_id&&!accepted&&<p role="status" className="editor-status">Asignación visual confirmada: {character.target}</p>}
    <p className="composer-hint">La selección se conserva en los tramos de una misma toma. Sólo un cambio de plano detectado requiere otra confirmación. La marca orienta a H3; no es una máscara.</p>
  </section>;
}
