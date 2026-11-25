import ViggleVideo from './ViggleVideo.jsx';
import {option as viggleOption,isViggle} from './viggleState.js';
import {PIKA_DETAILED,detailedOption,pikaRefMethod,isPikaRefMethod} from './pikaRefMethods.js';
import React,{useEffect,useRef,useState} from 'react';
import PikaRefVideo from './PikaRefVideo.jsx';
import TaraStudio from './TaraStudio.jsx';
import {workspaceLocalStorage as storage} from './workspaceStorage.js';
import MotionStudio from './MotionStudio.jsx';
import MotionLayersStudio from './MotionLayersStudio.jsx';
import SECoursesSwap from './SECoursesSwap.jsx';
import EnginePicker from './EnginePicker.jsx';
import {VISUAL_ENGINES,isVisualMotion} from './secoursesVisualMethods.js';
import './motionLayers.css';

export const motionMethodOptions=[
 viggleOption,
 {id:'pika_ref2va',label:'Pika H3 Flex · Experimental',detail:'480p / 768p · Qwen · Movimiento y escena de destino'},
 detailedOption,
 {id:'bunny',label:'Bunny · Predeterminado',detail:'Continuidad en vídeos de hasta 60 segundos'},
 {id:'tara',label:'TARA-H3 · Experimental',detail:'Alineación temporal · Tramos de 5 segundos'},
 {id:'layers',label:'Personaje + Escenario · Experimental',detail:'Personaje y escenario en pasadas separadas'},
 {...VISUAL_ENGINES[0],id:'visual_experimental'},
];
function preferred(){try{const value=storage.getItem('motion-method-v1');return ['tara','layers'].includes(value)?value:'bunny';}catch{return 'bunny';}}
export default function MotionMethods(props){
 const viggleJob=isViggle(props.job);
 const pikaJob=pikaRefMethod(props.job);const selectedPika=props.selectedRef2va===true?'pika_ref2va':props.selectedRef2va;
 const taraJob=props.job?.parameters?.transfer_method==='tara';
 const layersJob=props.job?.parameters?.operation==='motion_layers';
 const visualJob=isVisualMotion(props.job);
 const initial=viggleJob||props.selectedViggle?'viggle_remake':pikaJob||selectedPika?pikaJob||selectedPika:visualJob?'visual_experimental':layersJob?'layers':taraJob?'tara':props.selectedVisualMethod==='experimental'?'visual_experimental':preferred();
 const [method,setMethod]=useState(initial),[visited,setVisited]=useState(initial==='layers'),[pending,setPending]=useState(false),[taraVisited,setTaraVisited]=useState(initial==='tara');
 const container=useRef(null);
 useEffect(()=>{if(props.job?.id){setMethod(viggleJob?'viggle_remake':pikaJob?pikaJob:visualJob?'visual_experimental':layersJob?'layers':taraJob?'tara':'bunny');if(layersJob)setVisited(true);if(taraJob)setTaraVisited(true);}},[props.job?.id]);
 useEffect(()=>{container.current?.querySelectorAll('[hidden] video').forEach(video=>video.pause());},[method]);
 function change(value){setMethod(value);try{storage.setItem('motion-method-v1',value);}catch{}if(value==='tara')setTaraVisited(true);if(value==='layers')setVisited(true);props.onMethodChange?.(value);props.onViewChange?.('edit');}
 return <div className="motion-methods" ref={container}>
  <div data-studio-pane="edit"><EnginePicker value={method} onChange={change} engines={motionMethodOptions} ariaLabel="Método de Motion Transfer" compact disabled={props.busy||Boolean(props.uploading)||pending}/></div>
  {method!=='viggle_remake'&&!isPikaRefMethod(method)&&(!taraJob||method==='bunny')&&<div hidden={method!=='bunny'}><MotionStudio {...props} job={layersJob||taraJob||visualJob||pikaJob||viggleJob?null:props.job}/></div>}
  {taraVisited&&<div hidden={method!=='tara'}><TaraStudio {...props} job={taraJob?props.job:null} enabled={method==='tara'} onPending={setPending}/></div>}
  {visited&&<div hidden={method!=='layers'}><MotionLayersStudio {...props} job={layersJob?props.job:null} enabled={method==='layers'} onPending={setPending}/></div>}
  {method==='viggle_remake'&&<ViggleVideo {...props} mode="motion" key={'viggle-motion-'+(viggleJob?props.job.id:'draft')} job={viggleJob?props.job:null} onCreated={result=>{props.onCreated(result);props.onViewChange?.('result');}}/>}
  {isPikaRefMethod(method)&&<PikaRefVideo {...props} mode="motion" method={method} key={method+'-motion-'+(pikaJob===method?props.job.id:'draft')} job={pikaJob===method?props.job:null} onCreated={result=>{props.onCreated(result);props.onViewChange?.('result');}}/>}
  {method==='visual_experimental'&&<SECoursesSwap {...props} key={'visual-motion-'+(visualJob?props.job.id:'draft')} visualMethod="experimental" mode="motion" job={visualJob?props.job:null} onPending={setPending} onSelectMethod={props.onSelectVisualMethod} onCreated={result=>{props.onCreated(result);props.onViewChange?.('result');}}/>}
 </div>;
}
