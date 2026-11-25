// Browser-local editor state, isolated by workspaceStorage. Never stores a new request key.
export const DRAFTS_KEY='gen-studio-drafts-v1';
export const DRAFT_EVENT='gen-studio-drafts-changed';
const engines=new Set(['h3_direct','bunny']);
const clone=value=>JSON.parse(JSON.stringify(value));
const object=value=>value&&typeof value==='object'&&!Array.isArray(value);
function state(storage,strict=false){
 try{const raw=storage.getItem(DRAFTS_KEY);if(!raw)return {version:1,entries:{},current:{},pending:{}};
 const value=JSON.parse(raw);if(value?.version!==1||!object(value.entries)||!object(value.current)||!object(value.pending))throw Error('Invalid draft storage');return value;
 }catch(error){if(strict)throw error;return {version:1,entries:{},current:{},pending:{}};}
}
function write(storage,value){storage.setItem(DRAFTS_KEY,JSON.stringify(value));if(typeof window!=='undefined')window.dispatchEvent(new Event(DRAFT_EVENT));}
export function draftIdFor(storage,engine,jobId){return jobId?engine+':take:'+jobId:state(storage).current[engine]||engine+':new';}
export function readStudioDraft(storage,id){const d=state(storage).entries[id];return d?.id===id&&engines.has(d.engine)&&object(d.form)&&Array.isArray(d.assets)&&typeof d.updated_at==='string'&&(d.job_id===null||typeof d.job_id==='string')?clone(d):null;}
export function listStudioDrafts(storage){return Object.keys(state(storage).entries).map(id=>readStudioDraft(storage,id)).filter(Boolean).sort((a,b)=>b.updated_at.localeCompare(a.updated_at));}
export function draftAssets(form,assets=[]){
 const ids=new Set([form.video_id,form.reference_id,form.face_reference_id,form.environment_reference_id,...(form.direct_reference_ids||[]),...(form.direct_characters||[]).flatMap(c=>c.reference_ids||[])]);
 return assets.filter(a=>ids.has(a.id)).map(a=>Object.fromEntries(['id','name','kind','width','height','duration','display_width','display_height'].filter(k=>Object.hasOwn(a,k)).map(k=>[k,a[k]])));
}
export function saveStudioDraft(storage,{id,engine,job_id=null,form,assets=[]},time=new Date().toISOString()){
 if(!id||!engines.has(engine)||!object(form))throw Error('Borrador no válido.');
 const data=state(storage,true),old=data.entries[id];
 if(old&&(old.engine!==engine||old.job_id!==job_id))throw Error('El borrador pertenece a otra toma.');
 const merged=[...new Map([...(old?.assets||[]),...assets].map(a=>[a.id,a])).values()];
 const record={id,engine,job_id,form:clone(form),assets:draftAssets(form,merged),updated_at:time};
 data.entries[id]=record;write(storage,data);return clone(record);
}
export function activateStudioDraft(storage,record){
 if(record.job_id)return;
 const data=state(storage,true);if(!data.entries[record.id])throw Error('No se encontró el borrador.');
 data.current[record.engine]=record.id;write(storage,data);
}
export function createStudioDraft(storage,engine,form,assets=[],id=engine+':new:'+crypto.randomUUID()){
 const record=saveStudioDraft(storage,{id,engine,form,assets});activateStudioDraft(storage,record);return record;
}
export function migrateStudioDrafts(storage,{direct,selection={},bunny}={}){
 const data=state(storage,true);if(data.migrated)return;
 const candidates={h3_direct:direct?.engine==='h3_direct'?{...direct,video_id:direct.video_id||selection.video||''}:null,bunny};
 for(const [engine,form] of Object.entries(candidates))if(object(form)&&!data.entries[engine+':new'])data.entries[engine+':new']={id:engine+':new',engine,job_id:null,form:clone(form),assets:[],updated_at:new Date().toISOString()};
 data.migrated=true;write(storage,data);
}
export function bindPendingDraft(storage,engine,requestKey,id){const data=state(storage,true);data.pending[engine]={request_key:requestKey,id};write(storage,data);}
export function pendingDraftId(storage,engine,requestKey){const p=state(storage).pending[engine];return p?.request_key===requestKey?p.id:engine+':request:'+requestKey;}
export function recoverPendingDraft(storage,engine,requestKey,form,assets=[]){
 const id=pendingDraftId(storage,engine,requestKey);
 return readStudioDraft(storage,id)||saveStudioDraft(storage,{id,engine,form,assets});
}
export function clearPendingDraft(storage,engine,requestKey){const data=state(storage,true);if(data.pending[engine]?.request_key===requestKey){delete data.pending[engine];write(storage,data);}}
export function draftTitle(record){return record.assets.find(a=>a.id===record.form.video_id)?.name||(record.job_id?'Toma '+record.job_id.slice(0,8):'Nueva toma');}

export function preserveSubmissionDraft(storage,draft,requestKey,retry=false){
 if(retry){if(pendingDraftId(storage,draft.engine,requestKey)!==draft.id)throw Error('El envío pendiente pertenece a otra toma.');}
 else saveStudioDraft(storage,draft);
 bindPendingDraft(storage,draft.engine,requestKey,draft.id);
}
