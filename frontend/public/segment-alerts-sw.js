self.addEventListener('push',event=>{
  const data=event.data?.json();
  if(!data||!/^\w{8}-[\da-f-]{27}$/i.test(data.job)||!Number.isInteger(data.number))return;
  event.waitUntil(self.registration.showNotification('Tramo '+data.number+' generado',{
    body:'Toca para revisar el vídeo en Gen Studio.', tag:'segment-'+data.job,
    data:{job:data.job}
  }));
});
self.addEventListener('notificationclick',event=>{
  event.notification.close();
  const url=new URL('/',self.location.origin);
  url.searchParams.set('take',event.notification.data.job);
  event.waitUntil((async()=>{
    const windows=await clients.matchAll({type:'window',includeUncontrolled:true});
    const existing=windows.find(w=>w.url===url.href);
    if(existing)return existing.focus();
    return clients.openWindow(url.href);
  })());
});
