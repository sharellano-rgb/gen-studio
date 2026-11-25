"""Speech-only H3 adapter on the existing analysis lease. No native workflow edits."""
import asyncio
import copy
import json
import time
from pathlib import Path
from uuid import uuid4
from fastapi import HTTPException
from .repository import Repository
from .analysis_media import sha256
from .voice_runtime import local_input,atomic_json
from .montage_runtime import command,inspect,CleanupFailed
from .speech import fingerprint,voice_path,result_path
from . import speech_director
from ..workflows import check_graph

class Cancelled(Exception):pass
class Uncertain(Exception):pass


async def start(service,record,run):
    from .render import RenderStore,PlanRequest,checked_plan,public
    store=RenderStore(service.root);p=record['project_id'];ident=record['id']
    async with service.lock:
        record=store.read(p,ident);task=getattr(service,'parody_speech_tasks',{}).get(ident)
        if task and not task.done():return public(service,record)
        if record['status'] in ('completed','review_pending'):return public(service,record)
        if record['status'] in ('cancelled','direction_review','failed','cleanup_failed'):
            raise HTTPException(409,'Conserva este resultado y prepara una nueva versión; no se regenera el mismo plano.')
        from .ref2va import is_native
        native=is_native(record['plan'])
        flag='parody_ref2va_enabled' if native else 'parody_speech_enabled'
        if service.settings.get(flag) is not True:raise HTTPException(503,'El motor de este plan está desactivado.')
        # Once submitted, recover the accepted graph regardless of later project edits.
        if not record.get('submission_intent'):
            project=run(lambda:Repository(service.root).read(p))
            rebuilt=await asyncio.to_thread(lambda:run(lambda:checked_plan(service,project,PlanRequest.model_validate(record['plan']['request']),record['plan']['speech'])))
            if fingerprint(rebuilt)!=record['plan_hash']:raise HTTPException(409,'Cambió la voz o una entrada; prepara otra versión.')
        if service.enhancer_active and getattr(service,'parody_speech_owner',None)!=ident:
            raise HTTPException(409,'Otro trabajo usa el turno; espera para ejecutar este mismo plan.')
        coordinator=getattr(service,'coordinator',None)
        retained=(getattr(service,'parody_speech_owner',None)==ident and service.analysis_lease and
                  (coordinator is None or (coordinator.owner is service and coordinator.mode=='analysis')))
        if service.store.unfinished_jobs() or (not retained and not service.reserve_analysis()):raise HTTPException(409,'La cola existente está ocupada.')
        service.enhancer_active=True;service.parody_speech_owner=ident
        record=store.update(p,ident,status='running',stage='Preparando H3 guiado por voz',error=None,resources_released=False)
        runner=execute
        if native:
            from .ref2va_runtime import execute as runner
        task=asyncio.create_task(runner(service,record));service.enhancer_task=task
        if not hasattr(service,'parody_speech_tasks'):service.parody_speech_tasks={}
        service.parody_speech_tasks[ident]=task
        return public(service,record)


async def upload(service,path,ident):
    with path.open('rb') as stream:
        response=await service.client.post(service.settings['comfy_url']+'/upload/image',data=dict(type='input',subfolder='gen_studio/parody/'+ident,overwrite='false'),files={'image':(path.name,stream,'application/octet-stream')},timeout=90)
    response.raise_for_status();value=response.json()
    return '/'.join(x for x in (value.get('subfolder'),value['name']) if x)


