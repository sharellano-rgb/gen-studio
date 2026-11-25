import {isVisualMotion} from './secoursesVisualMethods.js';
import {takeLabel} from './sceneResults.js';
import {isNinja,ninjaLabel} from './ninjaState.js';
import React from 'react';
import {hasApprovedMotion,hasReviewedH3,resultUrl} from './resultVersion.js';
export default function RecentTakes({jobs,mode,onOpen}){
  const motionTake=job=>job.parameters.engine==='viggle_motion'||isVisualMotion(job.parameters);
  const takes=jobs.filter(j=>j.result_available&&j.status==='completed'&&(mode==='motion'?motionTake(j):!motionTake(j))).slice(0,6);
  return <section className="recent-takes" aria-label="Tus tomas recientes"><div className="stage-toolbar"><h2>Tus tomas</h2><span>{takes.length} recientes · Guardadas en tu PC</span></div>
    {takes.length?<div className="take-grid">{takes.map(j=><button className="take-card" key={j.id} onClick={()=>onOpen(j.id)} aria-label={'Abrir toma '+j.id.slice(0,8)}>
      <video src={resultUrl(j)+'#t=0.001'} muted playsInline preload="metadata" aria-hidden="true" tabIndex={-1}/>
      <span>{mode==='motion'&&!isVisualMotion(j.parameters)?hasApprovedMotion(j)||hasReviewedH3(j)?'Motion · Giro aprobado':j.parameters.transfer_method==='h3_multiview'?j.parameters.motion_h3_source_job?'Motion · Ajuste de tiempo':j.parameters.motion_h3_segmented?'Motion · H3 por tramos':'Motion · H3 experimental':'Motion Transfer':takeLabel(j)}</span><small>{new Date(j.created_at).toLocaleDateString()} · {(j.scene_resolution||j.parameters.resolution||'').replace('x',' × ')}</small>
    </button>)}</div>:<div className="gallery-empty"><span>▧</span><p>Tus resultados aparecerán aquí.<br/>Cada generación conserva su propia copia.</p></div>}
  </section>;
}
