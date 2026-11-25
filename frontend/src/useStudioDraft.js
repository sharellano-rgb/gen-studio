import {useEffect,useRef,useState} from 'react';
import {workspaceLocalStorage as storage} from './workspaceStorage.js';
import {saveStudioDraft,draftAssets} from './studioDrafts.js';
export default function useStudioDraft({id,engine,jobId,form,assets,restored=false,enabled=true}){
 const serialized=JSON.stringify(form),media=JSON.stringify(draftAssets(form,assets));
 const initial=useRef(serialized),last=useRef(restored?null:serialized+'|'+media);
 const [status,setStatus]=useState(restored?'saved':'');
 useEffect(()=>{
  if(!enabled)return;
  const signature=serialized+'|'+media;if(last.current===signature)return;
  if(!restored&&initial.current===serialized){last.current=signature;return;}
  try{saveStudioDraft(storage,{id,engine,job_id:jobId||null,form:JSON.parse(serialized),assets:JSON.parse(media)});last.current=signature;setStatus('saved');}
  catch{setStatus('error');}
 },[id,engine,jobId,serialized,media,restored,enabled]);
 return status;
}
