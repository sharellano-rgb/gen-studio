import ParodyBoundary,{ParodyLauncher} from './parody/ParodyBoundary.jsx';
import ViggleVideo from './ViggleVideo.jsx';
import QwenModelSelector,{QwenModelsProvider} from './QwenModelSelector.jsx';
import {PIKA_DETAILED,pikaRefMethod,isPikaRefMethod} from './pikaRefMethods.js';
import StudioWorkspace,{StudioSlot,ModeIcon} from './StudioWorkspace.jsx';
import {startNewStudio} from './newStudio.js';
import SequenceProgress from './SequenceProgress.jsx';
import QwenDirector from './QwenDirector.jsx';
import PikaVideo from './PikaVideo.jsx';
import PikaRefVideo from './PikaRefVideo.jsx';
import {generationBusy} from './queueAdmission.js';
import {characterVersion,characterVersionLabel} from './characterVersion.js';
import {workspaceLocalStorage as localStorage,workspaceSessionStorage as secoursesStorage,activeWorkspaceId} from './workspaceStorage.js';
import WorkspaceGate from './WorkspaceGate.jsx';
import RestartStudio from './RestartStudio.jsx';
import SonnyGenerationControl from './SonnyGenerationControl.jsx';
import SonnyGalleryLinks from './SonnyGalleryLinks.jsx';
import VideoUpscaling,{UpscalingHost} from './Upscaling.jsx';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';
import IdentityReview from './IdentityReview';
import IdentityAnchor from './IdentityAnchor';
import GuidedComposition from './GuidedComposition';
import GuidedHeadReview from './GuidedHeadReview';
import CharacterFlow from './CharacterFlow';
import LegacyMethods from './LegacyMethods';
import CompositionReview from './CompositionReview';
import {reviewText} from './guidedDraft';
import {repeatDraft,repeatable} from './reuseTake';
import {durationOptions,limitDuration} from './durationOptions';
import {limitDirectDuration} from './directDuration';
import {sourceResolution} from './videoOrientation';
import LongVideoReview from './LongVideoReview';
import DirectCharacter from './DirectCharacter';
import NinjaSwap from './NinjaSwap.jsx';
import AlissonSwap from './AlissonSwap.jsx';
import SECoursesSwap from './SECoursesSwap.jsx';
import {isSecoursesEngine,visualMethodForEngine,visualEngineFor,isVisualMotion,secoursesStudioMode,saveSecoursesDraft} from './secoursesVisualMethods.js';
import {isAlisson} from './alissonState.js';
import EnginePicker from './EnginePicker.jsx';
import {selectObjectSwapEngine,activeObjectSwap} from './engineSelection.js';
import {objectSwapEngine,restoreObjectSwapEngine,keepSelectedJob,isNinja,ninjaLabel} from './ninjaState.js';
import MotionMethods from './MotionMethods.jsx';
import DirectReview from './DirectReview';
import GeometryCopy from './GeometryCopy';
import H3FaceRefine from './H3FaceRefine';
import {showsDirectComposer,h3FacePreview} from './h3FaceRequest.js';
import {uploadAsset} from './assetUploads';
import UploadProgress from './UploadProgress.jsx';
import RecentTakes from './RecentTakes.jsx';
import {resultUrl} from './resultVersion.js';
import ResultComparison from './ResultComparison.jsx';
import H3Result from './H3Result.jsx';
import CinemaPresentation from './CinemaPresentation.jsx';
import SceneHistory from './SceneHistory.jsx';
import usePageNavigation from './usePageNavigation.js';
import DraftShelf from './DraftShelf.jsx';
import StudioTools from './StudioTools.jsx';
import StudioNav from './StudioNav.jsx';
import ImageStudio from './ImageStudio.jsx';
import ResultViewer from './ResultViewer.jsx';
import {isMdmz} from './mdmzState.js';
import {draftIdFor,readStudioDraft,createStudioDraft,activateStudioDraft,migrateStudioDrafts} from './studioDrafts.js';
import {sceneName} from './historyLibrary.js';
import {sceneContextName} from './resultPresentation.js';
import {takeLabel} from './sceneResults.js';
import './historyNavigation.css';
import ImproveResult from './ImproveResult.jsx';
import Upscale1080 from './Upscale1080.jsx';
import JobCancelButton from './JobCancelButton.jsx';
import JobElapsed from './JobElapsed.jsx';
import {ninjaDefaults} from './ninjaState.js';
import './studioShell.css';
import './studioWorkspace.css';
import './studioTheme.css';
import './studioHeader.css';
import './studioVideo.css';
import './studioNavigation.css';
import './studioHistory.css';
import './hengeStudio.css';

async function api(path, options) {
  const response = await fetch(`/api${path}`, options);
  if(response.headers.get('X-Gen-Studio-Workspace')&&response.headers.get('X-Gen-Studio-Workspace')!==activeWorkspaceId()){
    window.location.reload();throw Error('Actualizando el espacio de esta cuenta…');
  }
  const data = await response.json();
  if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : data.errors?.join(' · ') || `HTTP ${response.status}`);
  return data;
}
const gb = bytes => Number.isFinite(bytes) ? (bytes / 1024 ** 3).toFixed(1) : '—';
const labels = {queued: 'En cola', preparing: 'Preparando', submitting: 'Enviando', running: 'Generando', downloading: 'Guardando', refining: 'Afinando', cancelling: 'Cancelando', completed: 'Generado', failed: 'Error', cancelled: 'Cancelado', submission_unknown: 'Envío sin confirmar'};
const terminal = new Set(['completed', 'failed', 'cancelled', 'submission_unknown']);
function stored(key, fallback) { try { return JSON.parse(localStorage.getItem(key)) || fallback; } catch { return fallback; } }

