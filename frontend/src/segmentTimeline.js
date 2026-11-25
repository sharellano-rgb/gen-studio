import {bunnyBoundaries,bunnySourceStart} from './bunnyReview.js';
import {openNotesForSegment} from './qualityNotes.js';
export function validTimeline(segments,frames){
 if(!Array.isArray(segments)||!segments.length||!Number.isInteger(frames)||frames<1)return [];
 let cursor=0;
 for(let i=0;i<segments.length;i++){const s=segments[i];if(s.index!==i||s.start!==cursor||!Number.isInteger(s.keep)||s.keep<1)return [];cursor+=s.keep;}
 return cursor===frames?segments:[];
}
export function timelineSourceFrame(segment,startSeconds=0){return Math.round(bunnySourceStart(startSeconds)*24)+segment.start;}
export function segmentThumbnailUrl(videoId,segment,startSeconds=0){return videoId?'/api/direct/target-frame?video_id='+encodeURIComponent(videoId)+'&frame='+timelineSourceFrame(segment,startSeconds):null;}
export function segmentStatus(segment){
 if(segment.passthrough)return {kind:'original',label:'Original'};
 if(segment.analysis_status?.error||segment.job?.status==='failed')return {kind:'error',label:'Con error'};
 if(segment.job?.status==='completed'&&segment.job.result_available)return {kind:'completed',label:'Generado'};
 if(segment.job?.status==='cancelled')return {kind:'cancelled',label:'Detenido'};
 if(segment.job?.status&& !['queued','completed'].includes(segment.job.status))return {kind:'active',label:segment.job.status==='submission_unknown'?'Por confirmar':'En proceso'};
 return {kind:'pending',label:'Pendiente'};
}
export function initialSegment(segments,notes=[]){return segments.find(s=>segmentStatus(s).kind==='error')||segments.find(s=>openNotesForSegment(notes,s).length)||segments[0]||null;}
export function regenerationImpact(segment,segments){
 const ids=segment?.regeneration_indices;
 if(!Array.isArray(ids)||!ids.length||!ids.includes(segment.index)||new Set(ids).size!==ids.length||ids.some(id=>!Number.isInteger(id)||!segments.some(s=>s.index===id)))return null;
 const sorted=[...ids].sort((a,b)=>a-b),affected=sorted.map(id=>segments.find(s=>s.index===id));
 const label=sorted.length===1?'el tramo '+(sorted[0]+1):sorted.every((id,i)=>!i||id===sorted[i-1]+1)?'los tramos '+(sorted[0]+1)+'–'+(sorted.at(-1)+1):'los tramos '+sorted.map(i=>i+1).join(', ');
 return {indices:sorted,frames:affected.reduce((n,s)=>n+s.keep,0),label};
}
export function bunnyTimeline(job){
 const boundaries=bunnyBoundaries(job);if(!boundaries.length)return [];
 const starts=[0,...boundaries],ends=[...boundaries,job.parameters.frames];
 return starts.map((start,index)=>({index,start,keep:ends[index]-start,passthrough:false,job:{status:'completed',result_available:true}}));
}
export function segmentHashIndex(hash,jobId,segments){
 const prefix='#long-segment-'+jobId+'-';if(!hash.startsWith(prefix))return null;
 const suffix=hash.slice(prefix.length);if(!/^\d+$/.test(suffix))return null;
 const index=Number(suffix);return segments.some(s=>s.index===index)?index:null;
}