async def prepare(service,record,folder,stopped,stage):
    plan=record['plan'];files=folder/'prepared';files.mkdir(exist_ok=True)
    manifest=files/'manifest.json'
    if manifest.exists():
        meta=json.loads(manifest.read_text());assert meta['plan_hash']==record['plan_hash']
        for name,digest in meta['files'].items():
            path=(files/name).resolve()
            if not path.is_relative_to(files.resolve()) or sha256(path)!=digest:raise ValueError('Cambió la preparación aceptada.')
        return files
    # New attempt folders preserve incomplete files; only a complete manifest is reused.
    work=files/uuid4().hex;work.mkdir();stopped()
    source=local_input(service,plan['source']);reference=local_input(service,plan['references'][0])
    voice=voice_path(service,plan['voice']['id'],plan['voice']['sha256'])
    frames=plan['timing']['frames'];internal=plan['speech']['internal_frames']
    await command(['ffmpeg','-v','error','-nostdin','-n','-ss',plan['timing']['start_frame']/24,'-i',source,'-t',frames/24,
        '-an','-vf','fps=24','-frames:v',frames,'-c:v','libx264','-crf','17','-pix_fmt','yuv420p',work/'source-visible.mp4'],stopped)
    await command(['ffmpeg','-v','error','-nostdin','-n','-i',work/'source-visible.mp4','-vf',f'tpad=stop_mode=clone:stop={internal-frames}',
        '-frames:v',internal,'-an','-c:v','libx264','-crf','17',work/'source-guide.mp4'],stopped)
    offset=plan['voice']['offset_samples']
    for label,count,rate,channels in [('voice-guide.wav',internal,32000,2),('voice-visible.wav',frames,48000,2)]:
        await command(['ffmpeg','-v','error','-nostdin','-n','-i',voice,'-af',f'aresample=48000,adelay={offset}S:all=1,apad,atrim=end_sample={count*2000}',
            '-ar',rate,'-ac',channels,'-c:a','pcm_s16le',work/label],stopped)
    # Re-encode the single reference to bounded observation size without altering source.
    await command(['ffmpeg','-v','error','-nostdin','-n','-i',reference,'-vf',"scale='min(768,iw)':-2",'-frames:v',1,work/'reference.png'],stopped)
    import shutil
    shutil.copy2(reference,work/('reference-original'+reference.suffix.lower()))
    for i,t in enumerate([0,frames/48,(frames-1)/24]):
        await command(['ffmpeg','-v','error','-nostdin','-n','-ss',t,'-i',work/'source-visible.mp4','-vf',"scale='min(768,iw)':-2",'-frames:v',1,work/f'observation-{i}.jpg'],stopped)
    local_input(service,plan['source']);local_input(service,plan['references'][0]);voice_path(service,plan['voice']['id'],plan['voice']['sha256'])
    stage('Qwen analiza el plano completo y propone la actuación')
    direction=await speech_director.propose(service,plan,work,[*(work/f'observation-{i}.jpg' for i in range(3)),work/'reference.png'],stopped)
    atomic_json(work/'direction.json',direction)
    from .render import RenderStore
    RenderStore(service.root).update(record['project_id'],record['id'],direction=direction)
    if direction['needs_review']:return None
    text=speech_director.prompt(plan,direction);(work/'prompt.txt').write_text(text,encoding='utf-8')
    stopped();stage('Validando referencias y grafo de voz H3')
    graph=copy.deepcopy(plan['speech']['recipe'])
    if fingerprint(graph)!=plan['speech']['recipe_sha256']:raise ValueError('Cambió la receta aceptada.')
    names={key:await upload(service,path,record['id']) for key,path in [('video',work/'source-guide.mp4'),('voice',work/'voice-guide.wav'),('picture',work/('reference-original'+reference.suffix.lower()))]}
    graph['parody_voice']=dict(class_type='LoadAudio',inputs=dict(audio=names['voice']))
    for node in graph.values():
        kind=node['class_type'];v=node['inputs']
        if kind=='LoadVideo':v['file']=names['video']
        elif kind=='LoadImage':v['image']=names['picture']
        elif kind=='MiniMaxH3ReferenceToVideo':
            if any(k.startswith(('ref_audios.','ref_video_audios.')) for k in v):raise ValueError('La receta conserva otra guía de audio.')
            v.update(prompt=text);v['ref_audios.ref_audio_0']=['parody_voice',0]
        elif kind=='CreateVideo':v['audio']=['parody_voice',0]
        elif kind=='SaveVideo':v['filename_prefix']='gen_studio/parody/'+record['id']+'/speech'
    check=await check_graph(service.client,service.settings['comfy_url'],graph);atomic_json(work/'preflight.json',check)
    if not check['ok']:raise ValueError('El grafo de H3 no pasó la validación de nodos y enlaces.')
    atomic_json(work/'workflow.json',graph)
    entries={str(p.relative_to(files)):sha256(p) for p in work.iterdir() if p.is_file()}
    atomic_json(manifest,dict(plan_hash=record['plan_hash'],work=work.name,files=entries))
    return files


