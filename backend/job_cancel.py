"""Cancel supported pipelines while retaining their shared engine lease."""
import asyncio
import os
from pathlib import Path
import subprocess
from urllib.parse import quote

from .store import now

STAGE = 'Cancelando y liberando recursos…'
ACTIVE = {'queued', 'preparing', 'submitting', 'running', 'downloading', 'refining', 'cancelling'}

def sequence_job(job):
    return bool(job.get('video_sequence_version') or job['parameters'].get('ninja_segments_version'))


def requested(service, job_id):
    job=service.store.job(job_id)
    if not job:return False
    parent=service.store.job(job['parameters'].get('sequence_parent_id') or job['parameters'].get('ninja_sequence_owner',''))
    return bool(job.get('cancel_requested') or (parent and parent.get('cancel_requested')))


def checkpoint(service, job_id):
    if requested(service, job_id):
        raise asyncio.CancelledError('Cancelación solicitada por el usuario')


async def thread_checkpoint(service, job_id, function, *args):
    """A publishing thread must finish before cancellation releases the lease."""
    checkpoint(service, job_id)
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        result = await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)
        raise
    checkpoint(service, job_id)
    return result


async def request(service, job_id):
    attached=service.store.job(job_id)
    if attached and attached['parameters'].get('operation') in ('video_upscale','video_upscale_assembly'):
        from .tessera import request_cancel
        return await request_cancel(service,attached)
    if attached and (attached['parameters'].get('sequence_parent_id') or attached['parameters'].get('ninja_sequence_owner')):
        return await request(service,attached['parameters'].get('sequence_parent_id') or attached['parameters']['ninja_sequence_owner'])
    if attached and attached['parameters'].get('hybrid_parent_id'):
        parent=service.store.job(attached['parameters']['hybrid_parent_id'])
        from .motion_hybrid import identity_child
        owner=identity_child(service,parent) if parent and parent['parameters'].get('operation')=='motion_hybrid' else None
        if owner and (attached['id']==owner['id'] or attached['parameters'].get('long_parent_id')==owner['id']):
            return await request(service,parent['id'])
        raise ValueError('La pasada interna no está vinculada a un trabajo válido.')
    async with service.lock:
        job = service.store.job(job_id)
        if not job:
            raise KeyError(job_id)
        if job['status'] == 'cancelled' or job.get('cancel_requested'):
            return job
        if job['status'] not in ACTIVE | {'submission_unknown'}:
            raise ValueError('Este trabajo ya terminó.')
        hybrid=job['parameters'].get('operation')=='motion_hybrid'
        accepted=None
        if sequence_job(job):
            from .video_sequences import active_child
            child=active_child(service,job)
            accepted=child if child and (child.get('prompt_id') or child['parameters'].get('engine')=='viggle_remake' and (child.get('viggle_submission_attempted') or child.get('engine_instance_id') or (child.get('viggle_paint_runtime',{}).get('attempted') and not child.get('viggle_paint_runtime',{}).get('completed'))) or child['parameters'].get('engine')=='pika_ref2va' and (child.get('pika_submission_attempted') or child.get('engine_instance_id') or child.get('pika_swap_guide',{}).get('attempted'))) and child['status'] not in ('completed','failed','cancelled') else None
        if hybrid:
            from .motion_hybrid import accepted_child
            accepted=accepted_child(service,job)
        if job['status'] == 'queued' and not job.get('prompt_id') and not accepted and service.active_job_id != job_id:
            result = service.store.update(job_id, status='cancelled', stage='Cancelado antes del envío',
                cancel_requested=True, cancelled_at=now(), progress=None)
            question_id=job.get('qwen_question_job_id')
            if question_id and question_id!=job_id:
                question=service.store.job(question_id)
                if question and question.get('qwen_waiting') and not question.get('prompt_id'):
                    service.store.update(question_id,status='cancelled',cancel_requested=True,qwen_waiting=False,
                        cancelled_at=now(),progress=None,stage='Cancelado con la toma principal')
        else:
            if not sequence_job(job) and not job.get('qwen_director_version') and job['parameters'].get('engine') not in ('h3_alisson','h3_mdmz','h3_secourses') and job['parameters'].get('operation') not in ('upscale_1080','motion_hybrid','motion_anchor','motion_bunny','motion_layers','image_generation','omni_background'):
                raise ValueError('La cancelación durante la generación está disponible para Alisson y las copias a 1080p.')
            # Do not overwrite submitting: its response must first persist prompt_id.
            changes = dict(cancel_requested=True, cancel_requested_at=now(), stage=STAGE, progress=None)
            if hybrid and service.active_job_id!=job_id:changes['status']='cancelling'
            if job['status'] == 'submission_unknown':
                changes.update(status='cancelling', cancel_submission_unknown=not bool(job.get('prompt_id')))
            result = service.store.update(job_id, **changes)
        if service.active_job_id == job_id:
            service.cancel_wake.set()
        service.wake.set()
        return result


