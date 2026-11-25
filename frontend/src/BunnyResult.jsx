import {UpscalingButton} from './Upscaling.jsx';
import React,{useRef,useState} from 'react';
import ResultComparison from './ResultComparison.jsx';
import CinemaPresentation from './CinemaPresentation.jsx';
import {bunnyBoundaries,bunnySourceStart} from './bunnyReview.js';
import {bunnyTimeline,initialSegment} from './segmentTimeline.js';
import {openNotesForSegment,categoryLabel} from './qualityNotes.js';
import SegmentTimeline from './SegmentTimeline.jsx';
import './bunnyResult.css';

export default function BunnyResult({job,assets=[],onReviewSaved}) {
 const [mode,setMode]=useState('final'),[visited,setVisited]=useState(false),[seekRequest,setSeekRequest]=useState(null),[selected,setSelected]=useState(null),[cinema,setCinema]=useState(false),p=job.parameters;
 const container=useRef(null),finalVideo=useRef(null),finalTime=useRef(0);
 const boundaries=bunnyBoundaries(job),segments=bunnyTimeline(job),notes=job.quality_review?.notes||[];
 const current=segments.find(s=>s.index===selected)||initialSegment(segments,notes);
 function changeMode(next){if(next===mode)return;if(finalVideo.current){finalTime.current=finalVideo.current.currentTime;finalVideo.current.pause();}if(next==='compare')setVisited(true);setMode(next);}
 function seek(frame){changeMode('compare');setSeekRequest({frame,token:crypto.randomUUID()});requestAnimationFrame(()=>{container.current?.focus({preventScroll:true});container.current?.scrollIntoView({block:'start',behavior:'auto'});});}
 return <section ref={container} tabIndex={-1} className="bunny-result-review" aria-label="Revisar resultado Bunny">
  {mode==='final'&&<video className="result-player-video" ref={finalVideo} key={job.id} controls playsInline preload="metadata" src={'/api/jobs/'+job.id+'/result'} aria-label="Vídeo final de Bunny" onLoadedMetadata={e=>{if(finalTime.current>0&&Number.isFinite(e.currentTarget.duration))e.currentTarget.currentTime=Math.min(finalTime.current,e.currentTarget.duration);}}/>}
  <div className="result-player-toolbar"><UpscalingButton jobId={job.id}/>
  <button type="button" className="cinema-launch" onClick={()=>{const current=finalVideo.current||container.current?.querySelector('.result-comparison [data-comparison-video="result"]');if(current?.readyState>=1)finalTime.current=current.currentTime;current?.pause();setCinema(true);}}>Ver en Cine <span aria-hidden="true">↗</span></button>
  <div className="bunny-result-modes" role="group" aria-label="Vista del resultado">
   <button type="button" aria-pressed={mode==='final'} onClick={()=>changeMode('final')}>Vídeo final</button>
   <button type="button" aria-pressed={mode==='compare'} onClick={()=>changeMode('compare')}>Comparar movimiento</button>
  </div>
  <a className="bunny-result-download" href={'/api/jobs/'+job.id+'/result?download=true'}>Descargar vídeo ↓</a>
  </div>
  <div className="bunny-result-summary"><span>{Number((p.frames/24).toFixed(2))} s</span>{(job.scene_resolution||p.resolution)&&<span>{(job.scene_resolution||p.resolution).replace('x',' × ')} px</span>}<span>24 fps</span>{boundaries.length>0&&<span>{boundaries.length+1} tramos</span>}</div>
  {visited&&<ResultComparison job={job} active={mode==='compare'&&!cinema} seekRequest={seekRequest} onReviewSaved={onReviewSaved}/>}
  {current&&<><SegmentTimeline segments={segments} frames={p.frames} selected={current.index} onSelect={setSelected} notes={notes} videoId={p.video_id} startSeconds={bunnySourceStart(p.start_seconds)}/>
   <section className="bunny-segment-detail" aria-label={'Revisar tramo '+(current.index+1)}><h4>Tramo {current.index+1} · {(current.start/24).toFixed(2)}–{((current.start+current.keep)/24).toFixed(2)} s</h4>
    <div className="segment-review-tools"><UpscalingButton jobId={job.id} segmentIndex={current.index} previewStart={current.start/24}/><button type="button" className="secondary" onClick={()=>seek(current.start)}>Comparar inicio del tramo {current.index+1}</button>{current.start>0&&<button type="button" className="secondary" onClick={()=>seek(current.start-1)}>Revisar unión a {(current.start/24).toFixed(2)} s</button>}</div>
    {openNotesForSegment(notes,current).map(note=><div className="segment-quality-note" key={note.id}><p>{categoryLabel(note.category)}{note.subject?' · '+note.subject:''}{note.comment?' · '+note.comment:''}</p><button type="button" className="secondary" onClick={()=>seek(note.frame)}>Comparar marca en {(note.frame/24).toFixed(2)} s</button></div>)}
   </section></>}
  {cinema&&<CinemaPresentation key={job.id} job={job} assets={assets} method="Motion Transfer · Bunny" initialTime={finalTime.current} onClose={()=>setCinema(false)}/>}
 </section>;
}
