export const VISUAL_ENGINES = [
  {id:'h3_visual_experimental',value:'experimental',label:'Análisis visual detallado Experimental',detail:'Encuadre completo + detalles · Qwen por instante'},
  {id:'h3_visual_faithful',value:'faithful',label:'Análisis visual detallado fiel',detail:'Guía reforzada con varios fotogramas · Escenario original'},
];
const definitions=new Map(VISUAL_ENGINES.map(item=>[item.value,item]));
const baseKeys={draft:'secourses-draft-v2',pending:'secourses-pending-v1'};
const requestFields=['video_id','reference_id','variant','background','frames','start_seconds','megapixels','guide_megapixels',
  'threshold','mask_grow','target','prompt','speech','speech_language','auto_describe','lock_audio','seed',
  'qwen_adaptive_zoom','qwen_motion_grounding','visual_method'];
const parameters=value=>value?.parameters||value||{};
const parse=(storage,key)=>{try{const value=JSON.parse(storage.getItem(key));return value&&typeof value==='object'&&!Array.isArray(value)?value:null;}catch{return null;}};

export function visualMethod(value){
  const p=parameters(value),request=p.secourses_request||{};
  const explicit=p.visual_method??request.visual_method;
  if(explicit!=null)return definitions.has(explicit)?explicit:null;
  // Saved C analyses become the experimental family; B remains SECourses.
  return (p.qwen_adaptive_zoom??request.qwen_adaptive_zoom)===true&&
    (p.qwen_motion_grounding??request.qwen_motion_grounding)===true?'experimental':null;
}
export const visualMethodForEngine=id=>VISUAL_ENGINES.find(item=>item.id===id)?.value||null;
export const isSecoursesEngine=id=>id==='h3_secourses'||VISUAL_ENGINES.some(item=>item.id===id);
export function visualEngineFor(value){const p=parameters(value);return p.engine==='h3_secourses'?(definitions.get(visualMethod(p))?.id||'h3_secourses'):null;}
export const secoursesLabel=value=>definitions.get(visualMethod(value))?.label||'SECourses · Genjutsu';
export function isVisualMotion(value){const p=parameters(value);return p.engine==='h3_secourses'&&visualMethod(p)==='experimental'&&(p.background??p.secourses_request?.background)==='image';}
export const secoursesStudioMode=value=>isVisualMotion(value)?'motion':'character';
export function visualEditorError(form,method){
  return method&&/subject[ _]definitions[\s*_`]*:/i.test(form.prompt||'')
    ?'Escribe indicaciones adicionales; este método prepara el prompt completo automáticamente con Qwen.':'';
}
export function secoursesStorageKeys(method=null,mode='character'){
  if(method===null)return {...baseKeys};
  if(!definitions.has(method))throw Error('Método visual desconocido.');
  const suffix=method==='experimental'&&mode==='motion'?'-motion':'';
  return {draft:`secourses-${method}${suffix}-draft-v1`,pending:`secourses-${method}${suffix}-pending-v1`};
}
export function readSecoursesState(storage,method=null,mode='character'){
  const keys=secoursesStorageKeys(method,mode),pendingKeys=[keys.pending,...[null,'experimental','faithful'].map(value=>secoursesStorageKeys(value).pending),secoursesStorageKeys('experimental','motion').pending];
  const candidates=[...new Set(pendingKeys)].map(key=>({key,body:parse(storage,key)})).filter(item=>item.body);
  const matches=value=>visualMethod(value)===method&&secoursesStudioMode({engine:'h3_secourses',...value})===mode;
  const own=candidates.find(item=>matches(item.body));
  const other=candidates.find(item=>!matches(item.body));
  const ownDraft=parse(storage,keys.draft),legacyDraft=keys.draft!==baseKeys.draft?parse(storage,baseKeys.draft):null;
  return {pending:own?.body||null,pendingKey:own?.key||keys.pending,
    otherPending:other?{...other,method:visualMethod(other.body),label:secoursesLabel(other.body)}:null,
    draft:[ownDraft,legacyDraft].find(value=>value&&matches(value))||null};
}
export function secoursesFormForJob(value){
  const p=parameters(value),source=p.secourses_request||p;
  const form=Object.fromEntries(requestFields.filter(key=>Object.hasOwn(source,key)).map(key=>[key,source[key]]));
  for(const key of ['visual_method','qwen_adaptive_zoom','qwen_motion_grounding'])
    if(!Object.hasOwn(form,key)&&Object.hasOwn(p,key))form[key]=p[key];
  return form;
}
export function prepareSecoursesForm(form,{method=null,mode='character'}={}){
  const next=secoursesFormForJob(form);
  delete next.visual_method;delete next.qwen_adaptive_zoom;delete next.qwen_motion_grounding;
  if(method){
    if(!definitions.has(method))throw Error('Método visual desconocido.');
    if(method==='faithful'&&mode==='motion')throw Error('Análisis visual detallado fiel está disponible en Object Swap.');
    Object.assign(next,{visual_method:method,auto_describe:true,qwen_adaptive_zoom:true,qwen_motion_grounding:true,
      background:method==='experimental'&&mode==='motion'?'image':'video'});
  }
  return next;
}
export function secoursesMethodSubmission(form,pending,makeRequestKey,options={}){
  // Retry exactly the accepted/uncertain body, including old B/C request keys.
  if(pending)return pending;
  const error=visualEditorError(form,options.method);
  if(error)throw Error(error);
  return {...prepareSecoursesForm(form,options),request_key:makeRequestKey()};
}
export function saveSecoursesDraft(storage,value){
  const form=secoursesFormForJob(value),method=visualMethod(value),mode=secoursesStudioMode({engine:'h3_secourses',...form});
  storage.setItem(secoursesStorageKeys(method,mode).draft,JSON.stringify(form));
  return {method,engine:definitions.get(method)?.id||'h3_secourses',mode};
}
export function secoursesRepeatDraft(selected,jobs,assets){
  let source=selected;const seen=new Set();
  while(source?.parameters?.sequence_parent_id){
    if(seen.has(source.id))throw Error('No se puede resolver la secuencia de origen de esta toma.');
    seen.add(source.id);source=jobs.find(job=>job.id===source.parameters.sequence_parent_id);
  }
  if(!source||source.parameters?.engine!=='h3_secourses')throw Error('La secuencia de origen no está en el historial disponible.');
  if(!['completed','failed','cancelled'].includes(source.status)||selected.status==='submission_unknown')
    throw Error('Revisa el trabajo pendiente o su envío antes de preparar otra toma.');
  const form=secoursesFormForJob(source),selection={video:form.video_id,image:form.reference_id,face:null};
  if(!assets.some(asset=>asset.id===selection.video&&asset.kind==='video')||!assets.some(asset=>asset.id===selection.image&&asset.kind==='image'))
    throw Error('Falta un archivo original en la biblioteca disponible. Vuelve a cargarlo antes de repetir la toma.');
  return {params:{...form,engine:'h3_secourses'},selection,origin:{id:source.id,created_at:source.created_at}};
}
