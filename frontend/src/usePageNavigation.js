import {useCallback,useLayoutEffect,useRef,useState} from 'react';
import {routeFromLocation,routeUrl,routeState,routeKey,positionKey,patchRoute,takePatch} from './studioNavigation.js';

// Routes contain navigation only. Drafts and cached data remain scoped to the workspace.
export default function usePageNavigation({workspace,initial}){
 const [route,setRoute]=useState(()=>routeFromLocation(window.location.search,window.history.state,workspace,initial));
 const [historyVisited,setHistoryVisited]=useState(route.tab==='history');
 const current=useRef(route),positions=useRef({}),action=useRef({kind:'replace'}),destination=useRef(null);
 const move=useCallback((patch,{replace=false,reset=false,pop=false}={})=>{
  const previous=current.current,next=patchRoute(previous,typeof patch==='function'?patch(previous):patch);
  if(routeKey(previous)===routeKey(next))return;
  if(positionKey(previous)!==positionKey(next)){
   const focused=document.activeElement,anchor=focused?.closest('.scene-library')?(focused.closest('button,a,input,select,textarea,summary')||focused):null;
   positions.current[positionKey(previous)]={y:window.scrollY,anchor,offset:anchor?.getBoundingClientRect().top};
   destination.current={position:reset?null:positions.current[positionKey(next)]};
  }
  current.current=next;action.current={kind:pop?'pop':replace?'replace':'push'};
  if(next.tab==='history')setHistoryVisited(true);
  setRoute(next);
 },[]);
 useLayoutEffect(()=>{
  const previous=window.history.scrollRestoration;window.history.scrollRestoration='manual';
  const pop=()=>move(routeFromLocation(window.location.search,window.history.state,workspace,initial),{pop:true});
  window.addEventListener('popstate',pop);
  return()=>{window.removeEventListener('popstate',pop);window.history.scrollRestoration=previous;};
 },[workspace,move]);
 useLayoutEffect(()=>{
  const kind=action.current?.kind||'replace',url=routeUrl(window.location.href,route,{keepHash:kind!=='push'});
  const existing=window.location.pathname+window.location.search+window.location.hash;
  const method=kind==='push'&&url!==existing?'pushState':'replaceState';
  window.history[method](routeState(window.history.state,route,workspace),'',url);action.current=null;
  if(destination.current){
   const position=destination.current.position,anchor=position?.anchor,visible=anchor?.isConnected&&anchor.getClientRects().length;
   const top=visible?window.scrollY+anchor.getBoundingClientRect().top-position.offset:position?.y||0;
   window.scrollTo({top:Math.max(0,top),behavior:'instant'});
   if(route.tab==='history'&&visible)anchor.focus({preventScroll:true});
   destination.current=null;
  }
 },[route,workspace]);
 const field=(name,value)=>move(previous=>({[name]:typeof value==='function'?value(previous[name]):value}));
 return {...route,historyVisited,setTab:(tab,options)=>move({tab},options),setJobId:value=>move(previous=>takePatch(previous,value)),setStudioMode:value=>field('studioMode',value),setStudioEngine:value=>field('studioEngine',value),setStudioView:value=>field('studioView',value),replaceNavigation:patch=>move(patch,{replace:true})};
}
