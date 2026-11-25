import React from 'react';

export default function WorkspaceStage({id,number,title,description,summary,open,onToggle,children}){
 const panel='pm-stage-panel-'+id,heading='pm-stage-heading-'+id;
 return <section className={'pm-stage'+(open?' is-open':'')}>
  <h3 id={heading} className="pm-stage-heading"><button type="button" className="pm-stage-toggle" aria-expanded={open} aria-controls={panel} onClick={onToggle}>
   <span className="pm-stage-number" aria-hidden="true">{number}</span>
   <span className="pm-stage-label"><span className="pm-stage-name">{title}</span><span className="pm-stage-description">{open?description:summary||description}</span></span>
   <span className="pm-stage-chevron" aria-hidden="true">{open?'−':'+'}</span>
  </button></h3>
  {/* Keep controls mounted: collapsing a stage must not discard unsaved fields,
      pending request keys, player state or background job polling. */}
  <div id={panel} className="pm-stage-panel" role="region" aria-labelledby={heading} hidden={!open}>{children}</div>
 </section>;
}