function App({workspace}) {
  const [status, setStatus] = useState(null);
  const [workflow, setWorkflow] = useState(null);
  const [error, setError] = useState(()=>{try{migrateStudioDrafts(localStorage,{direct:stored('gen-parameters',null),selection:stored('gen-assets',{}),bunny:stored('motion-bunny-draft-v1',null)});return '';}catch{return 'No se pudieron recuperar los borradores de este navegador.';}});
  const [validation, setValidation] = useState(null);
  const [checking, setChecking] = useState(false);
  const {tab,setTab,historyVisited,jobId,setJobId,studioMode,setStudioMode,studioEngine,setStudioEngine,studioView,setStudioView,replaceNavigation}=usePageNavigation({workspace:workspace.id,initial:{jobId:localStorage.getItem('gen-job')||'draft',studioMode:stored('gen-studio-mode','character'),studioEngine:restoreObjectSwapEngine(stored('gen-object-swap-engine',null),stored('gen-parameters',null)),studioView:localStorage.getItem('gen-job')&&localStorage.getItem('gen-job')!=='draft'?'result':'edit'}});
  const [historyTake,setHistoryTake]=useState(null);
  const viewSwitch=useRef(null);
  const [comparisonSeek,setComparisonSeek]=useState(null);
  const [cinemaTake,setCinemaTake]=useState(null);
  function seekComparison(frame){if(!job)return;setComparisonSeek({jobId:job.id,frame,token:crypto.randomUUID()});setStudioView('result');}
  function openTake(id){setJobId(id);setStudioView(id==='draft'?'edit':'result');requestAnimationFrame(()=>window.scrollTo({top:0,behavior:'instant'}));}
  function changeStudioView(view){setStudioView(view);if(window.matchMedia('(max-width: 900px)').matches)requestAnimationFrame(()=>viewSwitch.current?.scrollIntoView({block:'nearest'}));}
  function openHistoryTake(id,context){
    setHistoryTake({id,name:context.name});
    setJobs(current=>current.some(item=>item.id===id)?current:[...current,context.job]);
    setAssets(current=>[...new Map([...context.assets,...current].map(item=>[item.id,item])).values()]);
    openTake(id);setTab('studio',{reset:true});
  }
  useEffect(()=>{
    const mobile=window.matchMedia('(max-width: 900px)');
    const pauseHidden=()=>document.querySelectorAll('video').forEach(video=>{if(!video.getClientRects().length)video.pause();});
    pauseHidden();
    mobile.addEventListener('change',pauseHidden);
    return()=>mobile.removeEventListener('change',pauseHidden);
  },[studioView,tab]);
  useEffect(()=>{localStorage.setItem('gen-studio-mode',JSON.stringify(studioMode));},[studioMode]);
  useEffect(()=>{localStorage.setItem('gen-object-swap-engine',JSON.stringify(studioEngine));},[studioEngine]);
  const [params, setParams] = useState(() => {const saved=stored('gen-parameters',null);return saved?.engine==='h3_direct'?limitDirectDuration({...saved,direct_character_version:characterVersion(saved)}):limitDuration(saved);});
  const [ninjaDraft,setNinjaDraft]=useState(()=>stored('gen-ninja-draft',null));
  const [alissonDraft,setAlissonDraft]=useState(()=>stored('gen-alisson-draft',null));
  useEffect(()=>{if(alissonDraft)localStorage.setItem('gen-alisson-draft',JSON.stringify(alissonDraft));},[alissonDraft]);
  useEffect(()=>{if(ninjaDraft)localStorage.setItem('gen-ninja-draft',JSON.stringify(ninjaDraft));},[ninjaDraft]);
  const [assets, setAssets] = useState([]);
  const [selection, setSelection] = useState(() => stored('gen-assets', {}));
  const [uploading, setUploading] = useState('');
  const [uploadProgress,setUploadProgress]=useState(null);
  const uploadController=useRef(null);
  useEffect(()=>()=>uploadController.current?.abort(),[]);
  const [submitting, setSubmitting] = useState(false);
  const [jobs, setJobs] = useState([]);
  const selectedJobId=useRef(jobId);selectedJobId.current=jobId;
  const [takeLoadError,setTakeLoadError]=useState(''),[takeLoadRetry,setTakeLoadRetry]=useState(0);
  const [repeatOrigin,setRepeatOrigin] = useState(() => stored('gen-repeat-origin',null));
  const [directDraftVersion,setDirectDraftVersion]=useState(0);
  const requestKey = useRef(null);
  const headRequest = useRef(null);
  const [headDirection, setHeadDirection] = useState('Keep the exact fabric cap shape, crown, seams, curved brim and front patch from the face reference through every head turn, if a cap is present.');
  const video = assets.find(a => a.id === selection.video);
  const reference = assets.find(a => a.id === selection.image);
  const faceReference = assets.find(a => a.id === selection.face);
  const preserveFace = params?.preserve_face ?? false;
  const job = jobId==='draft'?null:jobId?jobs.find(j => j.id === jobId)||null:jobs[0];
  useEffect(()=>{
    setTakeLoadError('');
    if(!jobId||jobId==='draft'||jobs.some(j=>j.id===jobId))return;
    let live=true;
    api('/jobs/'+jobId).then(value=>{if(live)setJobs(current=>[...current.filter(j=>j.id!==value.id),value]);}).catch(e=>{if(live)setTakeLoadError(e.message);});
    return()=>{live=false;};
  },[jobId,takeLoadRetry]);
  useEffect(()=>{
    if(tab!=='studio'||!job?.id)return;let live=true;
    api('/jobs/'+job.id+'/scene-context').then(context=>{if(live)setHistoryTake({id:job.id,name:sceneContextName(context,sceneName({id:job.id,root:job,jobs:[job]},assets))});}).catch(()=>{});
    return()=>{live=false;};
  },[job?.id,tab]);
  const editorReady=Boolean(status)&&(!jobId||jobId==='draft'||Boolean(job))&&(!job||(job.parameters.engine==='viggle_remake'?studioEngine==='viggle_remake'&&studioMode===(job.parameters.mode==='motion'?'motion':'character'):job.parameters.engine==='pika_ref2va'?studioEngine===pikaRefMethod(job)&&studioMode===(job.parameters.mode==='motion'?'motion':'character'):['pika_h3_flex','h3_i2va'].includes(job.parameters.engine)?studioMode==='image_video':isVisualMotion(job.parameters)?studioMode==='motion'&&studioEngine===visualEngineFor(job.parameters):job.parameters.engine==='viggle_motion'?studioMode==='motion':studioMode==='character'&&studioEngine===objectSwapEngine(job.parameters)));
  useEffect(()=>{setCinemaTake(null);},[job?.id,tab]);
  function openCinema(){
    if(!job?.result_available||job.status!=='completed')return;
    const media=Array.from(document.querySelectorAll('.result-player-video,[data-comparison-video="result"],.panel.preview .video-stage video,.motion-result video')).find(video=>video.getClientRects().length&&video.getAttribute('src')?.includes('/api/jobs/'+job.id+'/result'));
    setCinemaTake({id:job.id,time:media?.readyState>=1?media.currentTime:0});
  }
  const directJob=job?.parameters.engine==='h3_direct'?job:null;
  const bunnyJob=job?.parameters.engine==='viggle_motion'&&job.parameters.operation!=='motion_layers'?job:null;
  const directDraftId=draftIdFor(localStorage,'h3_direct',directJob?.id);
  const bunnyDraftId=draftIdFor(localStorage,'bunny',bunnyJob?.id);
  const isHead = job?.parameters.operation === 'head_detail';
  const isAnchor = job?.parameters.operation === 'identity_anchor';
  const isGuidedHead = job?.parameters.operation === 'guided_head';
  const isGuided = isAnchor && job?.parameters.anchor_method === 'guided_composition';
  const isLong = ['long_character','long_assembly'].includes(job?.parameters.operation);
  const isLocalizedHeadRepair=job?.parameters.engine==='cpu_composite'&&job.parameters.operation==='localized_head_repair';
  const isGeometry=job?.parameters.operation==='geometry_copy';
  const isUpscale=job?.parameters.operation==='upscale_1080';
  useEffect(()=>{
    if(!job?.id)return;
    if(job.parameters.engine==='tessera'){replaceNavigation({studioMode:'video_upscale',studioEngine:'h3_direct'});return;}
    if(job.parameters.hybrid_parent_id){replaceNavigation({studioMode:'motion',jobId:job.parameters.hybrid_parent_id});return;}
    if(job.parameters.engine==='viggle_remake'){replaceNavigation({studioMode:job.parameters.mode==='motion'?'motion':'character',studioEngine:'viggle_remake'});return;}
    if(job.parameters.engine==='pika_ref2va'){replaceNavigation({studioMode:job.parameters.mode==='motion'?'motion':'character',studioEngine:pikaRefMethod(job)});return;}
    if(isVisualMotion(job.parameters)){replaceNavigation({studioMode:'motion',studioEngine:visualEngineFor(job.parameters)});return;}
    if(job.parameters.engine==='viggle_motion'){replaceNavigation({studioMode:'motion',...(visualMethodForEngine(studioEngine)?{studioEngine:'h3_direct'}:{})});return;}
    if(['pika_h3_flex','h3_i2va'].includes(job.parameters.engine)){replaceNavigation({studioMode:'image_video',studioEngine:job.parameters.engine});return;}
    const engine=objectSwapEngine(job.parameters);replaceNavigation({studioMode:'character',studioEngine:engine});
    if(engine==='h3_sam3'&&['h3_direct','h3_ninja'].includes(params?.engine))setParams(workflow?.defaults||null);
  },[job?.id]);
  const facialPreview = h3FacePreview(job);
  const previewId = facialPreview ? facialPreview.id : isHead && !job.head_result_available ? job.parameters.head_parent_id : job?.id;
  const canDownload = job?.result_available && (!isHead || job.head_result_available);
  const hasH3Result=job?.parameters.engine==='h3_direct'&&job.status==='completed'&&job.result_available&&(isLong||!job.parameters.operation&&!job.parameters.long_parent_id&&!Number.isInteger(job.parameters.long_segment_index));
  const previewReference = isUpscale ? null : facialPreview ? assets.find(a => a.id === facialPreview.referenceId) : job?.result_available || isAnchor ? assets.find(a => a.id === (job.parameters.h3_face_reference_id||job.parameters.reference_id)) : reference;
  useEffect(() => { if (!selection.face && job?.parameters.face_reference_id) setSelection(current => ({...current, face: job.parameters.face_reference_id})); }, [job?.parameters.face_reference_id, selection.face]);
  async function refresh() {
    try { const [state, history] = await Promise.all([api('/status'), api('/jobs')]); setStatus(state); setJobs(current=>keepSelectedJob(history.jobs,current,selectedJobId.current)); setError(current => current.startsWith('Conexión:') ? '' : current); }
    catch (e) { setError(`Conexión: ${e.message}`); }
  }
  useEffect(() => {
    Promise.all([api('/status'), api('/jobs'), api('/assets')]).then(async ([state, history, data]) => {
      setStatus(state); setJobs(current=>keepSelectedJob(history.jobs,current,selectedJobId.current));
      const cached=[...(readStudioDraft(localStorage,directDraftId)?.assets||[]),...(readStudioDraft(localStorage,bunnyDraftId)?.assets||[])];
      setAssets(current=>[...new Map([...cached,...current,...data.assets].map(asset=>[asset.id,asset])).values()]);
      const latest = history.jobs.find(item => !item.parameters.operation && item.parameters.resolution !== '512x512')?.parameters;
      setSelection(current => jobId==='draft'?current:({...current,
        video: current.video || latest?.video_id || data.assets.find(a => a.kind === 'video')?.id,
        image: current.image || latest?.reference_id || data.assets.find(a => a.kind === 'image')?.id,
        face: current.face || latest?.face_reference_id}));
    }).catch(e => setError(`Conexión: ${e.message}`));
    api('/workflows/character-replacement').then(data => { setWorkflow(data); setParams(current => current || data.defaults); }).catch(e => setError(e.message));
    const id = setInterval(refresh, 3000); return () => clearInterval(id);
  }, []);
  useEffect(() => { if (params) localStorage.setItem('gen-parameters', JSON.stringify(params)); }, [params]);
  useEffect(() => { localStorage.setItem('gen-assets', JSON.stringify(selection)); }, [selection]);
  useEffect(() => { localStorage.setItem('gen-job', jobId); }, [jobId]);
  useEffect(() => { localStorage.setItem('gen-repeat-origin',JSON.stringify(repeatOrigin)); },[repeatOrigin]);
  function update(name, value) { setParams(current => ({...current, [name]: value})); }
  const saveDirectDraft=useCallback(next=>{
    setParams(current=>JSON.stringify(current)===JSON.stringify(next)?current:next);
    const inputs={video:next.video_id,face:next.direct_reference_ids[0]||'',image:next.direct_reference_ids[1]||next.direct_reference_ids[0]||''};
    setSelection(current=>Object.entries(inputs).every(([k,v])=>current[k]===v)?current:{...current,...inputs});
  },[]);
  const saveNinjaDraft=useCallback(next=>{setNinjaDraft(current=>JSON.stringify(current)===JSON.stringify(next)?current:next);},[]);
  const saveAlissonDraft=useCallback(next=>{setAlissonDraft(current=>JSON.stringify(current)===JSON.stringify(next)?current:next);},[]);
  function chooseEngine(value){
    const next=selectObjectSwapEngine(studioEngine,jobId,value);
    setStudioEngine(next.engine);setJobId(next.jobId);setStudioView('edit');setError('');
    if(value==='h3_sam3'&&['h3_direct','h3_ninja'].includes(params?.engine))setParams(workflow?.defaults||null);
  }
  function openSecoursesMethod(body){
    const p={...body,engine:'h3_secourses'};replaceNavigation({tab:'studio',jobId:'draft',studioMode:secoursesStudioMode(p),studioEngine:visualEngineFor(p),studioView:'edit'});setDirectDraftVersion(v=>v+1);
  }
  function chooseMotionMethod(value){replaceNavigation({studioMode:'motion',studioEngine:value==='viggle_remake'||isPikaRefMethod(value)?value:value==='visual_experimental'?'h3_visual_experimental':'h3_direct',jobId:'draft',studioView:'edit'});}
  async function resumeDraft(record){
    if(!record)return;
    try{
      let target=null;
      if(record.job_id){target=jobs.find(item=>item.id===record.job_id)||await api('/jobs/'+record.job_id);setJobs(current=>[target,...current.filter(item=>item.id!==target.id)]);}
      activateStudioDraft(localStorage,record);
      setAssets(current=>[...new Map([...(record.assets||[]),...current].map(asset=>[asset.id,asset])).values()]);
      replaceNavigation({jobId:target?.id||'draft',studioMode:record.engine==='bunny'?'motion':'character',...(['bunny','h3_direct'].includes(record.engine)?{studioEngine:'h3_direct'}:{})});
      setDirectDraftVersion(v=>v+1);setStudioView('edit');setError('');setTab('studio',{reset:true});
    }catch(e){setError('No se pudo retomar el borrador: '+e.message);}
  }
  async function reuseTake(item,availableAssets=assets,availableJobs=jobs) {
    try {
      if(!activeObjectSwap(item?.parameters))throw Error('Este método fue retirado. Selecciona H3 directo para crear una nueva toma.');
      if(submitting||uploading||requestKey.current||activeJobs.length)
        throw Error('Termina la carga o revisa el envío actual antes de preparar otra toma.');
      let history=availableJobs;
      if(isNinja(item)&&item.parameters.operation==='ninja_render'&&!history.some(j=>j.id===item.parameters.ninja_parent_id)){
        const parent=await api('/jobs/'+item.parameters.ninja_parent_id);history=[parent,...history];setJobs(current=>[parent,...current.filter(j=>j.id!==parent.id)]);
      }
      if(item?.parameters?.engine==='h3_secourses'){
        let parent=item;const seen=new Set();
        while(parent?.parameters?.sequence_parent_id&&!seen.has(parent.id)){seen.add(parent.id);const id=parent.parameters.sequence_parent_id;parent=history.find(j=>j.id===id)||await api('/jobs/'+id);if(!history.some(j=>j.id===parent.id))history=[parent,...history];}
      }
      const draft=repeatDraft(item,history,availableAssets);
      setAssets(current=>[...new Map([...availableAssets,...current].map(asset=>[asset.id,asset])).values()]);
      if(draft.params.engine==='h3_ninja')setNinjaDraft(draft.params);
      else if(draft.params.engine==='h3_alisson')setAlissonDraft(draft.params);
      else if(draft.params.engine==='h3_secourses')saveSecoursesDraft(secoursesStorage,draft.params);
      else {createStudioDraft(localStorage,'h3_direct',{...draft.params,video_id:draft.selection.video},availableAssets);setParams(draft.params);}
      setSelection(draft.selection);setRepeatOrigin(draft.origin);
      replaceNavigation({studioMode:secoursesStudioMode(draft.params),studioEngine:objectSwapEngine(draft.params),jobId:'draft',studioView:'edit'});setDirectDraftVersion(v=>v+1);
      setValidation(null);setError('');headRequest.current=null;setJobId('draft');setStudioView('edit');setTab('studio',{reset:true});
    } catch(e){setError(e.message);}
  }
  async function prepareResultRefinement(item,referenceId) {
    const asset=await api(`/jobs/${item.id}/refinement-source`,{method:'POST'});
    setAssets(current=>[asset,...current.filter(a=>a.id!==asset.id)]);
    setNinjaDraft({...ninjaDefaults(asset),reference_id:referenceId,head_motion:'preserve',engine:'h3_ninja'});
    setSelection({video:asset.id,image:referenceId,face:referenceId});
    setRepeatOrigin({id:item.id,created_at:item.created_at});setStudioEngine('h3_ninja');
    setDirectDraftVersion(v=>v+1);setJobId('draft');setStudioView('edit');setTab('studio');setError('');
  }
  async function validate() {
    setChecking(true); setValidation(null);
    try { setValidation(await api('/workflows/character-replacement/preflight?preserve_face=' + preserveFace)); }
    catch (e) { setValidation({ok: false, errors: [e.message]}); }
    finally { setChecking(false); }
  }
  async function uploadFile(file,kind,{select=true}={}) {
    if(uploadController.current)throw Error('Ya hay un archivo subiendo. Espera o cancela esa carga.');
    const controller=new AbortController();uploadController.current=controller;
    setUploading(kind); setError('');
    setUploadProgress({fileName:file.name,fileSize:file.size,loaded:0,total:null,phase:'sending',updatedAt:Date.now()});
    try {
      const asset=await uploadAsset(file,kind==='video'?'video':'image',fetch,{signal:controller.signal,
        onProgress:progress=>setUploadProgress(current=>current&&current.phase===progress.phase&&current.total!==null&&progress.updatedAt-current.updatedAt<250&&progress.loaded!==progress.total?current:({...current,...progress}))});
      setAssets(current=>[asset,...current]);
      if(select)setSelection(current=>({...current,[kind]:asset.id}));
      if(select&&kind==='video')setParams(current=>current?{...current,resolution:sourceResolution(asset,current.resolution,false)}:current);
      return asset;
    } finally {uploadController.current=null;setUploading('');setUploadProgress(null);}
  }
  async function upload(event, kind) {
    const input=event.currentTarget,file=input.files[0];if(!file)return;
    try{await uploadFile(file,kind);}catch(e){setError(e.message);}finally{input.value='';}
  }
  async function submit() {
    setSubmitting(true); setError('');
    if (!requestKey.current) requestKey.current = crypto.randomUUID();
    try {
      const result = await api('/jobs', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({...params, engine:'h3_sam3', request_key: requestKey.current, video_id: video.id, reference_id: reference.id, preserve_face: preserveFace, face_reference_id: faceReference?.id || null})});
      setJobs(current => [result, ...current.filter(j => j.id !== result.id)]); setJobId(result.id); setRepeatOrigin(null); requestKey.current = null;
    } catch (e) { setError(`${e.message} Revisa Historial antes de repetir el envío.`); }
    finally { setSubmitting(false); }
  }
  async function refineHead() {
    const faceId = job?.parameters.face_reference_id || faceReference?.id;
    if (!headRequest.current || headRequest.current.parent !== job.id || headRequest.current.direction !== headDirection || headRequest.current.face !== faceId)
      headRequest.current = {key: crypto.randomUUID(), parent: job.id, direction: headDirection, face: faceId};
    setSubmitting(true); setError('');
    try {
      const result = await api(`/jobs/${job.id}/refine-head`, {method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({request_key:headRequest.current.key, face_reference_id:faceId, direction:headDirection})});
      setJobs(current => [result, ...current.filter(j => j.id !== result.id)]); setJobId(result.id); headRequest.current = null;
    } catch(e) { setError(`${e.message} Revisa Historial antes de repetir el envío.`); }
    finally { setSubmitting(false); }
  }
  async function cancel(id) {
    try { const result=await api(`/jobs/${id}/cancel`, {method: 'POST'}); setJobs(current=>current.map(item=>item.id===id?result:item)); setError(''); await refresh(); }
    catch (e) { setError(e.message); }
  }
  async function retryFace(id) { try { const result = await api(`/jobs/${id}/retry-face`, {method: "POST"}); setJobs(current => current.map(item => item.id === id ? result : item)); setError(""); } catch (e) { setError(e.message); } } const device = status?.comfy.data?.devices?.[0];
  const engineQueue = status?.queue.data;
  const loaded = status?.ollama.data?.models || [];
  const activeJobs = jobs.filter(j => !terminal.has(j.status));
  const generationBlocked = generationBusy(status,activeJobs);
  const used = device ? Math.max(0, (device.vram_total - device.vram_free) / device.vram_total * 100) : 0;
  const disabled = (preserveFace && (!faceReference || !status?.face_processing?.available)) || !status?.generation_enabled || !status?.comfy.online || !status?.ollama.online || !video || !reference || submitting || Boolean(uploading) || !params || (job && !terminal.has(job.status));
  const hasStudioViews=tab==='studio'&&!isLocalizedHeadRepair&&studioMode!=='video_upscale'&&(studioMode==='image_video'||studioMode==='motion'||(studioEngine==='h3_direct'&&showsDirectComposer(job))||(['h3_alisson','h3_ninja','pika_ref2va',PIKA_DETAILED,'viggle_remake'].includes(studioEngine)||isSecoursesEngine(studioEngine)));
  function newStudio(){
    if(generationBlocked||submitting||uploading){setError('Espera a que termine la operación actual antes de empezar otro vídeo.');return;}
    if(studioMode==='video_upscale'){const url=new URL(window.location.href);url.searchParams.set('take','draft');url.searchParams.set('mode','video_upscale');url.searchParams.delete('method');url.searchParams.delete('view');window.location.assign(url.toString());return;}
    try{
      startNewStudio({storage:localStorage,session:secoursesStorage,mode:studioMode,engine:studioEngine,job,params,ninja:ninjaDraft,alisson:alissonDraft});
      const url=new URL(window.location.href);url.searchParams.delete('page');url.searchParams.set('take','draft');url.searchParams.set('method',studioEngine);url.searchParams.set('view','edit');
      if(studioMode==='character')url.searchParams.delete('mode');else url.searchParams.set('mode',studioMode);
      window.location.assign(url.toString());
    }catch(error){setError(error.message);}
  }
  return <div className={'app henge-studio '+(tab==='studio'?'creative-studio':'')}>
    <aside className="sidebar"><a className="brand" href="/" aria-label="GEN STUDIO inicio"><span className="brand-mark">G</span><span>GEN<span className="muted"> STUDIO</span></span></a><div className="workspace"><span className="workspace-dot">◈</span><div>{workspace.id==='owner'?workspace.name:'Espacio de '+workspace.name}<small>Proyectos e historial privados</small></div></div><div className="nav-label">WORKSPACE</div><StudioNav current={tab==='image-history'?'history':tab} onNavigate={setTab} activeCount={activeJobs.length}/><div className="sidebar-bottom"><span className="live-dot"/>Motor compartido · Espacio privado<small>Tus archivos y tus proyectos</small><div className="version">GEN STUDIO <span>v0.1</span></div></div></aside>
    <main><UpscalingHost workspace={workspace.id}/><header><div><strong>GEN STUDIO</strong><span className="slash">/</span>{tab === 'studio' ? 'Crear vídeo' : tab === 'images' ? 'Crear imagen' : tab === 'image-history' ? 'Historial de imágenes' : tab === 'history' ? 'Historial' : tab === 'system' ? 'Sistema' : 'Workflow'}</div><div className="header-right"><QwenModelSelector/><span className="badge" aria-label="Espacio activo">{workspace.id==='owner'?'MI ESPACIO · PRIVADO':workspace.name.toUpperCase()+' · PRIVADO'}</span><span className="avatar">{workspace.id==='owner'?'G':workspace.name.slice(0,1).toUpperCase()}</span></div></header><div data-studio-view={hasStudioViews?studioView:'both'} className={'content '+(tab==='studio'&&studioMode==='character'&&studioEngine==='h3_direct'?'editor-layout object-editor':tab==='studio'&&studioMode==='motion'?'motion-editor':'')}><StudioWorkspace active={tab==='studio'&&!isLocalizedHeadRepair} mode={studioMode} job={job} busy={generationBlocked||submitting||Boolean(uploading)} onNew={newStudio}>
    {tab==='studio'&&job?<StudioSlot name="title"><div className="page-heading take-heading"><div><button className="take-back" onClick={()=>setTab('history')}>← Volver al historial</button><h1>{historyTake?.id===job.id?historyTake.name:sceneName({id:job.id,root:job,jobs:[job]},assets)}</h1><p>{takeLabel(job)}<span aria-hidden="true"> · </span>{new Date(job.created_at).toLocaleString('es-MX',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'})}</p></div>{job.status==='completed'&&job.result_available&&<button type="button" className="cinema-launch take-cinema" onClick={openCinema}>Ver en Cine <span aria-hidden="true">↗</span></button>}</div></StudioSlot>:!['history','image-history'].includes(tab)&&<StudioSlot name="title"><div className="page-heading"><div><div className="eyebrow">TU MOTOR. TU VISIÓN.</div><h1>{tab === 'studio' ? (studioMode==='video_upscale'?'Video Upscaling':'Nuevo vídeo') : tab === 'images' ? 'De la idea a la imagen.' : tab === 'history' ? 'Cada toma, en un lugar.' : tab === 'system' ? 'Tu estación creativa.' : 'Una base que ya funciona.'}</h1><p>{tab === 'studio' ? (studioMode==='video_upscale'?'Más resolución, con el original siempre disponible.':'Elige el movimiento, el personaje y la escena.') : tab==='images'?'Crea y edita personajes, escenarios y nuevas ideas.':tab==='history'?'Tus escenas, versiones y resultados elegidos.':'Tu motor local, conectado a tu espacio creativo.'}</p></div>{tab==='studio'?<button type="button" className="cinema-launch cinema-home" onClick={()=>setTab('history',{reset:true})}>Ver en Cine <span aria-hidden="true">↗</span></button>:<span className="stage">GEN STUDIO <span>{tab==='images'?'Imágenes':studioMode==='motion'?'Motion Transfer':'Character Replacement'}</span></span>}</div></StudioSlot>}
    {['history','image-history'].includes(tab)&&<nav className="history-media-tabs" aria-label="Tipo de historial"><button type="button" className="secondary" aria-current={tab==='history'?'page':undefined} onClick={()=>setTab('history',{reset:true})}>Vídeos</button><button type="button" className="secondary" aria-current={tab==='image-history'?'page':undefined} onClick={()=>setTab('image-history',{reset:true})}>Imágenes</button></nav>}
    {['images','image-history'].includes(tab)&&<>{tab==='image-history'&&<><SonnyGalleryLinks workspaceId={workspace.id} imagesOnly/><SonnyGalleryLinks workspaceId={workspace.id} imagesOnly targetId="jafet" targetName="Jafet"/></>}<ImageStudio view={tab==='image-history'?'history':'create'} onViewChange={view=>setTab(view==='history'?'image-history':'images',{reset:true})} generationEnabled={status?.generation_enabled} onError={setError} onReference={asset=>{setAssets(current=>[asset,...current.filter(a=>a.id!==asset.id)]);setError('');}}/></>}
    {tab==='studio'&&!isLocalizedHeadRepair&&<div className="studio-toolbar" data-mode={studioMode}>
      <div className="modes" role="group" aria-label="Tipo de creación"><button className={'mode '+(studioMode==='character'?'active':'')} aria-pressed={studioMode==='character'} onClick={()=>replaceNavigation({studioMode:'character',studioView:'edit',...(['pika_h3_flex','h3_i2va'].includes(studioEngine)?{studioEngine:'h3_direct'}:{}),...((job?.parameters.engine==='tessera'||['pika_motion','viggle_motion'].includes(job?.parameters.operation)||['pika_h3_flex','h3_i2va'].includes(job?.parameters.engine)||job?.parameters.engine==='viggle_motion'||isVisualMotion(job?.parameters))?{jobId:'draft'}:{})})}><span className="hg-mode-icon"><ModeIcon name="character"/></span><span className="hg-mode-copy"><strong>Object Swap</strong><small>Reemplazar personajes</small></span></button><button className={"mode "+(studioMode==="motion"?"active":"")} aria-pressed={studioMode==='motion'} onClick={()=>replaceNavigation({studioMode:'motion',studioView:'edit',...(['h3_visual_faithful','pika_h3_flex','h3_i2va'].includes(studioEngine)?{studioEngine:'h3_direct'}:{}),...((job?.parameters.engine!=='viggle_motion'&&!isVisualMotion(job?.parameters))?{jobId:'draft'}:{})})}><span className="hg-mode-icon"><ModeIcon name="motion"/></span><span className="hg-mode-copy"><strong>Motion Transfer</strong><small>Transferir movimiento</small></span></button><button className={'mode '+(studioMode==='image_video'?'active':'')} aria-pressed={studioMode==='image_video'} onClick={()=>replaceNavigation({studioMode:'image_video',studioEngine:'pika_h3_flex',studioView:'edit',...(!['pika_h3_flex','h3_i2va'].includes(job?.parameters.engine)?{jobId:'draft'}:{})})}><span className="hg-mode-icon"><ModeIcon name="image_video"/></span><span className="hg-mode-copy"><strong>Imagen a vídeo</strong><small>Animar una imagen</small></span></button><button className={'mode '+(studioMode==='video_upscale'?'active':'')} aria-pressed={studioMode==='video_upscale'} onClick={()=>replaceNavigation({studioMode:'video_upscale',studioEngine:'h3_direct',studioView:'edit',jobId:'draft'})}><span className="hg-mode-icon"><ModeIcon name="upscale"/></span><span className="hg-mode-copy"><strong>Video Upscaling</strong><small>Mejorar un vídeo</small></span></button><ParodyLauncher disabled={submitting||Boolean(uploading)}/></div>
      {studioMode==='character'&&<EnginePicker compact value={studioEngine} onChange={chooseEngine}/>}
      {studioMode==='image_video'&&<EnginePicker compact value={studioEngine} onChange={value=>replaceNavigation({studioEngine:value,jobId:'draft',studioView:'edit'})} ariaLabel="Motor de imagen a vídeo" engines={[{id:'pika_h3_flex',label:'Pika H3 Flex',detail:'Imagen a vídeo · 480p / 720p'},{id:'h3_i2va',label:'H3 directo',detail:'Imagen a vídeo · 480p / 720p'}]}/>}
      {studioMode!=='video_upscale'&&<StudioTools key={studioMode+':'+studioEngine+':'+(jobId||'draft')}>{close=><>
        {editorReady&&<DraftShelf disabled={submitting||Boolean(uploading)} onResume={record=>{resumeDraft(record);close();}}/>}
        <details className="operation-scope"><summary>Cómo funciona<span aria-hidden="true">＋</span></summary><p>{studioEngine==='viggle_remake'?'Viggle reemplaza al personaje manteniendo el movimiento y el escenario del vídeo original. Qwen analiza la toma completa y sus detalles. La continuidad nativa conserva 17 fotogramas del tramo anterior y se reinicia en cortes de plano.':studioEngine===PIKA_DETAILED?'Pika detallado añade encuadre completo y detalles del mismo instante al análisis de Qwen. Conserva los ajustes y el vídeo guía del Pika original para comparar ambos métodos. Genera por tramos de 5 s y conserva el chat de cada toma.':studioEngine==='pika_ref2va'?'Pika reemplaza al personaje o transfiere su movimiento a otra escena. Qwen observa el vídeo completo, prepara las indicaciones y permite corregirlas desde el chat. Genera tramos de hasta 5 segundos, con guía del último fotograma y reinicio en cambios de plano. Revisa las uniones y los detalles del resultado.':studioMode==='image_video'?'Pika anima una imagen inicial siguiendo tus indicaciones. Qwen prepara el prompt y permite corregirlo desde el chat de la toma. Genera un vídeo de 5.17 segundos con audio.':studioMode==='motion'?'Bunny transfiere el movimiento del vídeo al personaje de referencia y permite cambiar el escenario. Acepta hasta 60 segundos; genera y refina tramos de 5 segundos usando el último fotograma anterior como guía de continuidad. Puedes añadir una imagen independiente para el escenario. Personaje + Escenario es una alternativa experimental de hasta 10 segundos que separa la generación del personaje y el cambio del entorno.':'Asigna personajes, define qué cambiar y revisa los planos. Las reglas de ropa y fondo orientan la generación: comprueba el resultado con una prueba corta.'}</p></details>
        {job&&activeObjectSwap(job.parameters)&&repeatable(job)&&terminal.has(job.status)&&job.status!=='submission_unknown'&&<button className="secondary take-reuse" disabled={submitting||Boolean(uploading)||activeJobs.length>0} onClick={()=>{reuseTake(job);close();}}>Usar ajustes en otra toma</button>}
        {job&&<p className="studio-take-details">Versión {job.id.slice(0,8)}</p>}
      </>}</StudioTools>}
    </div>}
    {uploading&&uploadProgress&&<UploadProgress progress={uploadProgress} onCancel={()=>uploadController.current?.abort()}/>}
    {tab==='studio'&&jobId&&jobId!=='draft'&&!job&&<section className="panel take-load-state" aria-label="Abrir toma"><h2>{takeLoadError?'No se pudo abrir esta toma':'Abriendo tu toma'}</h2><p role={takeLoadError?'alert':'status'}>{takeLoadError?'Comprueba la conexión o busca la toma en tu historial. Puede que ya no esté disponible en este espacio.':'Recuperando el resultado y sus ajustes…'}</p>{takeLoadError&&<div className="history-actions"><button className="secondary" onClick={()=>setTakeLoadRetry(value=>value+1)}>Volver a intentar abrir la toma</button><button className="secondary" onClick={()=>setTab('history')}>Ir al historial</button><button className="secondary" onClick={()=>openTake('draft')}>Abrir un borrador</button></div>}</section>}
    {hasStudioViews&&<div className="studio-view-switch" role="group" aria-label="Vista del estudio" ref={viewSwitch}>
      <button type="button" aria-pressed={studioView==='edit'} onClick={()=>changeStudioView('edit')}>Editar</button>
      <button type="button" aria-pressed={studioView==='result'} onClick={()=>changeStudioView('result')}>Resultado{job&&<small>{labels[job.status]||'En curso'}</small>}</button>
    </div>}
    {tab==='studio'&&editorReady&&<StudioSlot name="sequence"><SequenceProgress onResume={updated=>setJobs(current=>current.map(item=>item.id===updated.id?{...item,...updated,error:null,sequence_done:updated.sequence_done}:item))} key={'sequence-'+job?.id} job={job} onError={setError} onOpen={openTake}/></StudioSlot>}
    {tab==='studio'&&editorReady&&!isLocalizedHeadRepair&&studioMode!=='video_upscale'&&<StudioSlot name="assistant"><QwenDirector onOpenTake={openTake} forcedModelId={studioEngine==='viggle_remake'?'qwen3.8:27b':null} inputMode={studioMode==='image_video'?'image':'video'} key={'qwen-'+(job?.id||'draft')} job={job} onCancel={cancel} onError={setError} onCreated={result=>{setJobs(current=>[result,...current.filter(j=>j.id!==result.id)]);openTake(result.id);setRepeatOrigin(null);}}/></StudioSlot>}
    {tab==='studio'&&studioMode==='video_upscale'&&<VideoUpscaling onReset={()=>setJobId('draft')} assets={assets} job={job} workspace={workspace.id} onUpload={uploadFile} uploading={Boolean(uploading)} onCreated={result=>{setJobs(current=>[result,...current.filter(j=>j.id!==result.id)]);setJobId(result.id);}}/>}
    {tab==='studio'&&editorReady&&studioMode==='character'&&studioEngine==='viggle_remake'&&<ViggleVideo mode="swap" method={studioEngine} key={studioEngine+'-swap-'+(job?.id||'draft')+'-'+directDraftVersion} assets={assets} selection={selection} job={job?.parameters.engine==='viggle_remake'?job:null} jobs={jobs} available={status?.generation_enabled&&status?.ollama.online} busy={generationBlocked} uploading={Boolean(uploading)} onUpload={uploadFile} onCancel={cancel} onOpen={openTake} onError={setError} onReviewSaved={review=>setJobs(current=>current.map(item=>item.id===job?.id?{...item,quality_review:review}:item))} onCreated={result=>{setJobs(current=>[result,...current.filter(j=>j.id!==result.id)]);setJobId(result.id);setStudioView('result');}}/>}
    {tab==='studio'&&editorReady&&studioMode==='character'&&isPikaRefMethod(studioEngine)&&<PikaRefVideo mode="swap" method={studioEngine} key={studioEngine+'-swap-'+(job?.id||'draft')} assets={assets} selection={selection} job={pikaRefMethod(job)===studioEngine?job:null} jobs={jobs} available={status?.generation_enabled&&status?.ollama.online} busy={generationBlocked} uploading={Boolean(uploading)} onUpload={uploadFile} onCancel={cancel} onOpen={openTake} onError={setError} onReviewSaved={review=>setJobs(current=>current.map(item=>item.id===job?.id?{...item,quality_review:review}:item))} onCreated={result=>{setJobs(current=>[result,...current.filter(j=>j.id!==result.id)]);setJobId(result.id);setStudioView('result');}}/>}
    {tab==='studio'&&editorReady&&studioMode==='image_video'&&<PikaVideo engine={studioEngine} key={studioEngine+'-'+(job?.id||'draft')} assets={assets} job={['pika_h3_flex','h3_i2va'].includes(job?.parameters.engine)?job:null} jobs={jobs} available={status?.generation_enabled&&status?.ollama.online} busy={generationBlocked} uploading={Boolean(uploading)} onUpload={uploadFile} onCancel={cancel} onOpen={openTake} onError={setError} onCreated={result=>{setJobs(current=>[result,...current.filter(j=>j.id!==result.id)]);setJobId(result.id);setStudioView('result');}}/>}
    {tab==='studio'&&editorReady&&studioMode==='motion'&&<MotionMethods key={'bunny-form-'+studioEngine+'-'+bunnyDraftId+'-'+directDraftVersion} selectedViggle={studioEngine==='viggle_remake'} selectedRef2va={isPikaRefMethod(studioEngine)?studioEngine:null} available={status?.generation_enabled&&status?.ollama.online} selectedVisualMethod={visualMethodForEngine(studioEngine)} onMethodChange={chooseMotionMethod} onSelectVisualMethod={openSecoursesMethod} draftId={bunnyDraftId} onResumeDraft={resumeDraft} onReviewSaved={review=>setJobs(current=>current.map(item=>item.id===job?.id?{...item,quality_review:review}:item))} view={studioView} onViewChange={changeStudioView} assets={assets} selection={selection} job={job} jobs={jobs} busy={generationBlocked} uploading={uploading} onUpload={uploadFile} onCreated={result=>{setJobs(current=>[result,...current.filter(j=>j.id!==result.id)]);setJobId(result.id);}} onOpen={openTake} onError={setError} onCancel={cancel}/>}
    {tab === 'studio' && studioMode==='character' && studioEngine==='h3_sam3' && !['h3_direct','h3_ninja'].includes(job?.parameters.engine) && <CharacterFlow job={job} jobs={jobs} onSelectJob={setJobId}/>}
    {tab==='studio'&&studioMode==='character'&&studioEngine==='h3_sam3'&&<p className="notice" role="status">Toma del motor retirado H3 + SAM3. Para crear otra toma, selecciona H3 directo.</p>}
    {tab === 'studio' && editorReady && studioMode==='character' && studioEngine==='h3_direct' && !isLocalizedHeadRepair && showsDirectComposer(job) && <DirectCharacter key={'direct-form-'+directDraftId+'-'+directDraftVersion} draftId={directDraftId} onResumeDraft={resumeDraft} assets={assets} selection={selection} job={directJob} draftParams={null} busy={generationBlocked} uploading={Boolean(uploading)} onUpload={(file,kind)=>uploadFile(file,kind,{select:false})} onDraftChanged={saveDirectDraft} available={status?.generation_enabled&&status?.comfy.online&&status?.ollama.online} onError={setError} onCreated={result=>{setJobs(current=>[result,...current.filter(j=>j.id!==result.id)]);setJobId(result.id);setRepeatOrigin(null);}}/>}
    {tab==='studio'&&editorReady&&studioMode==='character'&&studioEngine==='h3_ninja'&&<NinjaSwap onReviewSaved={review=>setJobs(current=>current.map(item=>item.id===job?.id?{...item,quality_review:review}:item))} key={'ninja-'+(isNinja(job)?job.id:'draft-'+directDraftVersion)} assets={assets} selection={selection} job={isNinja(job)?job:null} jobs={jobs} draftParams={ninjaDraft} busy={generationBlocked} uploading={Boolean(uploading)} onUpload={(file,kind)=>uploadFile(file,kind,{select:false})} onDraftChanged={saveNinjaDraft} onError={setError} onNewSequence={()=>{setJobId('draft');setStudioView('edit');setDirectDraftVersion(v=>v+1);}} onCreated={result=>{setJobs(current=>[result,...current.filter(j=>j.id!==result.id)]);setJobId(result.id);setStudioView('result');setRepeatOrigin(null);}}/>}
    {tab==='studio'&&editorReady&&studioMode==='character'&&studioEngine==='h3_alisson'&&<AlissonSwap onReviewSaved={review=>setJobs(current=>current.map(item=>item.id===job?.id?{...item,quality_review:review}:item))} key={'alisson-'+(isAlisson(job)?job.id:'draft-'+directDraftVersion)} assets={assets} selection={selection} job={isAlisson(job)?job:null} draftParams={alissonDraft} busy={generationBlocked} available={status?.generation_enabled&&status?.comfy.online&&status?.ollama.online} uploading={Boolean(uploading)} onUpload={(file,kind)=>uploadFile(file,kind,{select:false})} onDraftChanged={saveAlissonDraft} onError={setError} onReuse={()=>reuseTake(job)} onCancel={()=>cancel(job.id)} onCreated={result=>{setJobs(current=>[result,...current.filter(j=>j.id!==result.id)]);setJobId(result.id);setStudioView('result');setRepeatOrigin(null);}}/>}
    {tab==='studio'&&editorReady&&studioMode==='character'&&isSecoursesEngine(studioEngine)&&<SECoursesSwap visualMethod={visualMethodForEngine(studioEngine)} mode="character" onSelectMethod={openSecoursesMethod} key={'secourses-'+studioEngine+'-'+directDraftVersion+'-'+(job?.parameters.engine==='h3_secourses'?job.id:'draft')} assets={assets} selection={selection} job={job?.parameters.engine==='h3_secourses'?job:null} busy={generationBlocked} uploading={Boolean(uploading)} onUpload={(file,kind)=>uploadFile(file,kind,{select:false})} onError={setError} onCancel={cancel} onCreated={result=>{setJobs(current=>[result,...current.filter(j=>j.id!==result.id)]);setJobId(result.id);setStudioView('result');setRepeatOrigin(null);}}/>}
    {tab==='studio'&&editorReady&&studioMode==='character'&&isMdmz(job)&&<section className="panel mdmz-archive"><p className="notice" role="status">Esta prueba archivada se conserva para consulta y descarga.</p>{job.result_available?<ResultViewer job={job} assets={assets} method="Prueba archivada"/>:<p>{job.stage}</p>}</section>}
    {tab==='studio'&&editorReady&&isLocalizedHeadRepair&&<section className="panel localized-head-repair"><p className="notice" role="status">Corrección localizada · Copia de la toma</p>{job.status==='completed'&&job.result_available?<ResultViewer job={job} assets={assets} method="FlowHMR · Cabeza corregida"/>:<p>{job.stage}</p>}{job.parameters.source_result_parent_id&&<button type="button" className="secondary" onClick={()=>openTake(job.parameters.source_result_parent_id)}>Ver toma original</button>}</section>}
    {workspace.id==='owner'&&tab==='system'&&<><SonnyGenerationControl workspaceId={workspace.id}/><SonnyGenerationControl workspaceId={workspace.id} targetId="jafet" targetName="Jafet"/><RestartStudio/></>}
    {error && <div role="alert" className="notice error">{error}</div>}
    {tab==='system'&&<section className="metrics" aria-label="Estado de conexiones"><div><span className={'live-dot ' + (!status?.comfy.online ? 'off' : '')}/><span>ComfyUI</span><strong>{status ? status.comfy.online ? 'Conectado' : 'Sin conexión' : 'Consultando…'}</strong><small>MiniMax H3 · Generación local</small></div><div><span className={'live-dot ' + (!status?.ollama.online ? 'off' : '')}/><span>Ollama</span><strong>{status ? status.ollama.online ? `${loaded.length} modelos cargados` : 'Sin conexión' : 'Consultando…'}</strong><small>Alternancia de VRAM automática</small></div><div><span className="chip-icon">▤</span><span>Memoria GPU</span><strong>{gb(device?.vram_free)} <em>/ {gb(device?.vram_total)} GB libres</em></strong><small>RTX 4090 · Estado de ComfyUI</small></div></section>}
    {tab === 'studio' && studioMode==='character' && !isLocalizedHeadRepair && !['h3_ninja','h3_alisson','h3_mdmz','pika_ref2va',PIKA_DETAILED,'viggle_remake'].includes(studioEngine)&&!isSecoursesEngine(studioEngine) && <StudioSlot name="result"><div className={'studio-grid'+(studioEngine==='h3_direct'?' direct-engine-results':'')}><section className="panel composer" style={{display:'none'}}><div className="panel-title"><h2>Prepara tu toma</h2><span className="tiny">01 / CHARACTER</span></div><div className="model-card"><span className="model-icon">H3</span><div><strong>MiniMax H3 + SAM3</strong><small>Referencia + movimiento + máscara</small></div><span className="tag">LOCAL</span></div>{repeatOrigin&&<div role="status" className="validation success"><strong>Borrador de la escena del {new Date(repeatOrigin.created_at).toLocaleString()}</strong><small>Archivos y ajustes recuperados del Historial. Generar creará una escena nueva; después prepara y revisa su detalle H3.</small></div>}
    <details className="legacy-generation" open={preserveFace||undefined}><summary>Opciones avanzadas de la escena</summary><label className="face-toggle"><input type="checkbox" aria-label="Aplicar corrección facial CPU anterior" checked={preserveFace} disabled={!params} onChange={e => update("preserve_face", e.target.checked)}/><span>Corrección facial CPU anterior<small>Pasada opcional después de la escena base. El detalle H3 se genera y revisa desde el resultado.</small></span></label></details><div className="upload-grid">{[['video', 'Vídeo fuente', 'MP4, MOV, WebM · hasta 200 MB'], ['image', 'Personaje objetivo', 'PNG, JPG, WebP · hasta 25 MB'], ['face', 'Primer plano del rostro', 'Para el detalle H3 · PNG, JPG, WebP']].map(([kind, title, formats]) => {const asset = kind === 'video' ? video : kind === 'face' ? faceReference : reference;return <label className="upload-card" key={kind}><span className="upload-icon">{kind === 'video' ? '▷' : '⊞'}</span><strong>{uploading === kind ? 'Cargando…' : title}</strong><small>{asset ? `${asset.name} · ${asset.width} × ${asset.height}` : formats}</small><input type="file" accept={kind === 'video' ? '.mp4,.mov,.webm' : '.png,.jpg,.jpeg,.webp'} disabled={Boolean(uploading)} onChange={e => upload(e, kind)} aria-label={title}/><span className="upload-action">{asset ? 'Cambiar archivo ↗' : 'Seleccionar archivo ↗'}</span></label>;})}</div>
    <label htmlFor="subject">Sujeto a reemplazar <span>Describe a quién debe seguir SAM3</span></label><input className="text-input" id="subject" value={params?.subject || ''} onChange={e => update('subject', e.target.value)} maxLength={300}/><label htmlFor="prompt">Dirección de la escena <span>Conserva las etiquetas de H3</span></label><textarea id="prompt" value={params?.prompt || ''} onChange={e => update('prompt', e.target.value)} maxLength={4000}/><div className="prompt-footer"><span>Borrador guardado en este dispositivo</span><span>{params?.prompt?.length || 0}/4000</span></div>
    <div className="parameter-grid"><label>Resolución<select value={params?.resolution || '960x544'} onChange={e => update('resolution', e.target.value)}><option value="960x544">960 × 544</option><option value="864x480">864 × 480</option><option value="480x864">480 × 864</option></select></label><label>Duración<select value={params?.frames || 107} onChange={e => update('frames', Number(e.target.value))}>{durationOptions(video,params?.frames).map(option => <option key={option.frames} value={option.frames} disabled={option.disabled}>{option.label}</option>)}</select></label><label>Pasos<input type="number" min="8" max="30" value={params?.steps ?? 20} onChange={e => update('steps', Number(e.target.value))}/></label><label>Semilla<input type="number" min="0" max="9007199254740991" value={params?.seed ?? 314159} onChange={e => update('seed', Number(e.target.value))}/></label><label>Fuerza del LoRA<input type="number" min="0" max="1.5" step="0.1" value={params?.lora_strength ?? 1} onChange={e => update('lora_strength', Number(e.target.value))}/></label></div>
    <div className="approval-note"><span>◌</span><div><strong>Una toma a la vez. Toda la GPU para crear.</strong><p>Qwen se descarga antes de generar. ComfyUI libera sus modelos al terminar si la cola está vacía.</p></div></div><button className="primary" onClick={submit} disabled={disabled}>{submitting ? 'Enviando…' : !status?.generation_enabled ? 'Generación pendiente de validación' : activeJobs.length ? 'Generación en curso' : 'Generar Character Replacement'}<span>↗</span></button><button className="secondary check-button" onClick={validate} disabled={checking}>{checking ? 'Comprobando…' : 'Comprobar workflow'}</button>{validation && <div role="status" className={'validation ' + (validation.ok ? 'success' : 'error')}>{validation.ok ? `✓ ${validation.node_count} nodos compatibles.` : validation.errors.join(' · ')}<small>Comprobación estática; no ejecuta el workflow.</small></div>}</section>
    <section className="panel preview"><div className="panel-title"><h2>{hasH3Result ? 'Resultado' : isUpscale ? job.result_available ? 'Tu copia a 1080p' : 'Resultado original · Escalado en curso' : facialPreview ? facialPreview.title : isLong ? 'Vídeo completo · Character Replacement' : isGuidedHead ? 'Detalle H3 · Revisión del rostro' : isGuided ? job.face_result_available ? 'Tu composición estable' : 'Toma base · Composición en curso' : isAnchor && !job.face_result_available ? 'Toma base · Corrección de identidad' : isHead ? job.head_result_available ? 'Tu copia afinada' : 'Toma original · Afinado en curso' : job?.result_available ? 'Tu resultado' : 'Vista de la toma'}</h2><span className="tiny">{isUpscale ? "1080p / LOCAL" : isGeometry ? "GEOMETRÍA / CPU" : isGuidedHead ? "H3 / CABEZA" : isGuided ? "COMPOSICIÓN / CPU" : isAnchor ? "IDENTIDAD / CPU" : "H3 / LOCAL"}</span></div>{hasH3Result?<H3Result key={'h3-result-'+job.id} job={job} assets={assets} seekRequest={comparisonSeek?.jobId===job.id?comparisonSeek:null} onReviewSaved={review=>setJobs(current=>current.map(item=>item.id===job.id?{...item,quality_review:review}:item))}/>:<><div className="video-stage">{facialPreview || isHead || isAnchor || isGuidedHead || isGeometry || isUpscale || job?.result_available ? <video key={previewId + (job.head_result_available ? '-head' : job.face_result_available ? '-face' : '-base')} src={facialPreview ? facialPreview.src : `/api/jobs/${previewId}/result?variant=${(isAnchor && !job.face_result_available)||(isGuidedHead && !job.result_available)||(isGeometry && !job.result_available)||(isUpscale && !job.result_available) ? 'original' : 'final'}`} controls playsInline preload="metadata" aria-label="Resultado Character Replacement"/> : video ? <video key={video.id} src={`/api/assets/${video.id}`} controls playsInline preload="metadata" aria-label="Vídeo fuente"/> : <div className="empty-preview"><span>▷</span><strong>Todo empieza con una toma.</strong><p>Selecciona un vídeo y la imagen<br/>del personaje que quieres ver en él.</p></div>}<div className="video-caption"><span>{job ? labels[job.status].toUpperCase() : 'VÍDEO FUENTE'}</span><strong>{job ? job.stage : 'Tu escena, reinventada.'}</strong><small>{job ? new Date(job.created_at).toLocaleString() : 'Vídeo y referencia se guardan en tu PC'}</small>{isUpscale&&<small>{job.parameters.resolution?.replace('x',' × ')} píxeles · 24 fps</small>}{job&&<JobElapsed job={job}/>}{job?.progress?.max > 0 && (isLong || job.activity?.version!==1) && <div className="job-progress"><div style={{width: `${Math.min(100, job.progress.value/job.progress.max*100)}%`}}/><small>{job.progress.value} / {job.progress.max} · {isUpscale ? "Fotogramas de escalado" : isLong ? "Tramos · Etapa actual" : job.status === "refining" ? "Fotogramas de corrección" : "Nodo actual"}</small></div>}</div></div>{job?.error && <div role="alert" className="notice error job-error">{job.error}</div>}{job?.result_available&&<ResultComparison key={job.id+'-compare'} job={job} onReviewSaved={review=>setJobs(current=>current.map(j=>j.id===job.id?{...j,quality_review:review}:j))}/>}{!isUpscale&&<div className="preview-details"><div><strong>{previewReference ? 'Personaje de referencia' : 'Tu personaje objetivo'}</strong><p>{previewReference ? previewReference.name : 'La referencia guía la identidad y apariencia del reemplazo.'}</p></div>{previewReference && <img className="reference-thumb" src={`/api/assets/${previewReference.id}`} alt="Personaje objetivo"/>}</div>}{canDownload && <a className="download" href={`/api/jobs/${job.id}/result?download=true`}>{isUpscale ? "Descargar copia a 1080p ↓" : isLong ? "Descargar vídeo completo ↓" : isGeometry ? "Descargar copia con proporción ajustada ↓" : isGuidedHead ? "Descargar detalle H3 ↓" : isHead ? "Descargar copia con cabeza afinada ↓" : job.face_result_available ? "Descargar resultado corregido ↓" : "Descargar generación base ↓"}</a>}{!isHead && job?.face_result_available && <a className="download base-download" href={`/api/jobs/${job.id}/result?variant=original&download=true`}>Descargar generación base ↓</a>}</>}{isUpscale&&<JobCancelButton job={job} onCancel={cancel}/>}{job && <button className="secondary details-button" onClick={() => setTab('history')}>Ver versiones e historial</button>}{job?.parameters.direct_test&&job.status==='completed'&&<button className="primary" disabled={activeJobs.length>0} onClick={()=>reuseTake(job)}>Continuar con la configuración completa</button>}{!hasH3Result&&canDownload && <p className="identity-status">{isUpscale ? 'Copia a 1080p. Revisa el detalle en la comparación con el resultado original.' : isLong ? (job.long_review?.identity_approved ? 'Vídeo completo revisado: personaje aprobado.' : 'Vídeo completo generado: revisión del personaje pendiente.') : isGeometry && job.geometry_review?.approved ? 'Copia geométrica revisada.' : job.face_review?.exact_approved_video_match ? 'Esta copia coincide exactamente con el vídeo que ya aprobaste.' : isGuided && job.face_review?.identity_approved ? 'Composición estable revisada: rostro y gorra aprobados.' : (isHead || isGuidedHead) && job.head_review?.identity_approved ? 'Detalle facial revisado para esta copia.' : 'Vídeo generado. Identidad facial pendiente de revisión.'}</p>}{!isHead && job?.face_review && <div className="face-summary"><strong>{isGuided ? "Composición estable aplicada" : "Corrección facial aplicada"}</strong><p>{job.face_review.applied_count} fotogramas corregidos · {job.face_review.skipped_count} conservados por controles del sujeto. Revisa el rostro en movimiento.</p>{job.face_review.appearance_issue && <p role="status">{job.face_review.appearance_issue}</p>}</div>}{isHead && <div className="face-summary"><strong>{job.head_result_available ? job.head_review?.identity_approved && job.head_review?.accessory_approved ? 'Gorra y rostro revisados' : 'Cabeza afinada · Revisión pendiente' : 'Segunda pasada de cabeza'}</strong><p>{job.head_review ? `${job.head_review.applied_count} fotogramas compuestos · ${job.head_review.skipped_count} conservados. Revisa rostro y accesorios en movimiento.` : 'La vista muestra la toma original mientras se prepara la copia.'}</p>{job.head_review?.appearance_issue && <p>{job.head_review.appearance_issue}</p>}<button className="secondary details-button" onClick={() => setJobId(job.parameters.head_parent_id)}>Ver toma original</button></div>}{isGuided && job?.face_result_available && job.status==="completed" && <CompositionReview key={job.id+"-scene-review"} job={job} onUpdated={result=>setJobs(current=>current.map(j=>j.id===result.id?result:j))} onError={setError}/>}{isAnchor && <div className="face-summary"><strong>{isGuided ? "Detalle H3 aprobado · Composición estable" : "Identidad desde foto · Copia local"}</strong><p>{job.parameters.anchor_source_name}</p>{job.face_result_available && <><video src={`/api/jobs/${job.id}/anchor-detail`} controls playsInline preload="metadata" aria-label="Primer plano de identidad"/><a className="download" href={`/api/jobs/${job.id}/anchor-detail?download=true`}>Descargar primer plano ↓</a></>}<button className="secondary details-button" onClick={()=>setJobId(job.parameters.anchor_parent_id)}>Volver a la toma de origen</button></div>}
{isLong&&!job?.video_sequence_version&&<LongVideoReview key={job.id} job={job} onSeek={hasH3Result?seekComparison:undefined} busy={generationBlocked} onError={setError} onOpen={openTake} onUpdated={result=>setJobs(current=>current.map(j=>j.id===result.id?result:j))} onCreated={result=>{setJobs(current=>[result,...current.filter(j=>j.id!==result.id)]);setJobId(result.id);}}/>}{job?.parameters.long_parent_id&&<div className="face-summary">{Number.isInteger(job.parameters.long_keep_frames)&&<p>Este tramo aporta {(job.parameters.long_keep_frames/24).toFixed(2)} s al vídeo completo; el montaje recorta la duración adicional de preparación.</p>}<button className="secondary" onClick={()=>setJobId(job.parameters.long_parent_id)}>Volver al vídeo completo</button></div>}{isGuidedHead && job?.result_available && <><GuidedHeadReview job={job} onUpdated={result=>setJobs(current=>current.map(j=>j.id===result.id?result:j))} onError={setError}/><button className="secondary details-button" onClick={()=>setJobId(job.parameters.head_parent_id)}>Volver a la escena para componer</button></>}{job?.result_available && !job.parameters.operation && job.parameters.engine!=='h3_direct' && terminal.has(job.status) && <IdentityAnchor key={job.id+"-guided"} mode="guided" job={job} assets={assets} defaultFace={faceReference} available={status?.generation_enabled&&status?.face_processing?.available} busy={generationBlocked} onError={setError} onCreated={result=>{setJobs(current=>[result,...current.filter(j=>j.id!==result.id)]);setJobId(result.id);}}/>}{job?.result_available && !job.parameters.operation && <GuidedComposition job={job} busy={generationBlocked} onError={setError} onCreated={result=>{setJobs(current=>[result,...current.filter(j=>j.id!==result.id)]);setJobId(result.id);}}/>}
{job?.result_available && !job.parameters.operation && job.parameters.engine!=='h3_direct' && <LegacyMethods>{!isHead && job?.result_available && job.parameters.preserve_face && terminal.has(job.status) && <button className="secondary details-button" onClick={() => retryFace(job.id)} disabled={activeJobs.length > 0}>Reintentar solo la corrección facial</button>}{!isHead && job?.result_available && ['completed','failed'].includes(job.status) && job.parameters.preserve_face && <div className="face-summary"><strong>Afinar cabeza · Experimental</strong><p>Crea una segunda pasada con tu primer plano y conserva esta toma. Se aplica al primer plano continuo, antes del primer corte.</p><label htmlFor="head-direction">Detalles a conservar</label><textarea id="head-direction" maxLength={1000} value={headDirection} onChange={e => setHeadDirection(e.target.value)}/><button className="secondary details-button" onClick={refineHead} disabled={submitting || activeJobs.length > 0 || !status?.generation_enabled || !status?.comfy.online || !status?.ollama.online || !status?.face_processing?.available || !(job.parameters.face_reference_id || faceReference?.id)}>{submitting ? 'Enviando…' : 'Afinar cabeza en una copia'}</button></div>}{job?.result_available && !job.parameters.operation && job.parameters.engine!=='h3_direct' && terminal.has(job.status) && <IdentityAnchor key={job.id} job={job} assets={assets} available={status?.generation_enabled && status?.face_processing?.available} busy={activeJobs.length > 0}
onError={setError} onCreated={result => {setJobs(current => [result,...current.filter(j=>j.id!==result.id)]);setJobId(result.id);}}/>}<IdentityReview jobId={job.id}/></LegacyMethods>}</section></div></StudioSlot>}
    {tab === 'studio' && studioMode==='character' && job?.parameters.engine==='h3_direct' && !job.parameters.operation && !isLong && job.status==='completed' && <StudioSlot name="recent"><DirectReview key={job.id} job={job} onError={setError} onUpdated={result=>setJobs(current=>current.map(j=>j.id===result.id?result:j))}/></StudioSlot>}

    {tab==='studio'&&studioMode==='character'&&job?.parameters.engine==='h3_direct'&&job.status==='completed'&&job.result_available&&<StudioSlot name="recent"><ImproveResult key={job.id+'-improve'} job={job} assets={assets} busy={generationBlocked} onRefine={prepareResultRefinement} onAsset={asset=>setAssets(current=>[asset,...current.filter(a=>a.id!==asset.id)])} onError={setError} onOpen={openTake} onUpdated={result=>setJobs(current=>current.map(j=>j.id===result.id?result:j))} onCreated={result=>{setJobs(current=>[result,...current.filter(j=>j.id!==result.id)]);setJobId(result.id);}}/></StudioSlot>}
    {tab==='studio'&&studioMode==='character'&&isUpscale&&job.status!=='completed'&&<StudioSlot name="recent"><Upscale1080 key={job.id+'-upscale-status'} job={job} busy={generationBlocked} onOpen={openTake} onError={setError}/></StudioSlot>}
    {tab==='studio'&&studioMode==='character'&&(['h3_direct','h3_ninja','h3_alisson','h3_mdmz'].includes(studioEngine)||isSecoursesEngine(studioEngine))&&<StudioSlot name="recent"><RecentTakes jobs={jobs} mode="character" onOpen={id=>{openTake(id);setTab('studio');}}/></StudioSlot>}
    {tab==='history'&&<><SonnyGalleryLinks workspaceId={workspace.id}/><SonnyGalleryLinks workspaceId={workspace.id} targetId="jafet" targetName="Jafet"/></>}
    {historyVisited&&<SceneHistory active={tab==='history'} currentJobId={job?.id} jobs={jobs} assets={assets} busy={submitting||Boolean(uploading)||generationBlocked} onOpen={openHistoryTake} onReuse={reuseTake} onCancel={cancel}/>}
    {tab === 'system' && <section className="panel system-panel"><div className="panel-title"><h2>Memoria y acceso</h2><button className="secondary" onClick={refresh}>Actualizar</button></div><h3>{device?.name || 'GPU no disponible'}</h3><div className="memory-bar"><span className="transition-all duration-500" style={{width: `${used}%`}}/></div><p>{gb(device?.vram_free)} GB libres según ComfyUI. El motor comparte tu GPU con las otras aplicaciones.</p><div className="system-rows"><div><span>Modelos Ollama cargados</span><strong>{loaded.length ? loaded.map(m => m.name).join(', ') : status?.ollama.online ? 'Ninguno' : 'Sin datos'}</strong></div><div><span>ComfyUI</span><strong>{status?.comfy.data?.system?.comfyui_version || '—'}</strong></div><div><span>Android vía Tailscale</span><strong>{status?.remote.activation === 'active' ? 'HTTPS privado · Puerto 10000' : 'Pendiente de activar · Puerto 10000'}</strong></div><div><span>Acceso a este Studio</span><strong>PC local y cuenta del propietario</strong></div></div><p className="system-note">Object Swap dispone de reemplazo de personaje y refinamiento facial. Motion Transfer utiliza Bunny como predeterminado y ofrece Personaje + Escenario como procesamiento por tramos de 5 segundos.</p></section>}
    {tab === 'workflow' && <section className="panel workflow-panel"><div className="panel-title"><h2>Character Replacement · Copia preservada</h2><span className="tag">{workflow?.integrity_ok ? 'SHA256 OK' : 'CONSULTANDO'}</span></div><p>Base con {workflow?.node_count ?? '—'} nodos. El original se conserva intacto; cada trabajo crea su propia copia con parámetros y salidas únicas.</p><div className="node-grid">{workflow?.nodes.map((n, i) => <div key={n.id}><span>{String(i + 1).padStart(2, '0')}</span><strong>{n.title}</strong><small>{n.type}</small></div>)}</div><button className="primary" onClick={validate} disabled={checking}>{checking ? 'Comprobando…' : 'Comprobar compatibilidad actual'}<span>↗</span></button>{validation && <p role="status" className={validation.ok ? 'success' : 'error'}>{validation.ok ? 'Todos los nodos y opciones están disponibles.' : validation.errors.join(' · ')}</p>}</section>}
    {tab==='studio'&&cinemaTake&&job?.id===cinemaTake.id&&<CinemaPresentation key={'take-cinema-'+job.id} job={job} assets={assets} method={takeLabel(job)} initialTime={cinemaTake.time} onClose={()=>setCinemaTake(null)}/>}
    <StudioSlot name="bottom"><section className="queue-strip"><div><span className="queue-icon">≡</span><div><strong>{studioMode==='video_upscale'?'Cola de GEN STUDIO':'Cola del motor'}</strong><small>{studioMode==='video_upscale' ? `${activeJobs.length} ${activeJobs.length===1?'trabajo activo':'trabajos activos'} · Consulta el historial para ver la cola` : status?.queue.online ? `${engineQueue.queue_running?.length || 0} en ejecución · ${engineQueue.queue_pending?.length || 0} pendientes · ${activeJobs.length} en GEN STUDIO` : 'Esperando conexión'}</small></div></div><span className="muted">Actualización cada 3 s</span></section><StudioSlot name="bottom"></StudioSlot><footer><span>Diseñado para crear cerca de ti.</span><span>Windows <span className="footer-dot">·</span> Android vía Tailscale</span></footer></StudioSlot></StudioWorkspace></div></main></div>;
}
createRoot(document.getElementById('root')).render(<WorkspaceGate>{workspace=><ParodyBoundary key={workspace.id} workspace={workspace}><QwenModelsProvider key={workspace.id}><App workspace={workspace}/></QwenModelsProvider></ParodyBoundary>}</WorkspaceGate>);


