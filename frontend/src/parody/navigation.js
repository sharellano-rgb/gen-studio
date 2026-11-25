export const navigationEvent='gen-parody-navigation';
export function isParody(href){return new URL(href).searchParams.get('mode')==='parody';}
export function parodyUrl(href,projectId=''){
 const u=new URL(href);u.searchParams.set('page','studio');u.searchParams.set('mode','parody');
 if(projectId)u.searchParams.set('parody_project',projectId);else u.searchParams.delete('parody_project');
 return u.pathname+u.search+u.hash;
}
export function openParody(){
 const previous={url:location.pathname+location.search+location.hash,state:history.state};
 history.pushState({parodyReturn:previous},'',parodyUrl(location.href));
 window.dispatchEvent(new Event(navigationEvent));
}
export function leaveParody(){
 if(history.state?.parodyReturn){history.back();return;}
 const u=new URL(location.href);u.searchParams.delete('mode');u.searchParams.delete('parody_project');u.searchParams.delete('parody_view');
 history.replaceState(history.state,'',u.pathname+u.search+u.hash);window.dispatchEvent(new Event(navigationEvent));
}
export function selectProject(id){history.replaceState(history.state,'',parodyUrl(location.href,id));window.dispatchEvent(new Event(navigationEvent));}

export function secondsToMs(value){
 if(!/^\d+(?:\.\d{1,3})?$/.test(String(value)))throw Error('Usa segundos con hasta tres decimales.');
 const [seconds,fraction='']=String(value).split('.');const ms=Number(seconds)*1000+Number(fraction.padEnd(3,'0'));
 if(!Number.isSafeInteger(ms)||ms>120000)throw Error('El tiempo debe estar entre 0 y 120 segundos.');
 return ms;
}
