import asyncio
import json
import os
import socket
import subprocess
import time
from pathlib import Path
from uuid import uuid4
from .profiles import BASE,root,inventory

def running(settings):
    # A surviving private instance blocks new GPU work until its job reconciles.
    with socket.socket() as s:
        s.settimeout(.1)
        return s.connect_ex(('127.0.0.1',8191))==0

def helper(settings,action,*args):
    value=subprocess.run([str(root(settings)/'python/python.exe'),'-B',str(Path(__file__).with_name('process_probe.py')),action,*map(str,args)],capture_output=True,text=True,timeout=40,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if value.returncode:raise RuntimeError('No se confirmó el estado del proceso Pika: '+value.stderr[-1200:])
    return json.loads(value.stdout)

def atomic(path,value):
    part=path.with_suffix('.part');part.write_text(json.dumps(value,indent=2),encoding='utf-8');part.replace(path)

class Session:
    def __init__(self,service,job):
        self.service=service;self.job_id=job['id'];self.workspace=service.settings.get('workspace_id','owner')
        self.root=root(service.settings);self.record_path=self.root/'runtime/gen-studio-process.json'
        self.process=None;self.log=None;self.record=None

    async def external_idle(self):
        bases=[self.service.settings['comfy_url'],*self.service.settings.get('pika_external_comfy_urls',['http://127.0.0.1:8199','http://127.0.0.1:8190'])]
        for base in dict.fromkeys(bases):
            try:q=await self.service.get(base,'/queue')
            except Exception as error:
                # An absent external application has no active GPU queue.
                import httpx
                if base!=self.service.settings['comfy_url'] and isinstance(error,httpx.ConnectError):continue
                raise
            if q.get('queue_running') or q.get('queue_pending'):raise ValueError('Otro motor está utilizando la GPU. Pika no interrumpirá ese trabajo.')

    async def resources(self):return await asyncio.to_thread(helper,self.service.settings,'resources')

    async def attach(self):
        if not self.record_path.is_file():return False
        record=json.loads(self.record_path.read_text(encoding='utf-8'))
        if record.get('stopped'):return False
        alive=await asyncio.to_thread(helper,self.service.settings,'inspect','--record',self.record_path)
        if not alive['running']:return False
        if record['job_id']!=self.job_id or record['workspace']!=self.workspace:
            raise ValueError('El proceso Pika pertenece a otra toma; espera a su recuperación.')
        self.record=record
        return True

    async def start_or_resume(self,job):
        if await self.attach():
            await self.ready()
            return
        if job.get('prompt_id') or job.get('pika_submission_attempted'):
            raise ValueError('El proceso aceptado ya no está activo. Se conserva la toma sin reenviarla; crea otra versión para repetirla.')
        check=await asyncio.to_thread(inventory,self.service.settings)
        if not check['ok']:raise ValueError(' · '.join(check['errors']))
        if running(self.service.settings):raise ValueError('El puerto privado de Pika pertenece a un proceso sin vinculación verificable.')
        await self.external_idle()
        if (await self.service.get(self.service.settings['ollama_url'],'/api/ps')).get('models'):
            raise ValueError('Qwen todavía ocupa memoria; no se inició Pika.')
        resources=await self.resources()
        if resources['gpu_free_mib']<16*1024 or resources['host_available_bytes']<36*1024**3:
            raise ValueError('Pika necesita al menos 16 GiB de GPU y 36 GiB de RAM disponibles antes de iniciar. Cierra otras generaciones.')
        config=json.loads((self.root/'config/launch.json').read_text(encoding='utf-8'))
        if Path(config['python']).resolve()!=self.root/'python/python.exe' or Path(config['main']).resolve()!=self.root/'ComfyUI/main.py':
            raise ValueError('La configuración aislada de Pika no corresponde a su instalación.')
        args=list(config['arguments'])
        if args[args.index('--listen')+1]!='127.0.0.1' or args[args.index('--port')+1]!='8191':raise ValueError('Pika debe permanecer en su puerto privado local.')
        nonce=uuid4().hex
        user=self.root/'runtime/user'/nonce;user.mkdir(parents=True)
        args[args.index('--user-directory')+1]=str(user)
        env=dict(os.environ);env.update(config['environment']);env.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',PYTHONNOUSERSITE='1')
        if env.get('CUDA_VISIBLE_DEVICES')=='-1':raise ValueError('La GPU está oculta para el proceso actual.')
        folder=self.service.root/'jobs'/self.job_id;folder.mkdir(parents=True,exist_ok=True)
        self.log=(folder/'pika-server.log').open('a',encoding='utf-8')
        self.process=subprocess.Popen([config['python'],'-B',config['main'],*args],cwd=self.root/'ComfyUI',env=env,stdout=self.log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            record=await asyncio.to_thread(helper,self.service.settings,'capture','--pid',self.process.pid,'--nonce',nonce)
            record.update(job_id=self.job_id,workspace=self.workspace,nonce=nonce)
            atomic(self.record_path,record);atomic(folder/'pika-process.json',record);self.record=record
            self.service.store.update(self.job_id,engine_instance_id=nonce,pika_runtime_version='isolated-1')
            await self.ready()
        except BaseException:
            if self.process.poll() is None:
                self.process.terminate();await asyncio.to_thread(self.process.wait,timeout=20)
            raise

    async def ready(self):
        from ..job_cancel import checkpoint
        for _ in range(90):
            checkpoint(self.service,self.job_id)
            if self.process and self.process.poll() is not None:raise ValueError('Pika no pudo arrancar; revisa el diagnóstico de esta toma.')
            try:
                info=await self.service.get(BASE,'/system_stats')
                argv=info['system']['argv']
                if str(self.root/'ComfyUI/main.py') not in argv or not any(self.record['nonce'] in s for s in argv):
                    raise ValueError('El servidor Pika no corresponde al proceso registrado.')
                return
            except (KeyError,ValueError):raise
            except Exception:await asyncio.sleep(1)
        raise ValueError('Pika no respondió durante el arranque.')

    async def stop(self):
        if self.record:
            await asyncio.to_thread(helper,self.service.settings,'stop','--record',self.record_path)
            if self.process:await asyncio.to_thread(self.process.wait,timeout=10)
            atomic(self.record_path,{**self.record,'stopped':True})
        elif self.process and self.process.poll() is None:
            self.process.terminate();await asyncio.to_thread(self.process.wait,timeout=20)
        if self.log:self.log.close()

    async def interrupt(self,prompt_id):
        if not self.record:return
        await self.service.post(BASE,'/queue',json={'delete':[prompt_id]})
        await self.service.post(BASE,'/interrupt',json={'prompt_id':prompt_id})