def matches(item,record):
    try:
        extra=item[3]
        return extra.get('parody_plan_id')==record['id'] and extra.get('parody_plan_hash')==record['plan_hash'] and extra.get('client_id')==record['submission_intent']['client_id']
    except (IndexError,TypeError,AttributeError,KeyError):return False


async def reconcile(service,record):
    queue=await service.get(service.settings['comfy_url'],'/queue');history=await service.get(service.settings['comfy_url'],'/history?max_items=200')
    found={item[1] for item in queue.get('queue_running',[])+queue.get('queue_pending',[]) if matches(item,record)}
    found.update(ident for ident,value in history.items() if matches(value.get('prompt',[]),record))
    if len(found)!=1:raise Uncertain('No se pudo confirmar un único envío anterior. Se conserva la intención; no se vuelve a generar automáticamente.')
    return next(iter(found))


async def generate(service,record,work,stopped,save):
    graph=json.loads((work/'workflow.json').read_text());url=service.settings['comfy_url']
    ident=record.get('prompt_id')
    if not ident and record.get('submission_intent'):ident=await reconcile(service,record);save(prompt_id=ident)
    if not ident:
        stopped()
        if not await service.idle() or (await service.get(service.settings['ollama_url'],'/api/ps')).get('models'):
            raise ValueError('Otro motor está ocupado; no se envía esta prueba.')
        intent=dict(client_id=str(uuid4()),workflow_sha256=sha256(work/'workflow.json'))
        save(submission_intent=intent,stage='Enviando una única generación H3')
        try:
            response=await service.client.post(url+'/prompt',json=dict(prompt=graph,client_id=intent['client_id'],extra_data=dict(parody_plan_id=record['id'],parody_plan_hash=record['plan_hash'])),timeout=90)
            atomic_json(work/'submission-response.json',dict(status=response.status_code,body=response.json()))
            response.raise_for_status();data=response.json()
            if data.get('node_errors') or not data.get('prompt_id'):raise ValueError('Comfy no confirmó la admisión.')
            ident=data['prompt_id'];save(prompt_id=ident)
        except BaseException as error:raise Uncertain('Envío incierto: recuperar por su identificador antes de cualquier reintento.') from error
    else:
        if sha256(work/'workflow.json')!=record['submission_intent']['workflow_sha256']:raise ValueError('Cambió el grafo enviado.')
    started=time.monotonic();save(stage='H3 genera el plano con la voz como guía')
    while True:
        result=(await service.get(url,'/history/'+ident)).get(ident)
        if result:
            atomic_json(work/'history.json',result)
            save(comfy_finished=True)
            if result['status']['status_str']!='success':raise ValueError('H3 no completó el plano. No se reduce resolución ni se regenera automáticamente.')
            stopped();break
        # Cancellation waits for THIS Comfy job. Never send a global interrupt.
        if time.monotonic()-started>1800:raise Uncertain('H3 sigue sin resultado confirmado; recupera el mismo envío.')
        await asyncio.sleep(2)
    save(stage='Recuperando y verificando vídeo')
    save_nodes={key for key,n in graph.items() if n['class_type']=='SaveVideo'}
    files=[v for key,out in result['outputs'].items() if key in save_nodes for values in out.values() if isinstance(values,list) for v in values if isinstance(v,dict) and v.get('filename','').endswith('.mp4')]
    if len(files)!=1:raise ValueError('Salida de H3 ambigua.')
    response=await service.client.get(url+'/view',params={k:files[0][k] for k in ('filename','subfolder','type')},timeout=180);response.raise_for_status()
    (work/'raw.mp4').write_bytes(response.content)
    return work/'raw.mp4'


