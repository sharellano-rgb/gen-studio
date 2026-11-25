import {pikaRefLabel} from './pikaRefMethods.js';
import {secoursesLabel} from './secoursesVisualMethods.js';
import {bunnySourceStart,bunnyBoundaries} from './bunnyReview.js';
export function parentTake(job) {
  const p=job?.parameters||{};
  return (['pika_h3_flex','h3_i2va','pika_ref2va','viggle_remake'].includes(p.engine)?job?.qwen_feedback_parent:null)||p.sequence_parent_id||p.reuse_character_job_id||p.upscale_parent_id||p.ninja_sequence_owner||p.ninja_sequence_parent_id||p.long_variant_root_id||p.long_variant_parent_id||p.source_result_parent_id||p.long_parent_id||p.hybrid_parent_id||p.h3_face_parent_id||p.geometry_parent_id||p.ninja_parent_id||p.head_parent_id||p.anchor_parent_id||(p.operation==='motion_hybrid'?p.motion_anchor_id:null)||null;
}
export function groupTakes(jobs) {
  const byId=new Map(jobs.map(j=>[j.id,j])),groups=new Map();
  for(const job of jobs) {
    let root=job,rootId=job.id;const seen=new Set([job.id]);
    while(parentTake(root)&&!seen.has(parentTake(root))) {
      rootId=parentTake(root);seen.add(rootId);root=byId.get(rootId);
      if(!root)break;
    }
    if(!groups.has(rootId))groups.set(rootId,{id:rootId,root:byId.get(rootId),jobs:[]});
    groups.get(rootId).jobs.push(job);
  }
  return [...groups.values()];
}
export function comparisonSpec(job) {
  if(!job?.result_available||job.status!=='completed')return null;
  const p=job.parameters;
  if(p.engine==='tessera')return {source:`/api/upscaling/jobs/${job.id}/original`,result:`/api/jobs/${job.id}/result`,offset:p.tessera_segment_index!=null?p.tessera_source.start/24:0,frames:p.frames,sourceLabel:'Original',resultLabel:'Upscaling'};
  if(['h3_mdmz','h3_secourses','pika_ref2va','viggle_remake'].includes(p.engine))return {source:`/api/assets/${p.video_id}`,result:`/api/jobs/${job.id}/result`,offset:p.start_seconds||0,frames:p.frames,sourceLabel:'Vídeo original'};
  if(p.engine==='h3_alisson'&&(!p.operation||p.operation==='alisson_swap'))return {
    source:`/api/assets/${p.video_id}`,result:`/api/jobs/${job.id}/result`,
    offset:(p.start_frame??Math.round((p.start_seconds||0)*24))/24,
    frames:p.frames,sourceLabel:'Vídeo original',
  };
  if(p.engine==='h3_ninja'&&['ninja_render','ninja_shots_render'].includes(p.operation))return {
    source:`/api/assets/${p.video_id}`,result:`/api/jobs/${job.id}/result`,offset:(p.start_frame||0)/24,
    frames:p.frames,sourceLabel:p.source_result_parent_id?'Resultado anterior':'Vídeo original',
  };
  if(p.engine==='viggle_motion'&&p.operation==='motion_layers')return {
    source:`/api/assets/${p.video_id}`,result:`/api/jobs/${job.id}/result`,
    offset:bunnySourceStart(p.start_seconds),frames:p.frames,sourceLabel:'Vídeo original',resultLabel:'Personaje + Escenario',
  };
  if(p.engine==='viggle_motion'&&p.operation==='motion_bunny')return {
    source:`/api/assets/${p.video_id}`,result:`/api/jobs/${job.id}/result`,
    offset:bunnySourceStart(p.start_seconds),frames:p.frames,sourceLabel:'Vídeo original',resultLabel:p.transfer_method==='tara'?'Resultado TARA-H3':'Resultado Bunny',
    boundaries:p.transfer_method==='tara'?[]:bunnyBoundaries(job),
  };
  if(p.engine==='viggle_motion'&&p.operation==='motion_hybrid')return {
    source:`/api/assets/${p.video_id}`,result:`/api/jobs/${job.id}/result`,offset:p.start_seconds||0,frames:p.frames,sourceLabel:'Vídeo original',resultLabel:p.transfer_method==='h3_scene_direct'?'H3 · Escena completa':'Viggle + H3',
  };
  if(p.engine!=='h3_direct')return null;
  if(p.operation==='upscale_1080')return {
    source:`/api/jobs/${job.id}/result?variant=original`,result:`/api/jobs/${job.id}/result`,
    offset:(p.upscale_source_start_frame||0)/24,frames:p.frames,
    sourceLabel:'Resultado original',resultLabel:'Copia a 1080p',
  };
  if(![undefined,'long_character','long_assembly','h3_face_refine','geometry_copy'].includes(p.operation))return null;
  const derived=p.h3_face_parent_id||p.geometry_parent_id;
  return {
    source:derived?`/api/jobs/${derived}/result`:`/api/assets/${p.video_id}`,
    result:`/api/jobs/${job.id}/result`,
    offset:p.operation==='h3_face_refine'?(p.h3_face_start_frame||0)/24:derived?(p.long_context_frames||0)/24:(p.direct_start_seconds||0)+(p.long_context_frames||0)/24,
    ...(p.long_context_frames&&p.operation!=='h3_face_refine'?{resultOffset:p.long_context_frames/24}:{}),
    frames:p.long_keep_frames||p.frames,
    sourceLabel:derived?'Resultado anterior':'Vídeo original',
  };
}
export function comparisonFrame(frame,frames,offset=0,resultOffset=0) {
  const value=Math.min(Math.max(0,Math.round(frame)),Math.max(0,frames-1));
  return {frame:value,resultTime:resultOffset+value/24,sourceTime:offset+value/24};
}
export function refinementStatus(job) {
  const p=job?.parameters||{};
  if(p.engine==='tessera')return 'Refinamiento generativo · Revisa texto, identidad y continuidad';
  if(p.engine==='cpu_composite'&&p.operation==='localized_head_repair')return 'Corrección localizada · Copia de la toma';
  if(p.operation==='motion_layers')return 'Personaje y escenario en dos pasadas · Revisa identidad e interacción';
  if(p.operation==='motion_hybrid'&&p.transfer_method==='h3_scene_direct')return 'Escena completa H3 · Prueba pendiente de revisión';
  if(p.operation==='motion_hybrid')return job.status==='completed'?'Identidad reforzada con H3 · Revisa personaje y movimiento':'Movimiento Viggle e identidad H3 en preparación';
  if(p.operation==='upscale_1080')return job.status==='completed'?'Escalado a 1080p · Sin refinamiento facial adicional':'Escalado a 1080p en preparación';
  if(p.operation==='h3_face_refine'||p.operation==='ninja_shots_render'||p.operation==='ninja_render'&&p.mode==='head')
    return job.status==='completed'?'Refinamiento facial aplicado · Pendiente de tu revisión':'Refinamiento facial en preparación';
  if(p.operation==='long_character'||p.operation==='long_assembly')return p.direct_chunk_frames?`Reemplazo en tramos de hasta ${p.direct_chunk_frames/24} s · Revisa el rostro y las uniones`:'Reemplazo generado por planos · Revisa el rostro en cada tramo';
  return 'Refinamiento facial no aplicado';
}
export function takeLabel(job) {
  const p=job.parameters;
  if(p.engine==='tessera')return p.tessera_segment_index!=null?'Upscaling · Tramo '+(p.tessera_segment_index+1):p.operation==='video_upscale_assembly'?'Montaje con tramo mejorado':'Video Upscaling · Tessera';
  if(p.engine==='viggle_remake')return 'Viggle Remake · '+(p.mode==='motion'?'Motion Transfer':'Object Swap');
  if(p.engine==='pika_ref2va')return pikaRefLabel(p)+' · '+(p.mode==='motion'?'Motion Transfer':'Object Swap');
  if(p.engine==='h3_i2va')return 'H3 directo · Imagen a vídeo';
  if(p.engine==='pika_h3_flex')return 'Pika H3 Flex · Imagen a vídeo';
  if(p.engine==='cpu_composite'&&p.operation==='localized_head_repair')return 'FlowHMR · Cabeza corregida';
  if(p.operation==='motion_hybrid')return p.transfer_method==='h3_scene_direct'?'H3 · Escena completa · Prueba':'Motion Transfer · Viggle + H3';
  if(p.operation==='motion_layers')return 'Personaje + Escenario · Experimental';
  if(p.transfer_method==='tara')return 'Motion Transfer · TARA-H3';
  if(p.operation==='motion_bunny')return 'Motion Transfer · Bunny';
  if(p.operation==='motion_anchor')return 'Guía de Motion Transfer';
  if(p.motion_scene_recipe)return 'Generación H3 de escena completa';
  if(p.hybrid_parent_id&&!p.long_parent_id)return 'Pasada de identidad H3';
  if(p.operation==='upscale_1080')return 'Copia a 1080p';
  if(p.operation==='ninja_shots_plan')return 'Análisis de cortes · Rostro';
  if(p.operation==='ninja_shots_prepare')return 'Máscaras faciales por plano';
  if(p.operation==='ninja_shots_render')return 'Refinamiento facial por planos';
  if(p.engine==='h3_mdmz'&&p.operation==='mdmz_diagnostics')return 'Diagnóstico archivado · Máscara y guía';
  if(p.engine==='h3_mdmz')return 'Prueba archivada · '+(p.speed==='turbo'?'Turbo':'Normal');
  if(p.engine==='h3_secourses')return secoursesLabel(p)+' · '+(p.variant==='fast'?'Rápida':'Normal');
  if(p.engine==='h3_alisson')return 'Alisson v1.1 · Object Swap';
  if(p.direct_preset==='fasth3_omni')return 'FastH3 Omni · Experimental';
  if(p.direct_test)return 'Prueba corta';
  if(p.operation==='ninja_prepare')return 'Preparación de rostro';
  if(p.operation==='ninja_render'||p.operation==='h3_face_refine')return 'Refinamiento facial';
  if(p.operation==='geometry_copy')return 'Ajuste de proporción';
  if(Number.isInteger(p.long_segment_index))return `Plano ${p.long_segment_index+1}`;
  if(p.long_variant_parent_id)return 'Versión con tramo recuperado';
  if(p.operation==='long_assembly')return 'Montaje revisado';
  return p.engine==='viggle_motion'?'Motion Transfer · Archivo':'Reemplazo de personaje';
}
