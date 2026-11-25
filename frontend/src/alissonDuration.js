import {ALISSON_MAX_FRAMES,alissonSourceFrames} from './alissonState.js';

export function alissonDurationOptions(form,video,maxFrames=ALISSON_MAX_FRAMES){
  const limit=Math.min(ALISSON_MAX_FRAMES,maxFrames),total=alissonSourceFrames(video);
  const remaining=total-Math.round(form.start_seconds*24);
  return [
    ...[120,240,360].map(frames=>({value:String(frames),frames,label:`${frames/24} segundos`,disabled:frames>limit||!Number.isFinite(remaining)||frames>remaining})),
    {value:'full',frames:total,label:total?`Vídeo completo · ${(total/24).toFixed(2)} s${total>limit?` (máximo ${limit/24} s)`:''}`:'Vídeo completo · Selecciona un vídeo',disabled:total<5||total>limit},
    {value:'custom',label:'Personalizada',disabled:false},
  ];
}

export function alissonDurationValue(form,video,maxFrames=ALISSON_MAX_FRAMES){
  const options=alissonDurationOptions(form,video,maxFrames),full=options.find(o=>o.value==='full');
  if(!full.disabled&&form.start_seconds===0&&form.frames===full.frames)return 'full';
  return options.find(o=>o.value!=='full'&&o.frames===form.frames&&!o.disabled)?.value||'custom';
}

export function chooseAlissonDuration(form,video,value,maxFrames=ALISSON_MAX_FRAMES){
  const option=alissonDurationOptions(form,video,maxFrames).find(o=>o.value===value);
  if(!option||option.disabled||value==='custom')return form;
  return {...form,frames:option.frames,...(value==='full'?{start_seconds:0}:{})};
}
