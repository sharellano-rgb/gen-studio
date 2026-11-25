import {isMdmz} from './mdmzState.js';
import {characterVersion} from './characterVersion.js';
import {isNinja,ninjaRepeatDraft} from './ninjaState.js';
import {isAlisson,alissonRepeatDraft} from './alissonState.js';
import {limitDuration} from './durationOptions.js';
import {limitDirectDuration} from './directDuration.js';
import {secoursesRepeatDraft} from './secoursesVisualMethods.js';
import {restoreDirectDraft} from './directEditor.js';
// A repeat starts at the original scene form, never at a derived output.
export function repeatable(job) {
  if(isMdmz(job))return false;
  const p=job?.parameters;
  if(p?.engine==='h3_secourses')return !p.operation||p.operation==='secourses_swap';
  if(isAlisson(job))return !p.operation||p.operation==='alisson_swap';
  if(isNinja(job))return ['ninja_prepare','ninja_render','ninja_shots_plan','ninja_shots_prepare','ninja_shots_render'].includes(p.operation);
  if(p?.engine==='h3_direct')return !p.operation||p.operation==='long_character';
  return !!p&&(!p.operation||p.operation==='long_character'||p.operation==='guided_head'||
    (p.operation==='identity_anchor'&&p.anchor_method==='guided_composition'));
}

export function repeatDraft(selected,jobs,assets) {
  if(isMdmz(selected))throw Error('Este método fue retirado como opción de generación.');
  if(selected?.parameters?.engine==='h3_secourses')return secoursesRepeatDraft(selected,jobs,assets);
  if(isAlisson(selected))return alissonRepeatDraft(selected,assets);
  if(isNinja(selected))return ninjaRepeatDraft(selected,jobs,assets);
  let scene=selected;
  const visited=new Set();
  while(scene?.parameters?.operation&&scene.parameters.operation!=='long_character') {
    if(!repeatable(scene)||visited.has(scene.id))throw Error('No se puede resolver la escena de origen de esta toma.');
    visited.add(scene.id);
    const id=scene.parameters.operation==='guided_head'?scene.parameters.head_parent_id:scene.parameters.anchor_parent_id;
    scene=jobs.find(job=>job.id===id);
  }
  if(!scene?.parameters)throw Error('La escena de origen no está en el historial disponible.');
  if(!['completed','failed','cancelled'].includes(scene.status)||selected.status==='submission_unknown')
    throw Error('Revisa el trabajo pendiente o su envío antes de preparar otra toma.');
  const original=scene.parameters;
  const p=original.engine==='h3_direct'&&original.direct_test_origin?.direct_shot_edits?.some(e=>e.disabled_character_ids?.length)?restoreDirectDraft(original):original;
  const direct=p.engine==='h3_direct';
  const resolutions=direct?['1344x768','768x1344','1024x1024','960x544','864x480','480x864','512x512']:['960x544','864x480','480x864'];
  if(!resolutions.includes(p.resolution))
    throw Error('Esta toma usa un formato anterior. Selecciona una escena del formulario actual.');
  const selection={video:p.video_id,image:p.reference_id,face:p.face_reference_id||null};
  for(const [key,kind] of [['video','video'],['image','image'],['face','image']]) {
    if(key==='face'&&!selection[key])continue;
    if(!assets.some(asset=>asset.id===selection[key]&&asset.kind===kind))
      throw Error('Falta un archivo original en la biblioteca disponible. Vuelve a cargarlo antes de repetir la toma.');
  }
  if((p.preserve_face||p.guide_face_reference)&&!selection.face)
    throw Error('La configuración requiere su foto original del rostro.');
  if(direct){
    const refs=p.direct_reference_ids;
    if(!Array.isArray(refs)||refs.length<1||refs.length>9||new Set(refs).size!==refs.length||
      refs.some(id=>!assets.some(a=>a.id===id&&a.kind==='image')))
      throw Error('Falta una referencia original de H3 directo o su orden no es válido.');
    if(!['base','turbo8','realism','hands'].includes(p.direct_preset))throw Error('Configuración directa no compatible.');
  }
  // Whitelist scalar form parameters: no operation, paths, IDs, approvals,
  // crop geometry, request key, checkpoints or head-only settings.
  const params={preserve_face:false,guide_face_reference:false,ref_image_size:'match',mask_grow_spatial:16};
  for(const key of ['prompt','subject','resolution','frames','steps','seed','lora_strength',
    'mask_grow_spatial','preserve_face','guide_face_reference','ref_image_size']) {
    if(Object.hasOwn(p,key))params[key]=p[key];
  }
  if(direct){
    Object.assign(params,{engine:'h3_direct',preserve_face:false,guide_face_reference:false,ref_image_size:'max',
      direct_reference_ids:[...p.direct_reference_ids],direct_preset:p.direct_preset,direct_character_version:characterVersion(p),
      direct_crop_face:p.direct_crop_face??true,direct_reference_type:p.direct_reference_type??'auto',direct_start_seconds:p.direct_start_seconds??0,
      match_source_orientation:p.match_source_orientation??true});
    for(const key of ['h3_location_provider','h3_plan_provider','direct_vsa','direct_continuity','direct_video_only','direct_binding_mode','direct_direction','direct_high_quality','direct_scene_cuts','direct_dynamic_prompts','direct_global_goal'])
      if(Object.hasOwn(p,key))params[key]=p[key];
    if(p.direct_targets)params.direct_targets=[...p.direct_targets];
    for(const key of ['direct_edit_policy','direct_characters','direct_shot_plan_id','direct_shot_edits'])
      if(Object.hasOwn(p,key))params[key]=structuredClone(p[key]);
    if(p.direct_test&&Number.isInteger(p.direct_test_source_frames)){
      params.frames=p.direct_test_source_frames;
      params.direct_start_seconds=0;
      params.direct_scene_cuts=params.frames>360;
    }
    if(p.direct_test&&p.direct_test_origin)Object.assign(params,structuredClone(p.direct_test_origin));
    if(p.long_parent_id&&params.direct_edit_policy&&params.direct_targets?.length===params.direct_reference_ids.length)params.direct_binding_mode='assigned';
  }
  return {params:direct?limitDirectDuration(params):limitDuration(params),selection,origin:{id:scene.id,created_at:scene.created_at}};
}
