import React,{useEffect,useState} from 'react';

async function registerDevice(subscription,action='device'){
  const response=await fetch('/api/segment-alerts/'+action,{
    method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(subscription)
  });
  if(!response.ok)throw Error('Gen Studio no pudo registrar el cambio. Inténtalo de nuevo.');
  return response.json();
}

export default function SegmentAlerts(){
  const supported=window.isSecureContext&&'serviceWorker' in navigator&&'PushManager' in window&&'Notification' in window;
  const [enabled,setEnabled]=useState(false),[busy,setBusy]=useState(true),[error,setError]=useState('');
  useEffect(()=>{
    let live=true;
    async function read(){
      try{
        if(!supported)return;
        const registration=await navigator.serviceWorker.getRegistration('/');
        const subscription=await registration?.pushManager.getSubscription();
        const active=subscription&&(await registerDevice(subscription,'status')).enabled;
        if(live)setEnabled(Boolean(active));
      }catch{if(live)setError('No se pudo consultar el permiso de avisos.');}
      finally{if(live)setBusy(false);}
    }
    read();return ()=>{live=false;};
  },[supported]);
  async function toggle(){
    setBusy(true);setError('');
    try{
      if(!enabled&&await Notification.requestPermission()!=='granted')throw Error('Permite las notificaciones de este sitio en tu navegador para recibir los avisos.');
      await navigator.serviceWorker.register('/segment-alerts-sw.js');
      const registration=await navigator.serviceWorker.ready;
      let subscription=await registration.pushManager.getSubscription();
      if(enabled){
        if(subscription){await registerDevice(subscription,'disable');await subscription.unsubscribe();}
        setEnabled(false);
      }else{
        const response=await fetch('/api/segment-alerts/key');
        if(!response.ok)throw Error('No se pudo activar el servicio de avisos.');
        const {publicKey}=await response.json();
        const key=Uint8Array.from(atob(publicKey.replace(/-/g,'+').replace(/_/g,'/')),c=>c.charCodeAt(0));
        if(subscription&&String(new Uint8Array(subscription.options.applicationServerKey))!==String(key)){
          await subscription.unsubscribe();subscription=null;
        }
        subscription=subscription||await registration.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:key});
        await registerDevice(subscription);setEnabled(true);
      }
    }catch(e){setError(e.name==='NotAllowedError'?'Activa el permiso de notificaciones para Gen Studio en este navegador.':e.message);}
    finally{setBusy(false);}
  }
  return <div><button type="button" className="secondary" disabled={!supported||busy} onClick={toggle}>{enabled?'Desactivar avisos de tramos':'Activar avisos de tramos'}</button>
    {(!supported||error)&&<p role="status">{error||'Abre Gen Studio por HTTPS en un navegador compatible con notificaciones.'}</p>}</div>;
}
