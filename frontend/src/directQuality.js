import {directSourceResolution} from './videoOrientation.js';
const resolutions=['864x480','480x864','512x512','960x544','1344x768','768x1344','1024x1024'];
export function directQualityOptions(video,settings){
  return resolutions.filter(value=>!settings.match_source_orientation||directSourceResolution(video,value)===value).map(value=>{
    const [w,h]=value.split('x').map(Number);
    const level=Math.max(w,h)>=1000?'Alta':value==='960x544'?'Equilibrada':'Rápida';
    return {value,label:`${level} · ${w} × ${h} px`};
  });
}
export function chooseDirectQuality(settings,resolution,video){
  if(!resolutions.includes(resolution))return settings;
  return {...settings,direct_high_quality:false,resolution:settings.match_source_orientation?directSourceResolution(video,resolution):resolution};
}