def submission_pending(service,job_id):
    job=service.store.job(job_id)
    if sequence_job(job):
        from .video_sequences import active_child
        child=active_child(service,job)
        if child and child['status']=='submitting':return True
    if job['parameters'].get('operation')=='motion_hybrid':
        from .motion_hybrid import submitting
        return submitting(service,job)
    return job['status']=='submitting'


async def run(service, job):
    """Called INSIDE EngineCoordinator.generation, including all cleanup."""
    job_id = job['id']
    service.active_job_id = job_id
    service.cancel_wake.clear()
    task = waiter = None
    try:
        if not requested(service, job_id):
            task = asyncio.create_task(service._run(job))
            waiter = asyncio.create_task(service.cancel_wake.wait())
            await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
            if requested(service, job_id):
                # An accepted POST must be recorded before aborting its local monitor.
                while not task.done() and submission_pending(service,job_id):
                    await asyncio.wait({task}, timeout=.1)
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            else:
                await task
        if requested(service, job_id):
            await finish(service, job_id)
    finally:
        # Server shutdown is not a user cancellation; preserve accepted state.
        for pending in (waiter, task):
            if pending and not pending.done():
                pending.cancel()
        await asyncio.gather(*(t for t in (waiter, task) if t), return_exceptions=True)
        service.active_job_id = None


def entries(queue):
    return [item for key in ('queue_running', 'queue_pending') for item in queue.get(key, [])
            if isinstance(item, (list, tuple)) and len(item) > 1]


