"""ComfyUI transport for an already planned window; caller owns the shared GPU lease.
No LLM commands, model downloads or modifications to other engines.
"""
import asyncio,json,time
from pathlib import Path
from uuid import UUID
import httpx
from .profiles import BASE
from .state import graph_hash,digest,atomic
from .graphs import relative

class SubmissionUnknown(RuntimeError):pass

class Adapter:
    def __init__(self,client,receipt):
        self.client=client;self.receipt=receipt
    async def request(self,method,path,**kw):
        response=await self.client.request(method,BASE+path,**kw);response.raise_for_status()
        return response.json() if response.content else {}
    async def upload(self,path):
        path=Path(path)
        if path.suffix.lower() not in ('.mp4','.png','.jpg','.jpeg','.webp'):raise ValueError('Formato de entrada no admitido.')
        filename=digest(path)[:20]+path.suffix.lower()
        with path.open('rb') as f:
            item=await self.request('POST','/upload/image',files={'image':(filename,f)},data={'overwrite':'false','subfolder':f'gen-studio-viggle/{self.receipt.workspace}/{self.receipt.job_id}'},timeout=120)
        return relative('/'.join(x for x in (item.get('subfolder',''),item['name']) if x))
    async def reconcile(self):
        state=self.receipt.read();matches=set()
        queue=await self.request('GET','/queue');history=await self.request('GET','/history?max_items=1000')
        rows=[*queue.get('queue_running',[]),*queue.get('queue_pending',[]),*[v.get('prompt',[]) for v in history.values()]]
        for row in rows:
            if not isinstance(row,(list,tuple)) or len(row)<4 or not isinstance(row[3],dict):continue
            extra=row[3]
            if (extra.get('gen_studio_job_id')==state['job_id'] and extra.get('workspace')==state['workspace']
              and extra.get('graph_sha256')==state['graph_sha256'] and graph_hash(row[2])==state['graph_sha256']):matches.add(str(UUID(row[1])))
        if len(matches)!=1:
            self.receipt.update(status='submission_unknown')
            raise SubmissionUnknown('No hay un único envío confirmado. Se conserva la toma; no se enviará otra vez automáticamente.')
        pid=matches.pop();self.receipt.update(prompt_id=pid,status='running');return pid
    async def submit(self,graph):
        state=self.receipt.accept(graph)
        if state['status'] in ('cancelled','failed'):raise ValueError('La ventana terminó; crea otra versión para repetirla.')
        if state.get('attempted'):
            if state.get('prompt_id'):return state['prompt_id']
            return await self.reconcile()
        info=await self.request('GET','/object_info')
        missing={n['class_type'] for n in graph.values()}-info.keys()
        if missing:raise ValueError('Faltan nodos Viggle: '+', '.join(sorted(missing)))
        self.receipt.update(status='submitting',attempted=True)
        try:
            response=await self.request('POST','/prompt',json={'prompt':graph,'client_id':state['job_id'],
              'extra_data':{'gen_studio_job_id':state['job_id'],'workspace':state['workspace'],'graph_sha256':state['graph_sha256']}},timeout=60)
            pid=str(UUID(response['prompt_id']))
        except (httpx.TransportError,httpx.HTTPStatusError,KeyError,ValueError):return await self.reconcile()
        self.receipt.update(status='running',prompt_id=pid);return pid
    async def cancel(self):
        state=self.receipt.read()
        if not state or state['status'] in ('completed','cancelled'):return
        if state.get('attempted'):
            pid=state.get('prompt_id') or await self.reconcile()
            await self.request('POST','/api/jobs/'+pid+'/cancel',json={})
            for _ in range(60):
                q=await self.request('GET','/queue')
                if not any(row[1]==pid for row in q.get('queue_running',[])+q.get('queue_pending',[])):break
                await asyncio.sleep(.5)
            else:raise SubmissionUnknown('No se confirmó la cancelación del envío de esta toma.')
        self.receipt.update(status='cancelled')
    async def wait(self,prompt_id,guard,progress,cancel_event=None,timeout=2700,poll=1):
        state=self.receipt.read()
        if not state or state.get('prompt_id')!=prompt_id:raise ValueError('El envío no pertenece a esta toma.')
        start=time.monotonic()
        while True:
            if cancel_event and cancel_event.is_set():await self.cancel();raise asyncio.CancelledError()
            await guard()
            history=await self.request('GET','/history/'+prompt_id)
            if prompt_id in history:
                entry=history[prompt_id];atomic(self.receipt.folder/'viggle-history.json',entry)
                if entry.get('status',{}).get('status_str')!='success':
                    self.receipt.update(status='failed',error=str(entry.get('status'))[:2000]);raise ValueError('Viggle no completó la ventana; consulta el registro de la toma.')
                self.receipt.update(status='rendered');return entry
            if time.monotonic()-start>timeout:
                await self.cancel();raise TimeoutError('Viggle superó el tiempo máximo de la ventana.')
            await progress(time.monotonic()-start)
            await asyncio.sleep(poll)
    async def collect(self,entry,*,frames,width,height):
        from .media import validate_video
        state=self.receipt.read()
        if state['status'] not in ('rendered','completed'):raise ValueError('La ventana aún no está lista.')
        outputs=[i for v in entry.get('outputs',{}).get('20',{}).values() if isinstance(v,list) for i in v if isinstance(i,dict) and str(i.get('filename','')).endswith('.mp4')]
        if len(outputs)!=1:raise ValueError('No hay una única salida de vídeo Viggle.')
        item=dict(outputs[0])
        # ComfyUI returns Windows separators in output descriptors. Normalize
        # only server-returned paths; still reject drive paths and traversal.
        for field in ('filename','subfolder'):
            if field in item:item[field]=item[field].replace('\\','/')
        if item.get('type')!='output':raise ValueError('La salida no pertenece al directorio de resultados.')
        relative(item['filename']);relative(item.get('subfolder') or 'output')
        target=self.receipt.folder/'viggle-result.mp4'
        if state['status']=='completed':
            if not target.is_file() or digest(target)!=state['result_sha256']:raise ValueError('Cambió el resultado aprobado.')
            return state
        partial=target.with_suffix('.part')
        async with self.client.stream('GET',BASE+'/view',params={k:item[k] for k in ('filename','subfolder','type') if k in item},timeout=120) as response:
            response.raise_for_status();size=0
            with partial.open('wb') as f:
                async for chunk in response.aiter_bytes():
                    size+=len(chunk)
                    if size>256*1024**2:raise ValueError('La ventana supera el límite de descarga.')
                    f.write(chunk)
        info=await asyncio.to_thread(validate_video,partial,frames,width,height)
        sha=await asyncio.to_thread(digest,partial)
        if target.exists() and digest(target)!=sha:raise ValueError('Ya existe otro resultado para esta toma.')
        partial.replace(target)
        return self.receipt.update(status='completed',result_sha256=sha,media=info,result=str(target))
