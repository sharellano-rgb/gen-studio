import {guideStorageKey} from './objectSwapGuide.js';
import {isPikaRefMethod,PIKA_DETAILED} from './pikaRefMethods.js';
import {keys as pikaRefKeys} from './pikaRefState.js';
import {draftIdFor,readStudioDraft,createStudioDraft,DRAFTS_KEY} from './studioDrafts.js';
import {secoursesStorageKeys,visualMethodForEngine,isSecoursesEngine,secoursesFormForJob} from './secoursesVisualMethods.js';
import {bunnyFormForJob} from './bunnyDuration.js';

import {keys as viggleKeys} from './viggleState.js';
const localPending=[viggleKeys('swap').pending,viggleKeys('motion').pending,pikaRefKeys('swap',PIKA_DETAILED).pending,pikaRefKeys('motion',PIKA_DETAILED).pending,'gen-pika-pending-v1','gen-pika-ref2va-swap-pending-v1','gen-pika-ref2va-motion-pending-v1','motion-bunny-pending-v1','motion-tara-pending-v1','motion-layers-pending-v1'];
const sessionPending=['gen-direct-pending-v1','gen-alisson-pending-v1','gen-ninja-shots-pending','gen-ninja-pending:prepare:',...[[null,'character'],['experimental','character'],['experimental','motion'],['faithful','character']].map(([m,s])=>secoursesStorageKeys(m,s).pending)];
const parse=(storage,key)=>{const raw=storage.getItem(key);if(!raw)return null;try{return JSON.parse(raw);}catch{throw Error('No se pudo leer el borrador. Se conservó sin cambios.');}};

export function emptyStudioForm(value={}){
 const clean=JSON.parse(JSON.stringify(value));
 const content=['prompt','direction','direct_direction','direct_global_goal','scene_description','interaction_description','character_direction','character_details','scene_goal','speech','motion_timing_notes','anchor_pose_notes','subject','project'];
 for(const key of Object.keys(clean)){
  if(/(_id|_ids|_sha256)$/.test(key))clean[key]=key.endsWith('_ids')?[]:'';
  if(content.includes(key))clean[key]='';
  if(['direct_characters','direct_targets','direct_shot_edits','selections','subjects','additional_characters'].includes(key))clean[key]=[];
  if(['start_seconds','direct_start_seconds','editor_trial_start_seconds','anchor_index'].includes(key))clean[key]=0;
  if(['x','y','point','box','target_box'].includes(key))clean[key]=null;
 }
 if(clean.engine==='viggle_remake'||clean.reference_mode){clean.reference_mode='appearance';clean.pin_frame=null;}
 delete clean.viggle_request;
 delete clean.request_key;delete clean.pika_ref2va_request;delete clean.secourses_request;
 delete clean.reuse_character_job_id;
 delete clean.direct_test;delete clean.direct_test_origin;delete clean.direct_test_source_frames;
 if(clean.direct_edit_policy)clean.direct_edit_policy={...clean.direct_edit_policy,background_description:''};
 return {...clean,video_id:'',reference_id:'',face_reference_id:'',environment_reference_id:'',target:clean.target===undefined?undefined:'person'};
}

// Only this workspace's current editor is reset. Pending request identities and
// completed takes are never removed. No API or generation request is sent.
export function startNewStudio({storage,session,mode,engine,job,params,ninja,alisson}){
 if(localPending.some(k=>storage.getItem(k))||sessionPending.some(k=>session.getItem(k))||(job?.id&&session.getItem('gen-ninja-pending:render:'+job.id)))throw Error('Hay un envío pendiente de confirmar. Recupera su estado antes de empezar otro vídeo.');
 const updates=[],p=job?.parameters||{};
 const add=(store,key,form,wrapper=false)=>updates.push([store,key,JSON.stringify(wrapper?{form:emptyStudioForm(form),assets:[]}:emptyStudioForm(form))]);
 let shared;
 if(engine==='viggle_remake'){
  const key=viggleKeys(mode==='motion'?'motion':'swap').draft;add(storage,key,parse(storage,key)||p.viggle_request||{});
 }else if(isPikaRefMethod(engine)){
  const key=pikaRefKeys(mode==='motion'?'motion':'swap',engine).draft;add(storage,key,parse(storage,key)||p.pika_ref2va_request||{});
 }else if(mode==='image_video')add(storage,'gen-pika-draft',parse(storage,'gen-pika-draft')||p);
 else if(isSecoursesEngine(engine)){
  const keys=secoursesStorageKeys(visualMethodForEngine(engine),mode);add(session,keys.draft,parse(session,keys.draft)||secoursesFormForJob(job)||{});
 }else if(mode==='motion'){
  const method=storage.getItem('motion-method-v1')||'bunny';
  if(method==='tara'||method==='layers'){
   const key='motion-'+method+'-draft-v1:new',current=parse(storage,'motion-'+method+'-draft-v1:'+(job?.id||'new'));
   add(storage,key,current?.form||p,true);
  }else{const saved=readStudioDraft(storage,draftIdFor(storage,'bunny',job?.id));shared=['bunny',emptyStudioForm(saved?.form||bunnyFormForJob(job)||{})];}
 }else if(engine==='h3_ninja')add(storage,'gen-ninja-draft',ninja||p);
 else if(engine==='h3_alisson')add(storage,'gen-alisson-draft',alisson||p);
 else{
  const saved=readStudioDraft(storage,draftIdFor(storage,'h3_direct',job?.id));
  shared=['h3_direct',emptyStudioForm(saved?.form||params||p)];add(storage,'gen-parameters',shared[1]);
 }
 updates.push([storage,'gen-assets','{}'],[storage,'gen-job','draft'],[storage,'gen-repeat-origin','null']);
 if(mode==='character'&&engine!=='h3_direct')updates.push([isSecoursesEngine(engine)?session:storage,guideStorageKey(engine),JSON.stringify(null)]);
 const previous=updates.map(([s,k])=>[s,k,s.getItem(k)]);if(shared)previous.push([storage,DRAFTS_KEY,storage.getItem(DRAFTS_KEY)]);
 try{for(const [s,k,v] of updates)s.setItem(k,v);if(shared)createStudioDraft(storage,shared[0],shared[1],[]);}
 catch(error){for(const [s,k,v]of previous){try{v===null?s.removeItem(k):s.setItem(k,v);}catch{}}throw error;}
 return {mode,engine};
}
