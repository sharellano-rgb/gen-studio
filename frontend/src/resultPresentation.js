export function resultReferences(job){
 const p=job?.parameters||{},original=p.alisson_request||p;
 let refs=['motion_bunny','motion_layers'].includes(p.operation)?[
  {id:p.reference_id,label:'Referencia del personaje'},
  {id:p.environment_reference_id,label:'Referencia del escenario'},
 ]:p.engine==='h3_alisson'?[
  {id:job.alisson_character_sheet?.asset_id,label:'Hoja usada en el vídeo'},
  {id:original.reference_id,label:'Cuerpo y vestuario'},
  {id:original.face_reference_id,label:'Referencia facial'},
 ]:p.engine==='h3_ninja'?[{id:p.reference_id,label:'Referencia del personaje'}]:(p.direct_reference_ids||[p.reference_id]).map((id,i)=>({id,label:'Referencia '+(i+1)}));
 const seen=new Set();return refs.filter(ref=>{if(typeof ref.id!=='string'||!ref.id||seen.has(ref.id))return false;seen.add(ref.id);return true;});
}
export function sceneContextName(context,fallback){
 if(typeof context?.name==='string'&&context.name.trim())return context.name.trim();
 const source=typeof context?.source_name==='string'?context.source_name.trim().split(/[\\/]/).pop():'';
 return source||fallback;
}
