import React,{useEffect,useRef,useState} from 'react';
import {cancellationControl} from './jobCancellation.js';
export default function JobCancelButton({job,onCancel}){
  const [requesting,setRequesting]=useState(false);
  const inFlight=useRef(false),live=useRef(true);
  useEffect(()=>{live.current=true;return()=>{live.current=false;};},[]);
  const control=cancellationControl(job,requesting);
  async function cancel(){
    if(control.disabled||inFlight.current)return;
    inFlight.current=true;setRequesting(true);
    try{await onCancel(job.id);}finally{inFlight.current=false;if(live.current)setRequesting(false);}
  }
  if(!onCancel||!control.visible)return null;
  return <button type="button" className="secondary" disabled={control.disabled} aria-busy={requesting||undefined} onClick={cancel}>{control.label}</button>;
}
