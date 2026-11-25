import {StudioSlot,useStudioWorkspace} from './StudioWorkspace.jsx';
import {matchesSwapStep,useSwapStep} from './ObjectSwapGuide.jsx';
import React,{useId,useRef,useState} from 'react';

// Keep controls mounted: folding a section must not reset a draft or request.
export default function StudioStep({number,title,summary,defaultOpen=false,guideStep,children}) {
  const workspace=useStudioWorkspace(),direction=/escena|indicaciones|escenario/i.test(title),source=/^(vídeo|video|imagen inicial)/i.test(title);
  const [open,setOpen]=useState(defaultOpen||Boolean(workspace?.active)),id=useId(),content=useRef(null);
  const current=useSwapStep(),expanded=Boolean(current)||open;
  const visible=matchesSwapStep(current,guideStep);
  React.useEffect(()=>{if(!visible)content.current?.querySelectorAll('video').forEach(video=>video.pause());},[visible]);
  function toggle(){if(open)content.current?.querySelectorAll('video').forEach(video=>video.pause());setOpen(value=>!value);}
  return <StudioSlot name={direction?'direction':'inline'}><section hidden={!visible} data-studio-section={source?'source':direction?'direction':'settings'} className={'studio-step'+(expanded?' is-open':'')}>
    <h3 className="studio-step-heading"><button type="button" aria-expanded={expanded} aria-controls={id} onClick={toggle}>
      <span className="studio-step-number" aria-hidden="true">{String(number).padStart(2,'0')}</span>
      <span className="studio-step-title"><strong>{title}</strong>{summary&&<small>{summary}</small>}</span>
      <span className="studio-step-toggle" aria-hidden="true">{open?'−':'+'}</span>
    </button></h3>
    <div className="studio-step-content" ref={content} id={id} hidden={!expanded}>{children}</div>
  </section></StudioSlot>;
}
