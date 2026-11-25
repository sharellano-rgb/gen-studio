import React,{useEffect,useRef,useState} from 'react';

export default function GuidedComposition({job,busy,onCreated,onError,preview=null}) {
  const [options,setOptions]=useState(preview),[candidate,setCandidate]=useState(preview?.candidates[0]?.id||''),[sending,setSending]=useState(false);
  const request=useRef(null);
  useEffect(()=>{
    if(preview)return;
    let active=true;
    setOptions(null);setCandidate('');request.current=null;
    fetch(`/api/jobs/${job.id}/guided-options`).then(async r=>r.ok?r.json():null).then(data=>{
      if(active&&data){setOptions(data);setCandidate(data.candidates[0]?.id||'');}
    }).catch(()=>{});
    return()=>{active=false;};
  },[job.id]);
  if(!options?.candidates.length)return null;
  const selected=options.candidates.find(c=>c.id===candidate);
  async function submit(){
    if(!options.enabled||preview)return;
    if(request.current?.candidate!==candidate)request.current={candidate,key:crypto.randomUUID()};
    setSending(true);
    try{
      const r=await fetch(`/api/jobs/${job.id}/guided-compose`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({candidate_id:candidate,request_key:request.current.key})});
      const result=await r.json();if(!r.ok)throw Error(typeof result.detail==='string'?result.detail:'No se pudo crear la copia.');
      request.current=null;onCreated(result);
    }catch(e){onError(e.message);}finally{setSending(false);}
  }
  return <section className="face-summary guided-composition"><h3>Detalle H3 · Composición estable</h3>
    <p>Reutiliza una cabeza ya generada y aprobada. Crea una copia con escala fija y una unión estable sobre su escena.</p>
    <label>Detalle disponible<select aria-label="Detalle H3 disponible" value={candidate} onChange={e=>{setCandidate(e.target.value);request.current=null;}}>
      {options.candidates.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
    {selected&&<video src={selected.head_url} controls playsInline preload="metadata" aria-label="Detalle H3 aprobado" style={{width:'100%',maxHeight:360,objectFit:'contain',borderRadius:14,background:'#060709'}}/>}
    <button className="secondary details-button" onClick={submit} disabled={!options.enabled||busy||sending||!!preview}>
      {sending?'Enviando…':'Crear copia estabilizada en la cola'}</button>
    {!options.enabled&&<p className="tiny">Opción preparada · activación pendiente.</p>}
    <p>Procesamiento CPU. La cabeza aprobada se conserva. La nueva copia aparece en Historial con su descarga y primer plano.</p>
  </section>;
}
