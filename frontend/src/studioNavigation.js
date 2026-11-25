import {PIKA_DETAILED,isPikaRefMethod} from './pikaRefMethods.js';
import {VISUAL_ENGINES} from './secoursesVisualMethods.js';
import {takeFromSearch} from './takeLink.js';
export const NAVIGATION_KEY='genStudioNavigation';
const pages=['studio','images','image-history','history','system','workflow'],methods=['h3_direct','h3_secourses',...VISUAL_ENGINES.map(method=>method.id),'h3_alisson','h3_ninja','h3_sam3','h3_mdmz','pika_h3_flex','h3_i2va','pika_ref2va',PIKA_DETAILED,'viggle_remake'];
const ownKeys=['page','take','mode','method','view'];
export function normalizeRoute(value={}){
 const id=value.jobId==='draft'?'draft':takeFromSearch('?take='+encodeURIComponent(value.jobId||''));
 // A loaded motion take overrides the previously selected image-to-video engine.
 const mode=value.studioMode==='video_upscale'?'video_upscale':value.studioMode==='motion'&&value.studioEngine!=='h3_visual_faithful'?'motion':value.studioMode==='image_video'||['pika_h3_flex','h3_i2va'].includes(value.studioEngine)?'image_video':'character';
 return {tab:pages.includes(value.tab)?value.tab:'studio',jobId:id,studioMode:mode,studioEngine:mode==='image_video'?(value.studioEngine==='h3_i2va'?'h3_i2va':'pika_h3_flex'):!['pika_h3_flex','h3_i2va'].includes(value.studioEngine)&&methods.includes(value.studioEngine)&&!(value.studioEngine==='h3_mdmz'&&(!id||id==='draft'))?value.studioEngine:'h3_direct',studioView:value.studioView==='result'?'result':'edit'};
}
export function routeFromLocation(search,state,workspace,fallback={}){
 const saved=state?.[NAVIGATION_KEY],base=normalizeRoute(saved?.version===1&&saved.workspace===workspace?saved.route:fallback),query=new URLSearchParams(search);
 if(!ownKeys.some(key=>query.has(key)))return base;
 const tab=pages.includes(query.get('page'))?query.get('page'):'studio';
 if(tab!=='studio')return {...base,tab};
 const jobId=query.has('take')?(query.get('take')==='draft'?'draft':takeFromSearch(search)||'draft'):query.has('mode')||query.has('method')?'draft':base.jobId;
 return normalizeRoute({...base,tab,jobId,studioMode:query.has('mode')?query.get('mode'):(jobId==='draft'?'character':base.studioMode),studioEngine:query.has('method')?query.get('method'):(jobId==='draft'?'h3_direct':base.studioEngine),studioView:query.get('view')==='edit'?'edit':query.get('view')==='result'?'result':jobId&&jobId!=='draft'?'result':'edit'});
}
export function routeUrl(href,value,{keepHash=false}={}){
 const route=normalizeRoute(value),url=new URL(href);
 ownKeys.forEach(key=>url.searchParams.delete(key));
 if(route.tab!=='studio')url.searchParams.set('page',route.tab);
 else {
  url.searchParams.set('take',route.jobId||'draft');
  if(!route.jobId||route.jobId==='draft'){
   if(['motion','image_video','video_upscale'].includes(route.studioMode))url.searchParams.set('mode',route.studioMode);
   if((route.studioMode!=='motion'||route.studioEngine==='viggle_remake'||isPikaRefMethod(route.studioEngine)||VISUAL_ENGINES.some(method=>method.id===route.studioEngine))&&route.studioEngine!=='h3_direct'&&route.studioEngine!=='h3_sam3')url.searchParams.set('method',route.studioEngine);
   if(route.studioView==='result')url.searchParams.set('view','result');
  }else if(route.studioView==='edit')url.searchParams.set('view','edit');
 }
 if(!keepHash)url.hash='';
 return url.pathname+url.search+url.hash;
}
export function routeState(state,route,workspace){return {...(state&&typeof state==='object'?state:{}),[NAVIGATION_KEY]:{version:1,workspace,route:normalizeRoute(route)}};}
export const routeKey=route=>JSON.stringify(normalizeRoute(route));
export function positionKey(route){return route.tab==='studio'?['studio',route.jobId||'draft',route.studioView,...(!route.jobId||route.jobId==='draft'?[route.studioMode,route.studioEngine]:[])].join(':'):route.tab;}
export function patchRoute(route,patch){return normalizeRoute({...route,...patch});}
export function takePatch(route,value){const id=typeof value==='function'?value(route.jobId):value;return id===route.jobId?{}:{jobId:id,studioView:id&&id!=='draft'?'result':'edit'};}
