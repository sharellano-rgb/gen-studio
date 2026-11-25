const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
export function pointInImage(clientX,clientY,rect){
  if(!rect||rect.width<=0||rect.height<=0)return null;
  const x=(clientX-rect.left)/rect.width,y=(clientY-rect.top)/rect.height;
  return Number.isFinite(x)&&Number.isFinite(y)&&x>=0&&x<=1&&y>=0&&y<=1?{x,y}:null;
}
export function clearVisualSelection(character){
  const next={...character};if(next.target_selection_id)next.target='';
  delete next.target_selection_id;return next;
}
export function reconcileCharacters(previous,next){
  return next.map(c=>{const old=previous.find(p=>p.id===c.id);if(!old?.target_selection_id||c.target_selection_id!==old.target_selection_id)return c;
    if(!same(old.reference_ids,c.reference_ids)||old.target!==c.target){const clean=clearVisualSelection(c);if(old.target!==c.target)clean.target=c.target;return clean;}return c;});
}
export function selectionMatches(record,{videoId,startFrame,frames,character,frame,x,y}){
  return Boolean(record&&character&&record.video_id===videoId&&record.start_frame===startFrame&&record.frames===frames&&record.frame===frame&&record.character_id===character.id&&same(record.reference_ids,character.reference_ids)&&record.x===x&&record.y===y);
}
export function applyShotSelection(edit,characters,record){
  const targets=edit.targets?.length?[...edit.targets]:characters.flatMap(c=>c.reference_ids.map(()=>c.target.trim()));
  let offset=0;for(const c of characters){if(c.id===record.character_id)c.reference_ids.forEach((_,i)=>{targets[offset+i]=record.target;});offset+=c.reference_ids.length;}
  return {...edit,targets,target_selections:[...(edit.target_selections||[]).filter(s=>s.character_id!==record.character_id),{character_id:record.character_id,selection_id:record.id}]};
}
export function shotCharacters(characters,edit){
  let offset=0;return characters.flatMap(c=>{const next={...c,target:edit?.targets?.length?edit.targets[offset]||'':c.target};offset+=c.reference_ids.length;
    if(edit?.disabled_character_ids?.includes(c.id))return [];
    delete next.target_selection_id;const selected=edit?.target_selections?.find(s=>s.character_id===c.id);if(selected)next.target_selection_id=selected.selection_id;return [next];});
}

export function missingShotSelection(characters,edits,edit){
  const marked=new Set(edits.filter(e=>!e.passthrough).flatMap(e=>(e.target_selections||[]).map(s=>s.character_id)));
  return shotCharacters(characters,edit).some(c=>marked.has(c.id)&&!c.target_selection_id);
}

export function toggleShotCharacter(edit,id,enabled){
  const disabled=new Set(edit.disabled_character_ids||[]);
  if(enabled)disabled.delete(id);
  else disabled.add(id);
  const next={...edit,disabled_character_ids:[...disabled]};
  if(!enabled)next.target_selections=(edit.target_selections||[]).filter(s=>s.character_id!==id);
  if(!disabled.size)delete next.disabled_character_ids;
  return next;
}
