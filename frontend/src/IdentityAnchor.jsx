import React, {useEffect, useRef, useState} from 'react';
import {guidedDefaults,guidedParams,readGuidedDraft,restoredRegion,saveGuidedDraft} from './guidedDraft';

export default function IdentityAnchor({job, assets, available, busy, onCreated, onError, mode='cpu', preview=null, defaultFace=null}) {
  const guided=mode==='guided';
  const [draft]=useState(()=>guided&&!preview?readGuidedDraft(localStorage,job.id):null);
  const [sources,setSources]=useState(preview?.sources||[]);
  const [sourceId,setSourceId]=useState(preview?.sources[0]?.id||draft?.source||'');
  const [face,setFace]=useState(draft?.face||job.parameters.face_reference_id||defaultFace?.id||'');
  const [sheet,setSheet]=useState(draft?.sheet||job.parameters.reference_id||'');
  const [enabled,setEnabled]=useState(false);
  const [draftSaved,setDraftSaved]=useState(null);
  const [params,setParams]=useState(draft?.params||{...guidedDefaults});
  const [reviewTime,setReviewTime]=useState(0);
  const [region,setRegion]=useState(null);
  const [sending,setSending]=useState(false);
  const request=useRef(null), video=useRef(null), drag=useRef(null);
  const source=sources.find(s=>s.id===sourceId);
  useEffect(()=>{
    if(preview)return;
    let active=true;
    fetch(`/api/jobs/${job.id}/${guided?'guided-head-options':'anchor-options'}`).then(async r=>{if(guided&&r.status===404)return null;if(!r.ok) throw Error('No se pudieron consultar las tomas para corregir.'); return r.json();})
      .then(d=>{if(active&&d){
        const restoring=draft||(guided?d.resume:null);
        const selected=d.sources.find(s=>s.id===restoring?.source)||d.sources[0];
        setSources(d.sources);setSourceId(selected?.id||'');setEnabled(!!d.enabled);
        const restored=guided?restoredRegion(restoring,selected):null;
        setRegion(restored);
        if(guided&&restoring){setParams(guidedParams(restoring.params));setFace(restoring.face||'');setSheet(restoring.sheet||'');}
        const time=restored&&Number.isFinite(restoring?.reviewTime)?Math.max(0,Math.min(restoring.reviewTime,selected.duration-.05)):0;
        setReviewTime(time);
      }})
      .catch(e=>{if(active) onError(e.message);});
    return ()=>{active=false;};
  },[job.id,mode]);
  useEffect(()=>{
    if(guided&&!preview&&source)setDraftSaved(saveGuidedDraft(localStorage,job.id,{source:sourceId,width:source.width,height:source.height,
      face,sheet,params,region,reviewTime}));
  },[job.id,sourceId,source,face,sheet,params,region,reviewTime,guided,preview]);
  function select(id) {setSourceId(id);setRegion(null);setReviewTime(0);request.current=null;}
  function point(e) {
    const r=e.currentTarget.getBoundingClientRect();
    return [Math.round(Math.max(0,Math.min(1,(e.clientX-r.left)/r.width))*source.width),
      Math.round(Math.max(0,Math.min(1,(e.clientY-r.top)/r.height))*source.height)];
  }
  function move(e) {
    if(!drag.current) return;
    const [x,y]=point(e),[sx,sy]=drag.current;
    const side=Math.min(Math.max(Math.abs(x-sx),Math.abs(y-sy)),source.width-sx,source.height-sy);
    if(side>=32) setRegion([sx,sy,side]);
  }
  async function submit() {
    if(preview)return;
    const body={face_reference_id:face,region,source:sourceId,...(guided?{sheet_reference_id:sheet,...params}:{})};
    const signature=JSON.stringify(body);
    if(request.current?.signature!==signature) request.current={signature,key:crypto.randomUUID()};
    setSending(true);
    try {
      const r=await fetch(`/api/jobs/${job.id}/${guided?'generate-guided-head':'anchor-face'}`,{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({...body,request_key:request.current.key})});
      const data=await r.json();
      if(!r.ok) throw Error(typeof data.detail==='string'?data.detail:'Revisa la foto y la zona seleccionada.');
      request.current=null;onCreated(data);
    } catch(e) {onError(e.message);}
    finally {setSending(false);}
  }
  if(guided&&!sources.length)return null;
  return <section className={'face-summary identity-anchor'+(guided?' guided-head':'')}><h3>{guided?'Generar detalle H3 · Guía de pose':'Identidad desde foto · Copia local'}</h3>
    <p>{guided?'Elige la escena, tu foto real y una hoja o referencia del personaje. Marca una zona que cubra el movimiento de la cabeza y muestre un solo rostro al inicio. Se seguirá ese rostro; si hay dudas entre personas, el proceso se detendrá. H3 generará un primer plano nuevo para revisar antes de componer.':'Elige una foto real y marca una zona cuadrada que contenga solo la cabeza durante toda la toma. El proceso comprueba el seguimiento y crea una copia para revisar.'}</p>
    <label>Toma a corregir<select aria-label="Toma a corregir" value={sourceId} onChange={e=>select(e.target.value)}>
      {sources.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
    <label>Foto real del rostro<select aria-label="Foto real del rostro" value={face} onChange={e=>setFace(e.target.value)}>
      <option value="">Selecciona una foto con un solo rostro</option>
      {assets.filter(a=>a.kind==='image').map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
    {face && <img className="reference-thumb" src={`/api/assets/${face}`} alt="Foto elegida para la identidad"/>}
    {guided&&<><label>Referencia de personaje<select aria-label="Referencia de personaje para detalle H3" value={sheet} onChange={e=>setSheet(e.target.value)}>
      <option value="">Selecciona una hoja o imagen del personaje</option>{assets.filter(a=>a.kind==='image').map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
      <div className="parameter-grid">{[['seed','Semilla H3',0,9007199254740991,1],['steps','Pasos H3',8,30,1],['lora_strength','Fuerza LoRA H3',0,1.5,.1],['control_strength','Fuerza de pose',.5,1.5,.1]].map(([key,label,min,max,step])=><label key={key}>{label}<input type="number" aria-label={label} min={min} max={max} step={step} value={params[key]} onChange={e=>setParams(p=>({...p,[key]:Number(e.target.value)}))}/></label>)}</div>
      <label>Detalles a conservar<textarea aria-label="Dirección del detalle H3" maxLength={1000} value={params.direction} onChange={e=>setParams(p=>({...p,direction:e.target.value}))}/></label>
       <details className="nsfw-adapters"><summary>Adaptadores NSFW opcionales <span>Desactivados por defecto</span></summary>
       <label className="check-option"><input type="checkbox" checked={Boolean(params.nsfw_h3_motion_booster)} onChange={e=>setParams(p=>({...p,nsfw_h3_motion_booster:e.target.checked}))}/>H3 Motion Booster V2</label>
       <label className="check-option"><input type="checkbox" checked={Boolean(params.nsfw_penis_v2)} onChange={e=>setParams(p=>({...p,nsfw_penis_v2:e.target.checked}))}/>Penis V2</label>
       <label className="check-option"><input type="checkbox" checked={Boolean(params.nsfw_hm_pussy)} onChange={e=>setParams(p=>({...p,nsfw_hm_pussy:e.target.checked}))}/>HM Pussy V2</label>
       <label className="check-option"><input type="checkbox" checked={Boolean(params.nsfw_minimax_unlocked)} onChange={e=>setParams(p=>({...p,nsfw_minimax_unlocked:e.target.checked}))}/>Minimax H3 Unlocked V2.1</label>
       <label>Fuerza NSFW<input type="number" min="0" max="2" step="0.05" value={params.nsfw_strength} onChange={e=>setParams(p=>({...p,nsfw_strength:Number(e.target.value)}))}/></label>
       <p className="muted">Se aplican como LoRA opcionales sobre el modelo H3 sin fundir pesos permanentes. Archivo, hash e intensidad se registran en el metadato de la toma.</p>
       </details></>}
    {source && <><div className="anchor-selector" style={{aspectRatio:`${source.width}/${source.height}`}}
      onPointerDown={e=>{e.currentTarget.setPointerCapture(e.pointerId);video.current?.pause();drag.current=point(e);}}
      onPointerMove={move} onPointerUp={e=>{move(e);drag.current=null;}} onPointerCancel={()=>{drag.current=null;}}>
      <video ref={video} key={source.id} src={source.url} playsInline preload="auto" aria-label="Selección de cabeza"
        onLoadedMetadata={()=>{if(video.current)video.current.currentTime=reviewTime;}}/>
      {region && <div className="anchor-box" style={{left:`${region[0]/source.width*100}%`,top:`${region[1]/source.height*100}%`,width:`${region[2]/source.width*100}%`,height:`${region[2]/source.height*100}%`}}/>}
    </div><p className="tiny">Arrastra desde la esquina superior izquierda de la zona hacia abajo y a la derecha.</p>
    <label>Revisar posición en el vídeo<input key={source.id} type="range" aria-label="Revisar posición en el vídeo" min="0" max={Math.max(0,source.duration-.05)} step=".04" value={reviewTime}
      onChange={e=>{video.current.pause();const time=Number(e.target.value);setReviewTime(time);video.current.currentTime=time;}}/></label>
    <div className="parameter-grid">{['X','Y','Tamaño'].map((label,i)=><label key={label}>{label}<input type="number" aria-label={`Zona de cabeza ${label}`} min={i===2?32:0}
      max={i===0?source.width:i===1?source.height:Math.min(source.width,source.height)} value={region?.[i]??''}
      onChange={e=>setRegion(r=>{const n=[...(r||[0,0,32])];n[i]=Number(e.target.value);return n;})}/></label>)}</div></>}
    <button className="secondary details-button" onClick={submit}
      disabled={!!preview||!available||(guided&&(!enabled||!sheet))||busy||sending||!face||!source||!region||!region.every(Number.isInteger)||region[0]<0||region[1]<0||region[2]<32||region[0]+region[2]>source.width||region[1]+region[2]>source.height}>
      {sending?'Enviando…':guided?'Generar primer plano H3 en la cola':'Crear copia con identidad desde foto'}</button>
    {guided&&!enabled&&<p>Opción preparada · activación pendiente.</p>}
    {guided&&!preview&&<p>{draftSaved===true?'Ajustes guardados en este dispositivo para esta toma.':draftSaved===false?'No se pudo guardar el borrador en este dispositivo.':'Preparando el borrador de esta toma.'}</p>}
    <p>{guided?'Dos etapas en ComfyUI: guía de pose y generación H3. Ollama libera sus modelos antes de cada envío. El resultado requiere tu aprobación y conserva la escena original. ':''}Tomas continuas de hasta 5,17 s. Si el rostro se oculta o la selección es ambigua, verás el motivo en Historial. Cada nueva copia requiere revisión visual.</p>
  </section>;
}
import {workspaceLocalStorage as localStorage} from './workspaceStorage.js';
