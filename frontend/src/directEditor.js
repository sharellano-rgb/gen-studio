import {directMaxFrames,continuityEnabled} from './directDuration.js';
import {missingShotSelection,shotCharacters} from './visualTargets.js';
export const DEFAULT_EDIT_POLICY={version:1,scope:'character',clothing:'reference',background:'source',background_description:''};
const copy=value=>JSON.parse(JSON.stringify(value));
export function restoreDirectDraft(prior){
  if(!prior)return prior;
  const restored=copy(prior);
  if(restored.direct_test&&restored.direct_test_origin)Object.assign(restored,restored.direct_test_origin);
  else if(restored.direct_test&&restored.direct_test_source_frames)restored.frames=restored.direct_test_source_frames;
  if(restored.direct_test&&restored.direct_shot_edits?.some(e=>e.disabled_character_ids?.length)){
    const refs=flattenCharacters(restored.direct_characters).reference_ids;
    Object.assign(restored,{direct_reference_ids:refs,reference_id:refs[1]||refs[0],face_reference_id:refs[0]});
  }
  delete restored.direct_test;delete restored.direct_test_source_frames;delete restored.direct_test_origin;
  return restored;
}
export function emptyCharacter(id=crypto.randomUUID()){return {id,name:'',target:'',reference_ids:[''],views:['front']};}
export function initialCharacters(prior,selection={}){
  if(prior?.direct_characters?.length)return copy(prior.direct_characters);
  const refs=prior?.direct_reference_ids?.length?prior.direct_reference_ids:[selection.face||selection.image||''];
  const result=[];
  refs.forEach((id,i)=>{
    const target=prior?.direct_targets?.[i]||'';
    const existing=target.trim()?result.find(c=>c.target===target):null;
    if(existing){existing.reference_ids.push(id);existing.views.push('other');}
    else result.push({...emptyCharacter(),name:`Personaje ${result.length+1}`,target,reference_ids:[id],views:['front']});
  });
  return result;
}
export function flattenCharacters(characters){
  return {reference_ids:characters.flatMap(c=>c.reference_ids),targets:characters.flatMap(c=>c.reference_ids.map(()=>c.target.trim()))};
}
export function planMatches(plan,videoId,frames){return Boolean(plan?.status==='completed'&&plan.video_id===videoId&&plan.frames===frames&&plan.segments?.length);}
export function trialWindow(video,startSeconds,plan){
  const start=Math.round(Number(startSeconds)*24),sourceFrames=Math.floor(Number(video?.duration)*24+.000001);
  if(!Number.isInteger(start)||start<0||!Number.isFinite(sourceFrames))return null;
  const segment=plan?.status==='completed'?plan.segments?.find(s=>start>=s.start&&start<s.start+s.keep):null;
  const frames=Math.min(120,sourceFrames-start,segment?segment.start+segment.keep-start:120);
  return frames>=5?{start,frames,segment}:null;
}
export function directReferenceIssue(ids,images){
  if(!ids.length||ids.some(id=>!id))return 'Añade una imagen a cada vista de tus personajes o elimina la vista vacía.';
  if(ids.length>9)return 'Usa como máximo 9 imágenes entre todos los personajes.';
  if(new Set(ids).size!==ids.length)return 'Una misma imagen está asignada más de una vez. Conserva una sola copia de cada vista.';
  if(ids.some(id=>!images.some(a=>a.id===id)))return 'Una referencia ya no está disponible. Selecciónala de nuevo.';
  return '';
}
export function directIntervalIssue(settings,video){
  if(settings.frames>directMaxFrames(settings))return `Selecciona un intervalo de hasta ${directMaxFrames(settings)/24} segundos.`;
  if(settings.direct_scene_cuts&&settings.direct_vsa&&settings.frames>360)return 'VSA admite hasta 15 segundos en vídeos con cortes. Reduce la duración o desactiva Aceleración VSA en Avanzado.';
  if((!Number.isInteger(settings.frames)||settings.frames<5||Math.round(settings.direct_start_seconds*24)+settings.frames>Math.floor(video.duration*24)+1))return 'Ajusta el intervalo para que quede dentro del vídeo.';
  return '';
}
export function directEditorReason({video,characters,images,settings,ready,available,busy,sending,uploading,enhancing,plan,trial=false,trialStart=0}){
  if(settings.direct_preset==='fasth3_omni'&&((trial?trialWindow(video,trialStart,plan)?.frames:settings.frames)||0)<48)return 'FastH3 Omni necesita al menos 2 segundos de vídeo.';
  if(sending)return 'Enviando tu solicitud…';
  if(uploading)return 'Espera a que termine de cargar el archivo.';
  if(busy)return 'Hay una generación en curso. Tu borrador está guardado.';
  if(enhancing)return 'Espera a que termine el análisis del prompt.';
  if(!video)return 'Carga o selecciona el vídeo fuente.';
  if(settings.direct_binding_mode!=='assigned'&&(!settings.prompt||settings.prompt.trim().length<10))return 'Escribe un prompt de al menos 10 caracteres o usa las referencias asignadas.';
  if(!Number.isSafeInteger(settings.seed)||settings.seed<0)return 'Usa una semilla entera entre 0 y 9007199254740991.';
  if(!Number.isFinite(settings.direct_start_seconds)||settings.direct_start_seconds<0)return 'El inicio del intervalo debe ser un número positivo o cero.';
  const referenceIssue=directReferenceIssue(flattenCharacters(characters).reference_ids,images);
  if(referenceIssue)return referenceIssue;
  if(settings.direct_binding_mode==='assigned'&&characters.some(c=>!c.name.trim()))return 'Dale un nombre a cada personaje.';
  if(new Set(characters.map(c=>c.id)).size!==characters.length)return 'Cada personaje necesita una ficha distinta.';
  if(settings.direct_binding_mode==='assigned'&&characters.some(c=>!c.target.trim()))return 'Indica a quién reemplaza cada personaje en el vídeo.';
  if(settings.direct_edit_policy?.background==='replace'&&!settings.direct_edit_policy.background_description?.trim())return 'Describe el nuevo fondo que quieres mantener en todos los planos.';
  if(settings.direct_dynamic_prompts&&!settings.direct_global_goal.trim())return 'Describe el objetivo general del vídeo.';
  const intervalIssue=!trial&&directIntervalIssue(settings,video);
  if(intervalIssue)return intervalIssue;
  if(trial){
    if((settings.direct_shot_plan_id||settings.direct_shot_edits?.length)&&!planMatches(plan,video.id,settings.frames))return 'Espera a que se recupere la revisión de planos antes de probar.';
    const window=trialWindow(video,trialStart,plan);
    if(!window)return 'Elige un inicio de prueba que deje al menos 5 fotogramas dentro del vídeo.';
    if(!settings.direct_scene_cuts&&characters.some(c=>c.target_selection_id)&&(window.start<Math.round(settings.direct_start_seconds*24)||window.start+window.frames>Math.round(settings.direct_start_seconds*24)+settings.frames))return 'La prueba queda fuera de la selección visual. Ajusta el intervalo completo y vuelve a marcar a la persona.';
    if(plan?.status==='completed'&&!window.segment)return 'Selecciona un inicio dentro de los planos analizados.';
    const edit=settings.direct_shot_edits?.find(e=>e.index===window.segment?.index);
    if(edit?.passthrough)return 'Este plano está marcado para conservar el original. Elige un plano que quieras transformar.';
    if(edit?.background==='replace'&&!edit.background_description?.trim())return 'Describe el fondo del plano elegido para la prueba.';
    const active=shotCharacters(characters,edit);
    if(!active.length)return 'Activa al menos un personaje en el plano de prueba.';
    if(edit?.targets?.length&&active.some(c=>!c.target.trim()))return 'Completa las asignaciones del plano elegido para la prueba.';
  }else if(settings.direct_scene_cuts&&settings.direct_binding_mode==='assigned'){
    if(!planMatches(plan,video.id,settings.frames)||settings.direct_shot_plan_id!==plan.id)return 'Analiza y confirma los planos antes de generar el vídeo completo.';
    for(const segment of plan.segments){
      const edit=settings.direct_shot_edits?.find(e=>e.index===segment.index);
      if(edit?.passthrough)continue;
      if(segment.keep>345&&!edit?.passthrough)return 'Un plano supera 14,38 s. Conserva ese plano original o selecciona un intervalo más corto.';
      if(edit?.background==='replace'&&!edit.background_description?.trim())return `Describe el fondo del plano ${segment.index+1}.`;
      const active=shotCharacters(characters,edit);
      if(!active.length)return `Activa un personaje en el plano ${segment.index+1} o conserva el original.`;
      if(active.some(c=>!c.target.trim()))return `Completa las asignaciones del plano ${segment.index+1}.`;
      if(missingShotSelection(characters,settings.direct_shot_edits||[],edit))return `Marca y confirma a los personajes activos en el plano ${segment.index+1}.`;
    }
  }
  if(!available)return 'El motor local no está disponible. Revisa su conexión en Sistema.';
  if(!ready)return 'Comprobando la configuración del motor. Si tarda, usa «Comprobar motor» en Avanzado.';
  return '';
}
export function buildDirectPayload(settings,characters,video,{requestKey,trial=false,trialStart=0,plan=null}){
  const flat=flattenCharacters(characters);
  const p={...copy(settings),engine:'h3_direct',steps:settings.direct_preset==='base'?20:8,subject:'target character described in the prompt',video_id:video.id,request_key:requestKey,direct_reference_ids:flat.reference_ids,direct_targets:flat.targets,reference_id:flat.reference_ids[1]||flat.reference_ids[0],face_reference_id:flat.reference_ids[0],preserve_face:false,guide_face_reference:false,ref_image_size:['realism','hands','fasth3_omni'].includes(settings.direct_preset)?'match':'max',direct_characters:copy(characters)};
  if(settings.direct_binding_mode!=='assigned'){p.direct_characters=[];p.direct_edit_policy=null;}
  if(!continuityEnabled(settings))delete p.direct_continuity;
  if(!trial)return p;
  const window=trialWindow(video,trialStart,plan);if(!window)throw Error('El intervalo de prueba no es válido.');
  if(plan?.status==='completed'&&!window.segment)throw Error('Selecciona un inicio dentro de los planos analizados.');
  const edit=settings.direct_shot_edits?.find(e=>e.index===window.segment?.index);
  if(edit?.passthrough)throw Error('El plano seleccionado conserva el original. Selecciona otro plano para probar.');
  p.direct_test=true;p.direct_test_source_frames=settings.frames;
  p.direct_test_origin={};
  for(const name of ['frames','direct_start_seconds','direct_scene_cuts','direct_dynamic_prompts','direct_shot_plan_id','direct_shot_edits','direct_edit_policy','direct_direction'])p.direct_test_origin[name]=copy(settings[name]??(name==='direct_shot_edits'?[]:null));
  for(const name of ['direct_continuity','direct_video_only'])if(Object.hasOwn(settings,name))p.direct_test_origin[name]=settings[name];
  p.direct_test_origin.direct_targets=copy(flat.targets);p.direct_test_origin.direct_characters=settings.direct_binding_mode==='assigned'?copy(characters):[];
  p.frames=window.frames;p.direct_start_seconds=window.start/24;p.direct_scene_cuts=false;p.direct_dynamic_prompts=false;p.direct_shot_plan_id=null;p.direct_shot_edits=[];
  delete p.direct_continuity;
  if(settings.direct_dynamic_prompts)p.direct_direction=settings.direct_global_goal;
  if(edit?.targets?.length)p.direct_targets=copy(edit.targets);
  if(window.segment)p.direct_characters=shotCharacters(p.direct_characters,edit);
  if(edit?.disabled_character_ids?.length){
    const active=flattenCharacters(p.direct_characters);
    if(!active.reference_ids.length)throw Error('Activa al menos un personaje en el plano de prueba.');
    p.direct_reference_ids=active.reference_ids;p.direct_targets=active.targets;
    p.reference_id=active.reference_ids[1]||active.reference_ids[0];p.face_reference_id=active.reference_ids[0];
    p.direct_direction='';
  }
  if(edit?.background&&edit.background!=='inherit')p.direct_edit_policy={...(p.direct_edit_policy||DEFAULT_EDIT_POLICY),background:edit.background,background_description:edit.background==='replace'?edit.background_description:''};
  return p;
}
const DIRECT_PENDING_KEY='gen-direct-pending-v1';
export function readPendingDirect(storage){
  try{const body=JSON.parse(storage.getItem(DIRECT_PENDING_KEY)||'null');
    return body&&body.engine==='h3_direct'&&typeof body.request_key==='string'&&typeof body.video_id==='string'&&Array.isArray(body.direct_reference_ids)&&body.direct_reference_ids.length>0&&Number.isInteger(body.frames)?body:null;
  }catch{return null;}
}
export function savePendingDirect(storage,body){storage.setItem(DIRECT_PENDING_KEY,JSON.stringify(body));}
export function clearPendingDirect(storage){storage.removeItem(DIRECT_PENDING_KEY);}
const editorDraftKey=id=>'gen-editor-draft:'+id;
export function readEditorDraft(storage,id){
  if(!id)return null;
  try{const value=JSON.parse(storage.getItem(editorDraftKey(id))||'null');return value?.engine==='h3_direct'&&value.video_id&&Array.isArray(value.direct_characters)?value:null;}catch{return null;}
}
export function saveEditorDraft(storage,id,value){if(id)try{storage.setItem(editorDraftKey(id),JSON.stringify(value));}catch{}}
export function clearEditorDraft(storage,id){if(id)storage.removeItem(editorDraftKey(id));}

export function directSubmissionFailure(error){
  const rejected=error.status>=400&&error.status<500;
  return {rejected,message:error.message+(rejected?'':' Si falta confirmar el envío, usa «Reintentar mismo envío».')};
}
