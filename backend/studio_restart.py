"""Owner-only restart of the registered local services; data stays in place."""
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
from uuid import uuid4

from fastapi import HTTPException

from .store import now
from .workflows import ROOT
from .workspaces import current_workspace

FINAL = {'completed', 'failed', 'cancelled'}


def process_started(pid):
    """Return the creation token of a live Windows process, without PID reuse."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x100000 | 0x1000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:return None
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if kernel.WaitForSingleObject(handle, 0) != 258:return None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
            raise ctypes.WinError(ctypes.get_last_error())
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    finally:kernel.CloseHandle(handle)


def recovery_jobs(store):
    with store.connect() as db:
        rows = db.execute("SELECT state FROM jobs WHERE json_extract(state,'$.status') NOT IN ('completed','failed','cancelled')").fetchall()
    return [json.loads(row[0]) for row in rows]


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


class RestartController:
    def __init__(self, app, settings):
        self.app, self.settings = app, settings
        self.path = Path(settings.get('restart_state_file', ROOT / '.runtime' / 'studio-restart.json'))
        self.boot_id = str(uuid4())
        self.lock = asyncio.Lock()
        self.task = None

    def operation(self):
        try:return json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):return None

    def update(self, operation, **changes):
        operation.update(changes, updated_at=now());save(self.path, operation)

    def blocked(self):
        registry = self.app.state.jobs
        for key, service in registry.services.items():
            if any(j['parameters'].get('engine')=='tessera' for j in recovery_jobs(service.store)):
                return 'Tessera tiene trabajos pendientes o en ejecución. Cancélalos desde Upscaling o espera a que terminen antes de reiniciar todos los motores.'
            if key != 'owner' and (service.enhancer_active or any(j['status'] != 'queued' or j.get('prompt_id') for j in recovery_jobs(service.store))):
                return 'Otro espacio está trabajando o tiene un envío por confirmar. Espera a que termine para reiniciar.'
        return ''

    async def status(self):
        operation = self.operation()
        if operation and operation['state'] == 'restarting':
            interrupted = False
            if operation.get('worker_started') is not None:
                try:interrupted = process_started(operation['worker_pid']) != operation['worker_started']
                except OSError:pass  # Lack of process access is not proof of a stopped worker.
            elif not operation.get('worker_pid') and (not self.task or self.task.done()):
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(operation['created_at'])).total_seconds()
                interrupted = operation['boot_id'] != self.boot_id or age > 90
            if interrupted:
                latest = self.operation()
                if latest and latest['id'] == operation['id'] and latest['state'] == 'restarting':
                    self.update(latest, state='failed', stage='El reinicio se interrumpió',
                        error='El proceso de reinicio se detuvo antes de terminar. Puedes volver a intentarlo.')
                operation = self.operation()
        busy = bool(operation and operation['state'] == 'restarting')
        if not busy and self.app.state.jobs.coordinator.maintenance:
            self.app.state.jobs.coordinator.maintenance = False
            self.app.state.jobs.coordinator.changed.set()
            for service in self.app.state.jobs.services.values():
                if self.settings['generation_enabled'] and (service.worker is None or service.worker.done()):
                    await service.start()
        reason = self.blocked()
        if not reason:
            reason = 'Reinicia Gen Studio y el motor de vídeo. Se detendrá tu generación en curso; se conservan archivos y resultados.'
        public = {k:operation.get(k) for k in ('id','state','stage','error')} if operation else None
        return {'can_restart':not busy and not bool(self.blocked()), 'busy':busy,
            'reason':reason,'operation':public,'boot_id':self.boot_id}

    async def begin(self):
        async with self.lock:
            await self.status()
            existing = self.operation()
            if existing and existing['state'] == 'restarting':return {k:existing[k] for k in ('id','state','stage')}
            if reason := self.blocked():raise HTTPException(409,reason)
            self.app.state.jobs.coordinator.maintenance = True
            operation = {'id':str(uuid4()),'state':'restarting','stage':'Preparando el reinicio…',
                'boot_id':self.boot_id,'server_pid':os.getpid(),'created_at':now(),
                'data_root':str(self.settings['data_root']), 'error':None}
            self.update(operation)
            self.task = asyncio.create_task(self.launch(operation))
            return {k:operation[k] for k in ('id','state','stage')}

    async def launch(self, operation):
        try:
            owner = self.app.state.jobs.services['owner']
            # Preserve any acknowledgement in flight before stopping the monitor.
            for _ in range(650):
                if not any(j['status']=='submitting' for j in recovery_jobs(owner.store)):break
                await asyncio.sleep(.1)
            self.update(operation, stage='Deteniendo el seguimiento antes de reiniciar…')
            await asyncio.wait_for(owner.close(), timeout=20)
            if reason := self.blocked():raise ValueError(reason)
            self.update(operation, stage='Reiniciando Gen Studio y su motor…')
            launcher_script = ROOT / 'scripts' / '_restart_launcher.py'
            worker_script = ROOT / 'scripts' / 'restart_studio.py'
            log_path = self.path.parent / 'studio-restart-worker.log'
            pid_file = self.path.parent / f'.worker-pid-{operation["id"]}'
            launcher_flags = (getattr(subprocess,'DETACHED_PROCESS',0)
                | getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0)
                | getattr(subprocess,'CREATE_NO_WINDOW',0)
                | getattr(subprocess,'CREATE_BREAKAWAY_FROM_JOB',0))
            try: pid_file.unlink()
            except FileNotFoundError: pass
            with log_path.open('ab') as log:
                subprocess.Popen([str(ROOT/'.venv/Scripts/python.exe'),'-B',str(launcher_script),
                    str(log_path), str(pid_file), str(ROOT/'.venv/Scripts/python.exe'), str(worker_script), operation['id']],
                    cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    creationflags=launcher_flags, close_fds=True)
            worker_pid=None
            for _ in range(20):
                await asyncio.sleep(0.25)
                if pid_file.exists():
                    try:worker_pid=int(pid_file.read_text().strip());break
                    except (OSError,ValueError):pass
            if worker_pid is None:
                self.update(operation,state='failed',stage='No se pudo iniciar el reinicio',
                    error='El lanzador no reportó el PID del proceso de reinicio.')
                await self.status()
                return
            try:started=process_started(worker_pid)
            except OSError:started=None
            self.update(operation,worker_pid=worker_pid,worker_started=started)
        except Exception as error:
            self.update(operation,state='failed',stage='No se pudo iniciar el reinicio',error=str(error)[:1000])
            await self.status()


def register(app, settings):
    controller = RestartController(app, settings)
    app.state.restart = controller

    @app.get('/api/system/restart-status')
    async def status():
        if current_workspace.get() != 'owner':raise HTTPException(403,'Sólo el propietario puede reiniciar Gen Studio.')
        return await controller.status()

    @app.post('/api/system/restart', status_code=202)
    async def restart():
        if current_workspace.get() != 'owner':raise HTTPException(403,'Sólo el propietario puede reiniciar Gen Studio.')
        return await controller.begin()
