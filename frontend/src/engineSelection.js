import {PIKA_DETAILED,detailedOption,pikaRefMethod,isPikaRefMethod} from './pikaRefMethods.js';
import {VISUAL_ENGINES,visualEngineFor} from './secoursesVisualMethods.js';
import {option as viggleOption} from './viggleState.js';
export const objectSwapEngines = [
  viggleOption,
  {id:'pika_ref2va',label:'Pika H3 Flex · Experimental',detail:'Object Swap · 480p / 768p · Qwen y continuidad por tramos'},
  detailedOption,
  {id:'h3_secourses',label:'SECourses · Genjutsu',detail:'Guía SAM3 · Rápida / Normal · Experimental'},
  ...VISUAL_ENGINES,
  {id:'h3_direct',label:'H3 directo',detail:'Realismo / referencia completa'},
  {id:'h3_alisson',label:'Alisson v1.1 · Experimental',detail:'1 personaje · Con o sin cortes'},
  {id:'h3_ninja',label:'Refinamiento Facial',detail:'Receta 2 · Experimental'},
];
export function selectObjectSwapEngine(current,jobId,next){
  if(!objectSwapEngines.some(item=>item.id===next))throw new Error('Motor desconocido');
  // Detach even while the selected history item is still loading.
  return {engine:next,jobId:next===current?jobId:'draft'};
}

export const activeObjectSwap=p=>!['pika_motion','viggle_motion'].includes(p?.operation)&&objectSwapEngines.some(engine=>engine.id===(pikaRefMethod(p)||visualEngineFor(p)||p?.engine));