async def restart_stalled_engine(service, job_id, prompt_id):
    """Only the locally registered engine, with this prompt as its sole work."""
    if not service.settings.get('cancel_engine_recovery') or os.name != 'nt':
        return False
    if not service.coordinator or service.coordinator.owner is not service or service.coordinator.mode != 'generation':
        return False
    if service.settings['comfy_url'].rstrip('/') != 'http://127.0.0.1:8189':
        return False
    queue = await service.get(service.settings['comfy_url'], '/queue')
    if len(queue.get('queue_running', [])) != 1 or queue.get('queue_pending') or any(item[1] != prompt_id for item in entries(queue)):
        return False
    current = service.store.job(job_id)
    if current.get('cancel_engine_restart_attempted'):
        return False
    service.store.update(job_id, cancel_engine_restart_attempted=True,
        stage='Recuperando el motor que no respondió a la cancelación…')
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'recover-cancelled-engine.ps1'
    process = await asyncio.create_subprocess_exec('powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', str(script), '-PromptId', prompt_id, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    # The helper may be between stop/start; do not orphan it during server shutdown.
    communication = asyncio.create_task(process.communicate())
    try:
        output, error = await asyncio.shield(communication)
    except asyncio.CancelledError:
        await communication
        raise
    if process.returncode:
        service.event(job_id, 'No se reinició el motor: ' + error.decode(errors='replace')[-1500:])
        return False
    service.event(job_id, 'Motor local recuperado tras una cancelación sin respuesta; no había otros prompts en su cola.')
    service.store.update(job_id, cancel_engine_restarted=True)
    return True


async def finish(service, job_id, _hybrid=True, _sequence=True):
    current = service.store.job(job_id)
    if _sequence and sequence_job(current):
        from .video_sequences import active_child
        child=active_child(service,current)
        if child and child['status'] not in ('completed','failed','cancelled'):
            service.store.update(child['id'],cancel_requested=True)
            await finish(service,child['id'])
            if service.store.job(child['id'])['status']=='submission_unknown':
                service.store.update(job_id,status='submission_unknown',error='Cancelación del tramo sin confirmar');return
        if current.get('prompt_id') or (current.get('cancel_submission_unknown') and not child):
            return await finish(service,job_id,_hybrid=False,_sequence=False)
        service.store.update(job_id,status='cancelled',cancel_requested=True,qwen_waiting=False,sequence_waiting=False,stage='Secuencia cancelada',progress=None,cancelled_at=now())
        return service.store.job(job_id)
    if current['parameters'].get('engine')=='viggle_remake':
        from .viggle_remake.integration import cancel
        await cancel(service,current)
        return service.store.job(job_id)
    if current['parameters'].get('engine')=='pika_ref2va':
        from .pika_ref2va.adapter import cancel
        await cancel(service,current)
        return service.store.job(job_id)
    if _hybrid and current['parameters'].get('operation')=='motion_hybrid':
        from .motion_hybrid import finish_cancel
        await finish_cancel(service,job_id)
        try:await service.release_ollama(job_id)
        except Exception as error:
            service.store.update(job_id,cancel_resources_released=False,cancel_cleanup_warning='No se confirmó la liberación de Qwen: '+str(error)[:500])
        return service.store.job(job_id)
    if current['parameters'].get('operation') == 'upscale_1080':
        # Its local worker has already stopped in process()'s finally block.
        # ComfyUI may contain unrelated work: never cancel or unload it here.
        cleanup_error = current.get('cancel_local_cleanup_error')
        service.store.update(job_id, status='cancelled',
            stage='Escalado cancelado · No se confirmó la detención del proceso auxiliar' if cleanup_error
                else 'Escalado cancelado · Recursos liberados',
            cancelled_at=now(), progress=None, error=None,
            cancel_resources_released=not bool(cleanup_error), cancel_cleanup_warning=cleanup_error)
        return
    ambiguous = current.get('cancel_submission_unknown') or (current['status'] == 'submission_unknown' and not current.get('prompt_id'))
    service.store.update(job_id, status='cancelling', stage=STAGE, progress=None,
                         cancel_submission_unknown=bool(ambiguous))
    base = service.settings['comfy_url']
    started = asyncio.get_running_loop().time()
    dispatched = False
    last_dispatch = 0
    while True:
        try:
            queue = await service.get(base, '/queue')
            current = service.store.job(job_id)
            prompt_id = current.get('prompt_id')
            if not prompt_id and ambiguous:
                # A lost POST response may still be identified by its private client_id.
                matches = [item[1] for item in entries(queue) if len(item) > 3 and
                    isinstance(item[3], dict) and current.get('client_id') and item[3].get('client_id') == current['client_id']]
                if len(matches) == 1:
                    prompt_id = matches[0]
                    service.store.update(job_id, prompt_id=prompt_id, cancel_submission_unknown=False)
                    ambiguous = False
                else:
                    service.store.update(job_id, status='submission_unknown',
                        stage='Cancelación sin confirmar: envío sin identificador',
                        error='No se pudo identificar con seguridad el envío. Se conserva el registro y no se repetirá automáticamente.')
                    return
            present = prompt_id and any(item[1] == prompt_id for item in entries(queue))
            if not present:
                break
            tick = asyncio.get_running_loop().time()
            if not dispatched or tick - last_dispatch >= 5:
                await service.post(base, '/api/jobs/' + quote(prompt_id, safe='') + '/cancel', json={})
                if not dispatched:
                    service.event(job_id, 'Cancelación enviada únicamente al prompt registrado: ' + prompt_id)
                dispatched = True
                last_dispatch = tick
            if tick - started >= 30:
                await restart_stalled_engine(service, job_id, prompt_id)
            service.store.update(job_id, stage='Cancelando: esperando a que el motor libere la toma…')
        except Exception as error:
            service.store.update(job_id, stage='Cancelación pendiente: esperando la conexión del motor',
                cancel_connection_error=str(error)[:500])
        await asyncio.sleep(1)
    # The target is absent. Never unload models being used by another prompt.
    released = False
    if not entries(queue):
        try:
            if await service.idle():
                await service.post(base, '/free', json={'unload_models': True, 'free_memory': True})
                for _ in range(30):
                    if not await service.idle():
                        break
                    stats = await service.get(base, '/system_stats')
                    devices = stats.get('devices', [])
                    if devices and all(d.get('torch_vram_total', float('inf')) < 512 * 1024**2 for d in devices):
                        released = True
                        break
                    await asyncio.sleep(1)
        except Exception as error:
            service.event(job_id, 'Cancelado; liberación de memoria sin confirmar: ' + str(error)[:500])
    cleanup_error = service.store.job(job_id).get('cancel_local_cleanup_error')
    if current['parameters'].get('operation')=='motion_anchor':
        try:await service.release_ollama(job_id)
        except Exception as error:cleanup_error='No se confirmó la liberación de Qwen: '+str(error)[:500]
    service.store.update(job_id, status='cancelled', stage='Trabajo cancelado · No se confirmó la detención de un proceso auxiliar' if cleanup_error else 'Trabajo cancelado · Memoria del motor liberada' if released
        else 'Trabajo cancelado · Memoria compartida pendiente de liberar', cancelled_at=now(), progress=None, error=None,
        cancel_resources_released=released and not bool(cleanup_error), cancel_cleanup_warning=cleanup_error)

