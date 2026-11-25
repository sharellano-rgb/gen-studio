"""Shared queue integration. The caller holds Gen Studio's workspace GPU lease."""
import asyncio,json,shutil,time
from pathlib import Path
import httpx
from .adapter import Adapter,SubmissionUnknown
from .runtime import Session,running
from .state import Receipt,digest,atomic,graph_hash
from .profiles import VERSION,SIZES
from .graphs import graph
from .director import native_prompt
from . import media,painted,paint_runtime
from ..job_cancel import checkpoint,thread_checkpoint,requested

async def release_idle_engines(service,job):
    session=Session(service,job)
    await session.external_idle()
    if running(service.settings):raise ValueError('Viggle tiene un proceso pendiente; recupera su toma antes de cargar Qwen.')
    bases=[service.settings['comfy_url'],*service.settings.get('viggle_external_comfy_urls',['http://127.0.0.1:8199','http://127.0.0.1:8190','http://127.0.0.1:8191','http://127.0.0.1:8197'])]
    for base in dict.fromkeys(bases):
        try:
            q=await service.get(base,'/queue')
            if q.get('queue_running') or q.get('queue_pending'):raise ValueError('Llegó otro trabajo al motor; no se liberarán sus modelos.')
            await service.post(base,'/free',json={'unload_models':True,'free_memory':True})
        except httpx.ConnectError:
            if base==service.settings['comfy_url']:raise

def local_pin(p):
    if p.get('reference_mode')!='painted_frame':return ()
    pin=p['pin_frame']-p.get('sequence_start_frame',0)
    return (pin,) if 0<=pin<p.get('sequence_input_keep',p['frames']) else ()

def inputs(service,job,folder):
    from ..qwen_director import asset
    p=job['parameters']
    if p.get('viggle_version')!=VERSION or p.get('viggle_native_mode')!='swap':raise ValueError('La versión o modo Viggle no está validado.')
    from .characters import references
    source=asset(service,p['video_id'],'video');refs=references(service,p)
    if digest(source)!=p['source_sha256']:raise ValueError('Cambió una entrada de esta toma.')
    if p.get(painted.FIELD) and not painted.enabled(p):raise ValueError('Esta revisión experimental de fotogramas ya no se ejecuta; crea una toma nueva.')
    w,h=painted.canvas(service.store.asset(p['video_id'])) if painted.enabled(p) else SIZES[p['short_edge']][p['format']]
    if p['resolution']!=f'{w}x{h}':raise ValueError('La resolución no coincide con el perfil aceptado.')
    target=folder/'viggle-source.mp4';receipt=folder/'viggle-source.json'
    count=p.get('sequence_input_keep',p['frames'])
    binding={'sha256':p['source_sha256'],'frames':p['frames'],'real_frames':count,'width':w,'height':h}
    if painted.enabled(p):binding.update(painted.binding(p),full_frame=True)
    if receipt.exists():
        old=json.loads(receipt.read_text(encoding='utf-8'))
        if old['binding']!=binding or digest(target)!=old['sha256']:raise ValueError('Cambió la ventana preparada.')
    else:
        # Any interrupted partial file is replaced only inside this job's private folder.
        partial=folder/'viggle-source-preparing.mp4'
        if partial.exists():partial.unlink()
        result=media.prepare_window(source,{'source':[0,count],'render_frames':p['frames'],'padding_frames':p['frames']-count},partial,w,h,p['source_sha256'],**({'full_frame':True} if painted.enabled(p) else {}))
        partial.replace(target);atomic(receipt,{'binding':binding,'sha256':result['sha256']})
    prefix_path=None
    if p.get('sequence_context'):
        from ..video_sequences import load,child_id
        from .planner import plan
        parent=service.store.job(p['sequence_parent_id']);previous=service.store.job(p.get('sequence_previous_id',''))
        if not parent or not previous or previous['status']!='completed':raise ValueError('Falta el tramo anterior aprobado.')
        if painted.enabled(p) and (not previous.get('viggle_review_passed') or not painted.enabled(previous['parameters'])):raise ValueError('El tramo anterior no tiene revisión visual aprobada; no se propagará.')
        record=load(service,parent);windows=plan(record['frames'],record['cuts'])['windows'];index=p['sequence_index']
        if not 0<index<len(windows):raise ValueError('Índice de continuidad inválido.')
        current=windows[index];prior=windows[index-1];pp=previous['parameters']
        if (job['id']!=child_id(parent,index) or previous['id']!=child_id(parent,index-1)
            or pp.get('sequence_parent_id')!=parent['id'] or pp.get('sequence_shot')!=p['sequence_shot']
            or p['sequence_context']!=17 or p['sequence_start_frame']!=current['source'][0]
            or p['sequence_input_keep']!=current['source'][1]-current['source'][0]
            or p['sequence_keep']!=current['keep'] or p['frames']!=current['render_frames']):raise ValueError('La continuidad no pertenece a este plano y tramo.')
        original=service.root/'results'/(previous['id']+'.mp4');sha=previous.get('viggle_result_sha256')
        if not sha or digest(original)!=sha:raise ValueError('Cambió el resultado anterior de Viggle.')
        prefix_path=folder/'viggle-prefix.mp4';receipt=folder/'viggle-prefix.json'
        binding={'previous_id':previous['id'],'sha256':sha,'window':current,'prior':prior}
        if receipt.exists():
            old=json.loads(receipt.read_text(encoding='utf-8'))
            if old['binding']!=binding or digest(prefix_path)!=old['sha256']:raise ValueError('Cambió el contexto de continuidad.')
        else:
            partial=folder/'viggle-prefix-preparing.mp4'
            if partial.exists():partial.unlink()
            result=media.prefix(original,current,prior,partial,w,h,sha)
            partial.replace(prefix_path);atomic(receipt,{**result,'binding':binding,'path':str(prefix_path)})
    return target,refs,prefix_path

