"""Tailscale identity selects a private store; request parameters never select it."""
import asyncio
import re
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path

from .jobs import JobService, TERMINAL

current_workspace = ContextVar('gen_studio_workspace', default='owner')


def profiles(settings):
    configured=settings.get('workspaces') or {'owner':{'name':'Mi espacio creativo','logins':settings['allowed_logins']}}
    result={};identities=set()
    for key,profile in configured.items():
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,39}',key):raise ValueError('Identificador de espacio inválido.')
        logins=[str(value).strip().lower() for value in profile.get('logins',[])]
        if not logins or any(not value or value in identities for value in logins) or len(set(logins))!=len(logins):
            raise ValueError('Cada cuenta de acceso debe pertenecer a un único espacio.')
        identities.update(logins)
        interval=profile.get('generation_interval_seconds',0)
        if type(interval) is not int or not 0<=interval<=86400:raise ValueError('Intervalo de generación inválido.')
        result[key]={'id':key,'name':profile.get('name',key),'logins':logins,'generation_interval_seconds':interval}
    if 'owner' not in result:raise ValueError('Falta el espacio del propietario.')
    return result


def identify(configured,login,local=False):
    if local and not login:return 'owner'
    for key,profile in configured.items():
        if login in profile['logins']:return key
    return None


def public_profile(configured,key):
    profile=configured[key]
    return {'id':key,'name':profile['name'],'storage_namespace':'' if key=='owner' else key,'private':True}


class EngineCoordinator:
    """One lease covers generation or an interactive analysis, across all stores."""
    def __init__(self):
        self.owner=None
        self.mode=None
        self.changed=asyncio.Event()
        self.maintenance=False

    def claim(self,service,mode):
        # All callers use the same event loop; no await between check and claim.
        if self.owner is not None or self.maintenance:return False
        self.owner=service;self.mode=mode
        return True

    def release(self,service,mode):
        if self.owner is service and self.mode==mode:
            self.owner=None;self.mode=None;self.changed.set()

    @asynccontextmanager
    async def generation(self,service,job):
        waiting=False
        from .generation_limits import check
        while True:
            if service.store.job(job['id'])['status'] in TERMINAL or not check(service,job):
                yield False
                return
            if self.claim(service,'generation'):break
            if not waiting and service.store.job(job['id'])['status']=='preparing':
                service.store.update(job['id'],status='queued',stage='Esperando turno del motor compartido',progress=None)
            waiting=True
            self.changed.clear()
            await self.changed.wait()
        try:
            current=service.store.job(job['id'])
            if current['status'] in TERMINAL or not check(service,current,reserve=True):
                yield False
            else:
                if current['status']=='queued':service.store.update(job['id'],status='preparing',stage='Preparando tu toma')
                yield True
        finally:
            self.release(service,'generation')


class WorkspaceServices:
    """Existing routes resolve jobs/store/root to the authenticated request context."""
    def __init__(self,client,settings,configured):
        object.__setattr__(self,'services',{})
        object.__setattr__(self,'coordinator',EngineCoordinator())
        base=Path(settings['data_root']).resolve()
        for key in configured:
            root=base if key=='owner' else base/'workspaces'/key
            if key!='owner' and not root.resolve().is_relative_to(base/'workspaces'):
                raise ValueError('La carpeta del espacio no es válida.')
            service=JobService(client,{**settings,'data_root':str(root),'workspace_id':key,'generation_interval_seconds':configured[key].get('generation_interval_seconds',0)})
            service.coordinator=self.coordinator
            self.services[key]=service
        from .sonny_generation_control import SonnyGenerationControl
        controls = {key: SonnyGenerationControl(self, key) for key in configured if key != 'owner'}
        object.__setattr__(self, 'generation_controls', controls)
        object.__setattr__(self,'sonny_generation_control',controls.get('sonny') or SonnyGenerationControl(self))

    @property
    def current(self):
        return self.services[current_workspace.get()]

    def __getattr__(self,name):return getattr(self.current,name)

    def __setattr__(self,name,value):setattr(self.current,name,value)

    async def start(self):
        for key,service in self.services.items():
            token=current_workspace.set(key)
            try:await service.start()
            finally:current_workspace.reset(token)

    async def close(self):
        # Cancel every workspace before awaiting cleanup of any individual one.
        for service in self.services.values():
            if service.worker:service.worker.cancel()
            if service.enhancer_task:service.enhancer_task.cancel()
        await asyncio.gather(*(service.close() for service in self.services.values()))