async def execute(service,record):
    from .render import RenderStore
    store=RenderStore(service.root);p=record['project_id'];ident=record['id'];folder=store.directory(ident);folder.mkdir(parents=True,exist_ok=True)
    start_time=time.monotonic();released=True
    def save(**patch):return store.update(p,ident,**patch)
    def stopped():
        if store.read(p,ident)['cancel_requested']:raise Cancelled()
    try:
        prepared=await prepare(service,record,folder,stopped,lambda text:save(stage=text))
        if prepared is None:save(status='direction_review',stage='Qwen pide aclarar la asignación; prepara otra versión con indicaciones');return
        work=prepared/json.loads((prepared/'manifest.json').read_text())['work']
        record=store.read(p,ident)
        raw=await generate(service,record,work,stopped,save);stopped()
        frames=record['plan']['timing']['frames'];width,height=map(int,record['plan']['resolution'].split('x'))
        probe=json.loads(await command(['ffprobe','-v','error','-count_frames','-show_streams','-of','json',raw],stopped))
        video=next(s for s in probe['streams'] if s['codec_type']=='video')
        if int(video['nb_read_frames'])<frames:raise ValueError('H3 produjo menos fotogramas que los solicitados.')
        output=work/'final.mp4'
        if output.exists():output=work/('final-'+uuid4().hex+'.mp4')
        await command(['ffmpeg','-v','error','-nostdin','-n','-i',raw,'-i',work/'voice-visible.wav','-map','0:v:0','-map','1:a:0',
            '-vf',f'trim=end_frame={frames},setpts=PTS-STARTPTS','-frames:v',frames,'-t',frames/24,'-c:v','libx264','-crf','17','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-movflags','+faststart',
            '-metadata','comment=GEN STUDIO - PARODIA SINTETICA - REVISION PENDIENTE',output],stopped)
        meta=await inspect(output,frames,width,height,stopped);meta['file']=output.relative_to(folder).as_posix();stopped()
        save(status='review_pending',stage='Revisa naturalidad y sincronización antes de aprobar el plano',artifact=meta,error=None,
            received_frames=int(video['nb_read_frames']),review_required=True)
    except Cancelled:save(status='cancelled',stage='Plano cancelado; evidencia conservada')
    except asyncio.CancelledError:
        released=False;save(status='interrupted',stage='Recupera el envío aceptado; no se regenera automáticamente');raise
    except Uncertain as error:
        released=False;save(status='submission_unknown',stage='Reconciliar el mismo envío de Comfy',error=str(error))
    except CleanupFailed as error:
        released=False;save(status='cleanup_failed',stage='No se confirmó limpieza; turno retenido',error=str(error))
    except Exception as error:
        latest=store.read(p,ident)
        if latest.get('submission_intent') and not latest.get('comfy_finished'):
            released=False;save(status='submission_unknown',stage='Se perdió la confirmación; recupera este mismo envío',error=str(error)[:800])
        else:save(status='failed',stage='Plano no completado; conserva evidencia y prepara otra versión',error=str(error)[:800])
    finally:
        # Never unload models while any Comfy job is active; no global interrupt.
        if released:
            try:
                queue=await service.get(service.settings['comfy_url'],'/queue')
                if not queue.get('queue_running') and not queue.get('queue_pending'):
                    await service.post(service.settings['comfy_url'],'/free',json=dict(unload_models=True,free_memory=True))
            except Exception:
                released=False;save(status='cleanup_failed',stage='No se confirmó limpieza de Comfy; turno retenido')
        save(resources_released=released,elapsed_seconds=round(time.monotonic()-start_time,3))
        if released:
            service.enhancer_active=False;service.parody_speech_owner=None;service.release_analysis();service.wake.set()