async def publish(service,job,receipt):
    checkpoint(service,job['id'])
    state=receipt.read();source=receipt.folder/'viggle-result.mp4'
    if state['status']!='completed' or digest(source)!=state['result_sha256']:raise ValueError('Falta el resultado verificado de Viggle.')
    target=service.root/'results'/(job['id']+'.mp4');target.parent.mkdir(exist_ok=True)
    if target.exists():
        if digest(target)!=state['result_sha256']:raise ValueError('El resultado publicado cambió.')
    else:await thread_checkpoint(service,job['id'],shutil.copy2,source,target)
    if painted.enabled(job['parameters']):
        service.store.update(job['id'],result_available=True,viggle_result_sha256=state['result_sha256'])
        await painted.validate_result(service,job,receipt.folder/'viggle-source.mp4',target)
    service.store.update(job['id'],status='completed',result_available=True,viggle_result_sha256=state['result_sha256'],
        stage='Ventana Viggle lista',scene_resolution=job['parameters']['resolution'],progress=None,error=None)

async def process(service,job):
    from ..qwen_director import before_job,before_phase
    ident=job['id'];p=job['parameters'];session=Session(service,job)
    receipt=Receipt(service.root,service.settings.get('workspace_id','owner'),ident);adapter=Adapter(service.client,receipt)
    preserve=False;peak=0;sampled=0
    async def guard():
        nonlocal peak,sampled
        checkpoint(service,ident)
        if time.monotonic()-sampled<3:return
        await session.external_idle()
        if (await service.get(service.settings['ollama_url'],'/api/ps')).get('models'):raise ValueError('Otro modelo ocupa la GPU durante Viggle; se detendrá únicamente esta toma.')
        resources=await session.resources();sampled=time.monotonic();peak=max(peak,resources['gpu_used_mib'])
        service.store.update(ident,viggle_peak_vram_mib=peak)
        if resources['gpu_free_mib']<512 or resources['host_available_bytes']<4*1024**3:raise ValueError('Memoria disponible insuficiente; se detiene Viggle antes de continuar.')
    async def progress(elapsed):
        service.store.update(ident,status='running',stage='Generando ventana con Viggle Remake',viggle_elapsed_seconds=round(elapsed,1),_engine_contact='running')
    try:
        state=receipt.read()
        if state and state['status']=='completed':
            if await session.attach():await asyncio.shield(session.stop())
            await publish(service,job,receipt);return
        if state and state['status']=='rendered':
            await session.start_for_results(job,receipt)
            entry=json.loads((receipt.folder/'viggle-history.json').read_text(encoding='utf-8'))
        else:
            if not state:
                if await session.attach():await session.stop();session=Session(service,job)
                if not await before_job(service,job):return
                await service.release_ollama(ident)
                checkpoint(service,ident)
                source,reference,prefix=await thread_checkpoint(service,ident,inputs,service,job,receipt.folder)
                pins=local_pin(p)
                if painted.enabled(p):
                    anchor,pin=await painted.ensure(service,job,source)
                    reference=[anchor];pins=(pin,)
                await release_idle_engines(service,job)
                service.store.update(ident,stage='Iniciando el motor privado de Viggle',progress=None)
                await session.start_or_resume(service.store.job(ident))
                source_name=await adapter.upload(source);ref_names=[await adapter.upload(path) for path in reference]
                prefix_name=await adapter.upload(prefix) if prefix else None
                native=graph(mode='swap',source=source_name,references=ref_names,prompt=native_prompt(p),frames=p['frames'],short_edge=p['short_edge'],
                    seed=p['seed'],pins=pins,prefix=prefix_name,output_prefix='gen-studio-viggle/'+ident)
                native=await before_phase(service,job,'viggle-remake',native)
                if native is None:return
                if len(native['11']['inputs']['prompt'])>12000:raise ValueError('Las indicaciones Qwen superan el límite de Viggle.')
                state=receipt.accept(native)
            else:
                # Never generate a fresh graph after accepting an immutable receipt.
                native=json.loads(receipt.workflow.read_text(encoding='utf-8'))
                try:await session.start_or_resume({**service.store.job(ident),'viggle_submission_attempted':bool(state.get('attempted'))})
                except ValueError as error:
                    if state.get('attempted'):raise SubmissionUnknown(str(error)) from error
                    raise
            checkpoint(service,ident)
            service.store.update(ident,status='submitting',client_id=ident,viggle_submission_attempted=True,viggle_graph_sha256=state['graph_sha256'],stage='Enviando ventana a Viggle')
            pid=await adapter.submit(native)
            service.store.update(ident,prompt_id=pid,status='running')
            entry=await adapter.wait(pid,guard,progress)
        w,h=map(int,p['resolution'].split('x'))
        service.store.update(ident,status='downloading',stage='Validando vídeo de Viggle')
        await adapter.collect(entry,frames=p['frames'],width=w,height=h)
        # Free the private engine before making the completed window eligible for reuse.
        await asyncio.shield(session.stop())
        await publish(service,job,receipt)
    except SubmissionUnknown as error:
        service.store.update(ident,status='submission_unknown',stage='Envío Viggle sin confirmar',error=str(error),progress=None)
    except Exception:
        if paint_runtime.pending(service.store.job(ident)):
            try:await asyncio.shield(paint_runtime.cancel(service,service.store.job(ident)))
            except Exception as error:
                service.store.update(ident,status='submission_unknown',error='No se confirmó la liberación del editor: '+str(error)[:1000])
                raise SubmissionUnknown(str(error)) from error
        raise
    except asyncio.CancelledError:
        # Server shutdown is recoverable; explicit user cancellation stops only our process.
        preserve=not requested(service,ident)
        raise
    finally:
        if preserve:
            if session.log:session.log.close()
        else:
            try:await asyncio.shield(session.stop())
            except Exception as error:
                service.store.update(ident,status='submission_unknown',error='No se confirmó la liberación de Viggle: '+str(error)[:1000],viggle_cleanup_pending=True)
                raise

async def cancel(service,job):
    session=Session(service,job);ident=job['id']
    try:
        await paint_runtime.cancel(service,job)
        attached=await session.attach()
        if not attached and running(service.settings):raise ValueError('El proceso activo no está vinculado a esta toma.')
        # Stopping this verified, job-owned process also handles an unknown POST id.
        await session.stop()
        receipt=Receipt(service.root,service.settings.get('workspace_id','owner'),ident)
        state=receipt.read()
        if state and state['status']!='completed':receipt.update(status='cancelled')
        await service.release_ollama(ident)
        service.store.update(ident,status='cancelled',cancel_requested=True,cancel_resources_released=True,qwen_waiting=False,
            stage='Viggle cancelado · Recursos liberados',progress=None,error=None)
    except Exception as error:
        service.store.update(ident,status='submission_unknown',cancel_resources_released=False,error='No se confirmó la cancelación de Viggle: '+str(error)[:1000])
