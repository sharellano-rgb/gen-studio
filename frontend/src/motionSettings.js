export const CHARACTER_DIRECTION='Replace the main performer in image 1 with the character in image 2. Use image 2 for identity and complete appearance only. Keep the exact pose, limb positions, object grips, background, camera and lighting from image 1.';
export const REALISM_DIRECTION='Reinterpret the entire source scene as photorealistic live-action cinema. Render natural skin, realistic clothing, materials and environment. Keep all characters, their silhouettes, exact poses, perspective, framing and object contacts. Remove cartoon outlines and flat cel shading. Do not add characters or reframe the shot.';
export const REFERENCE_DIRECTION='Reconstruct the entire source scene using the character appearance, clothing, materials and visual style of the selected reference image. Preserve the source performers roles and relative positions. Follow the source video for choreography, hand and foot motion, facial expressions, mouth movements, object contact, camera movement, framing and action timing. The reference supplies appearance and style; the video determines pose and movement. Keep the same number of performers. Follow changes of shot and show objects only while visible in the source. Preserve the full source composition and original audio timing.';
export const motionFields=['video_id','reference_id','frames','start_seconds','anchor_index','resolution','seed','direction','scene_mode','original_reference','anchor_editor','qwen_recipe','anchor_pose_notes'];
export function motionFingerprint(p){return JSON.stringify(motionFields.map(k=>k==='qwen_recipe'?(p.anchor_editor==='qwen_image_21'&&!p.original_reference?(p[k]||'legacy'):null):k==='anchor_pose_notes'?(p[k]||''):k==='anchor_editor'?(p[k]||'klein'):k==='original_reference'?Boolean(p[k]):k==='scene_mode'?(p[k]||'character'):k==='reference_id'?(p[k]||''):p[k]));}
export function motionPayload(p){const result={...p,reference_id:p.reference_id||null,scene_mode:p.scene_mode||'character'};if(result.anchor_editor!=='qwen_image_21'||result.original_reference)delete result.qwen_recipe;if(!result.anchor_pose_notes?.trim())delete result.anchor_pose_notes;if(result.anchor_editor==='klein')delete result.anchor_editor;return result;}
export function motionPreparationForm(p){return p.anchor_editor==='qwen_image_21'&&!p.original_reference?{...p,qwen_recipe:'pose_v2'}:p;}
export function anchorRecipeLabel(p){return p.anchor_editor==='qwen_image_21'?(p.qwen_recipe==='pose_v2'?'Qwen Pose v2 · 25 pasos':'Qwen anterior · 20 pasos'):'Klein · 4 pasos';}

export function restoreMotionDraft(saved,own,selection,restoreOriginal=false){
  if(restoreOriginal&&own)saved={};
  const {_draft_take_id:owner,...draft}=saved||{};
  const resumeEdited=Boolean(own&&owner===own.id);
  const restored=resumeEdited?{}:Object.fromEntries(motionFields.filter(k=>own&&k in own.parameters).map(k=>[k,own.parameters[k]]));
  const form={video_id:selection.video||'',reference_id:'',frames:73,start_seconds:0,anchor_index:0,
    resolution:'480x864',seed:42,direction:REALISM_DIRECTION,scene_mode:'restyle',anchor_editor:draft.anchor_editor||own?.parameters.anchor_editor||'klein',qwen_recipe:'pose_v2',anchor_pose_notes:'',...draft,...restored};
  if(own&&!resumeEdited)form.scene_mode=own.parameters.scene_mode||'character';
  form.original_reference=own&&!resumeEdited?Boolean(own.parameters.original_reference):Boolean(form.original_reference);
  if(own&&!resumeEdited)form.anchor_editor=own.parameters.anchor_editor||'klein';
  if(own&&!resumeEdited){form.qwen_recipe=own.parameters.qwen_recipe||'legacy';form.anchor_pose_notes=own.parameters.anchor_pose_notes||'';}
  if(!own&&owner&&!draft.qwen_recipe)form.qwen_recipe='legacy';
  if(own&&resumeEdited&&!draft.qwen_recipe)form.qwen_recipe=own.parameters.qwen_recipe||'legacy';
  form.reference_id=form.reference_id||'';
  return {form,owner:own?.id||owner||null};
}
