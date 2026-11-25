import React,{useId,useRef,useState} from 'react';
import {MediaPicker} from './ProjectGuide.jsx';

export default function RequiredPhrase({script,cast,assets,onChange,onCast,workspace,action,onUploaded,disabled,guided=false}){
 const rule=script.required_phrase;
 const group=useId();
 const [deferred,setDeferred]=useState(false);
 function enable(){
   const lead=cast.characters.find(c=>c.parody_role==='replacement')||cast.characters[0];
   if(lead){setDeferred(false);onChange({character_id:lead.id,text:'',reference_id:null,start_ms:0,end_ms:0});}
 }
 const actor=cast.characters.find(c=>c.id===rule?.character_id);
 const clips=actor?.references.filter(r=>r.role==='acting')||[];
 const selected=clips.find(r=>r.id===rule?.reference_id);
 const player=useRef(null),previewEnd=useRef(null);
 const words=text=>(text||'').toLocaleLowerCase().match(/[\p{L}\p{N}_]+/gu)?.join(' ')||'';
 const present=rule&&words(rule.text)&&script.lines.some(l=>l.character_id===rule.character_id&&l.kind==='speech'&&(' '+words(l.text)+' ').includes(' '+words(rule.text)+' '));
 const update=patch=>onChange({...rule,...patch});
 function selectClip(ref,asset){
   update({use_clip_audio:false,reference_id:ref?.id||null,start_ms:0,end_ms:ref?Math.min(3000,Math.round((asset?.duration||2)*1000)):0});
 }
 function attach(asset){
   let ref=clips.find(r=>r.asset_id===asset.id);
   if(!ref){
     if(actor.references.length>=24)throw Error('La ficha ya tiene 24 referencias.');
     ref={id:crypto.randomUUID(),asset_id:asset.id,name:asset.name,role:'acting'};
     onCast({...cast,characters:cast.characters.map(c=>c.id===actor.id?{...c,references:[...c.references,ref]}:c)});
   }
   selectClip(ref,asset);
 }
 return <fieldset disabled={disabled} className="pm-subpanel">{guided?<><legend>¿Quieres incluir una frase obligatoria?</legend>
 <div className="pm-actions"><label><input type="radio" name={group} checked={!!rule} disabled={!cast.characters.length} onChange={enable}/>Sí, añadir frase</label>
 <label><input type="radio" name={group} checked={!rule&&deferred} onChange={()=>{setDeferred(true);if(rule)onChange(null);}}/>Ahora no</label></div>
 {!rule&&<small>Puedes añadirla después en Guion y voces.</small>}</>:<label><input type="checkbox" checked={!!rule} disabled={!rule&&!cast.characters.length} onChange={e=>e.target.checked?enable():onChange(null)}/>Frase obligatoria</label>}
 {rule&&<><div className="pm-script-grid"><label>Quién la dice<select value={rule.character_id} onChange={e=>update({use_clip_audio:false,character_id:e.target.value,reference_id:null,start_ms:0,end_ms:0})}>{cast.characters.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
 <label>Palabras exactas<input maxLength={160} value={rule.text} placeholder="Alrighty Then" onChange={e=>update({text:e.target.value})}/></label></div>
 {script.lines.length>0&&rule.text.trim()&&<small role="status">{present?'La frase está en el diálogo del personaje.':'La frase aún falta en el diálogo de este personaje.'}</small>}
 <details open={guided||selected?true:undefined}><summary>Vídeo de la frase (opcional)</summary>
 {guided&&<p>Sube el clip donde el personaje dice la frase o elige uno que ya tengas. También puedes continuar sólo con las palabras.</p>}
 <label>Clip de este personaje<select value={rule.reference_id||''} onChange={e=>{const r=clips.find(v=>v.id===e.target.value);selectClip(r,assets.find(a=>a.id===r?.asset_id));}}><option value="">Sólo las palabras</option>{clips.map(r=><option key={r.id} value={r.id}>{r.name}</option>)}</select></label>
 {actor&&<MediaPicker kind="acting" assets={assets} workspace={workspace} action={action} onUploaded={onUploaded} onAsset={attach} slots={1}/>}
 {selected&&<><video ref={player} className="pm-preview" controls playsInline preload="metadata" src={'/api/assets/'+selected.asset_id} onTimeUpdate={e=>{if(previewEnd.current!==null&&e.currentTarget.currentTime>=previewEnd.current){previewEnd.current=null;e.currentTarget.pause();}}} onPause={()=>{previewEnd.current=null;}}/>
 <p>Selecciona entre 0,25 y 10 segundos donde se vea al personaje y se escuche la frase.</p>
 <div className="pm-script-grid">{[['start_ms','Desde (s)'],['end_ms','Hasta (s)']].map(([key,label])=><label key={key}>{label}<input type="number" min="0" step="any" value={rule[key]/1000} onChange={e=>update({[key]:Math.round(Number(e.target.value)*1000)})}/></label>)}</div>
 <label><input type="checkbox" checked={!!rule.use_clip_audio} onChange={e=>update({use_clip_audio:e.target.checked})}/>Usar también el audio autorizado de este fragmento en la guía de voz</label><small>La siguiente vista previa de voz incorporará el fragmento. Ref2VA generará el audio del plano a partir de esa guía.</small>
 <button type="button" disabled={rule.end_ms<=rule.start_ms} onClick={()=>action(async()=>{player.current.currentTime=rule.start_ms/1000;previewEnd.current=rule.end_ms/1000;await player.current.play();})}>Reproducir fragmento</button></>}</details>
 </>}</fieldset>;
}
