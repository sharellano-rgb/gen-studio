from . import qwen_runtime as qr
"""Serialized bounded jobs and an immutable full-duration result."""
import asyncio,json,os,shutil,subprocess,hashlib
from typing import Literal
from pathlib import Path
from uuid import UUID,uuid4,uuid5,NAMESPACE_URL
from fastapi import HTTPException
from pydantic import BaseModel,Field
from .face import ROOT
from .guided_composition import local_path,resolve as approved_head
from .head import digest
from .media import probe
from .long_plan import plan_segments,continuous_chunk_size
from .continuity import regeneration_indices


class AssembleRequest(BaseModel):
    request_key:UUID


class LongReview(BaseModel):
    approved:bool
    feedback:str=Field(default='',max_length=1000)


class SegmentRetry(BaseModel):
    request_key: UUID
    segment_index: int = Field(ge=0, strict=True)
    mode: Literal['analysis', 'generation'] = 'generation'
    reject_completed: bool = False
    seed: int | None = Field(default=None, ge=0, le=9007199254740991, strict=True)


def json_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def write_once(path, content):
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError('Ya existe otro checkpoint de recuperación; se conserva.')
        return
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_bytes(content)
    temp.replace(path)


@qr.bound
def lineage(service, parent):
    ids = set()
    current = parent
    while current:
        if current['id'] in ids or len(ids) > 100:
            raise ValueError('La procedencia de esta variante no es válida.')
        ids.add(current['id'])
        previous = current['parameters'].get('long_variant_parent_id')
        if not previous:
            break
        current = service.store.job(previous)
        if not current:
            raise ValueError('Falta la toma de origen de una variante.')
    return ids


@qr.bound
def validate_retry_plan(service, parent, plan, allow_active_current=False):
    p = parent['parameters']
    if p.get('operation') != 'long_character' or p.get('engine') != 'h3_direct':
        raise ValueError('La recuperación por tramo requiere un vídeo completo H3 directo.')
    with service.store.connect() as db:
        row = db.execute('SELECT payload FROM jobs WHERE id=?', (parent['id'],)).fetchone()
    if not row or json.loads(row[0]) != p:
        raise ValueError('Cambió la configuración registrada de la toma.')
    if not isinstance(plan, dict) or not plan.get('segments') or plan.get('frames') != p['frames']:
        raise ValueError('Falta el plan original completo.')
    plan_segments(p['frames'], scene_cuts=p.get('direct_scene_cuts', False), chunk_frames=continuous_chunk_size(p),validate_only=True)
    asset = service.store.asset(p['video_id'])
    if not asset or asset.get('kind') != 'video':
        raise ValueError('Falta el vídeo original.')
    source = local_path(service.root, service.root/'inputs'/asset['filename'])
    source_sha = digest(source)
    if source_sha != plan.get('source_sha256') or source_sha != p.get('direct_source_sha256'):
        raise ValueError('Cambió el vídeo fuente; no se recupera otro contenido.')
    hashes = []
    for ref_id in p.get('direct_reference_ids', []):
        ref = service.store.asset(ref_id)
        if not ref or ref.get('kind') != 'image':
            raise ValueError('Falta una referencia original.')
        hashes.append(digest(local_path(service.root, service.root/'inputs'/ref['filename'])))
    if not hashes or hashes != p.get('direct_reference_hashes'):
        raise ValueError('Cambió una referencia original.')
    allowed = lineage(service, parent)
    cursor = 0
    for index, s in enumerate(plan['segments']):
        if s.get('index') != index or s.get('start') != cursor or type(s.get('keep')) is not int or s['keep'] <= 0:
            raise ValueError('Los tramos no conservan el orden y la duración originales.')
        cursor += s['keep']
        if type(s.get('render_frames')) is not int or s['render_frames'] < s['keep']:
            raise ValueError('El relleno temporal de un tramo cambió.')
        if digest(local_path(service.root, s['path'])) != s.get('sha256'):
            raise ValueError('Cambió una entrada preparada de tramo.')
        if not s.get('job_id'):
            continue
        child = service.store.job(s['job_id'])
        cp = (child or {}).get('parameters', {})
        if not child or cp.get('long_parent_id') not in allowed or cp.get('long_segment_index') != index or cp.get('long_keep_frames') != s['keep'] or cp.get('frames') != s['render_frames'] or cp.get('video_id') != s.get('asset_id') or cp.get('direct_source_sha256') != s['sha256']:
            raise ValueError('Un hijo no corresponde al tramo original.')
        if bool(cp.get('direct_video_only')) != bool(p.get('direct_video_only')):
            raise ValueError('La opción de audio del tramo no corresponde al vídeo completo.')
        if cp.get('direct_reference_ids') != p.get('direct_reference_ids') or cp.get('direct_reference_hashes') != hashes:
            raise ValueError('Las referencias de un hijo no corresponden al vídeo completo.')
        if 'generation_seed' in s and cp.get('seed')!=s['generation_seed']:
            raise ValueError('Cambió la semilla registrada de un tramo.')
        if child['status'] not in ('completed', 'failed', 'cancelled') and not (allow_active_current and cp.get('long_parent_id')==parent['id']):
            raise ValueError('Hay un tramo activo o con envío sin confirmar; no se repetirá.')
        if child['status'] == 'completed':
            output = service.root/'results'/(child['id']+'.mp4')
            if not child.get('result_available') or not output.is_file() or digest(output) != child.get('direct_result_sha256'):
                raise ValueError('Cambió o falta la salida de un tramo terminado.')
            if p.get('direct_dynamic_prompts') and (s.get('prompt_analysis') or {}).get('id') != cp.get('shot_prompt_analysis_id'):
                raise ValueError('Falta la procedencia del prompt de un tramo terminado.')
            if p.get('direct_dynamic_prompts'):
                proposal=s['prompt_analysis']['proposal']
                if cp.get('prompt')!=proposal.get('prompt_original',proposal.get('prompt')):
                    raise ValueError('Cambió el prompt original de un tramo terminado.')
    if cursor != p['frames']:
        raise ValueError('Los tramos no cubren todos los fotogramas.')


@qr.bound
def validate_variant(service, parent, plan):
    expected = parent['parameters'].get('long_variant_manifest_sha256')
    if not expected:
        return
    folder = service.root/'jobs'/parent['id']
    path = folder/'long-recovery.json'
    if not path.is_file() or digest(path) != expected:
        raise ValueError('Cambió el registro de recuperación.')
    record = json.loads(path.read_text(encoding='utf-8'))
    origin = service.store.job(record['parent_id'])
    origin_plan = service.root/'jobs'/record['parent_id']/'long-plan.json'
    if not origin or digest(origin_plan) != record['source_plan_sha256']:
        raise ValueError('Cambió el plan de la toma de origen.')
    validate_retry_plan(service, parent, plan, allow_active_current=True)
    request=record['request'];selected=plan['segments'][request['segment_index']]
    p=parent['parameters']
    if p.get('long_variant_retry_index')!=request['segment_index'] or p.get('long_variant_mode')!=request['mode']:
        raise ValueError('Cambió la selección del reintento.')
    if selected.get('generation_seed')!=record.get('selected_seed'):
        raise ValueError('Cambió la semilla elegida para este tramo.')
    if selected.get('job_id'):
        child=service.store.job(selected['job_id'])
        if not child or child['parameters'].get('long_parent_id')!=parent['id']:
            raise ValueError('El tramo nuevo no pertenece a esta variante.')
    if request['mode']=='generation' and json_sha(selected.get('prompt_analysis'))!=record['selected_analysis_sha256']:
        raise ValueError('Cambió el análisis seleccionado para generar.')
    if json_sha(plan.get('recovery_reference_traits'))!=record['recovery_reference_traits_sha256']:
        raise ValueError('Cambió la identidad de referencia en la recuperación.')
    for analysis in record['reused_analyses']:
        if json_sha(plan['segments'][analysis['index']].get('prompt_analysis'))!=analysis['sha256']:
            raise ValueError('Cambió un análisis conservado.')
    for copied in record['checkpoints']:
        for path in (folder/copied['name'], service.root/'jobs'/record['parent_id']/copied['name']):
            if not path.is_file() or digest(path) != copied['sha256']:
                raise ValueError('Cambió un análisis conservado de la toma anterior.')
    for reused in record['reused_children']:
        child = service.store.job(reused['id'])
        if not child or child['status'] != 'completed' or json_sha(child['parameters']) != reused['payload_sha256'] or child.get('direct_result_sha256') != reused['result_sha256'] or child.get('direct_effective_prompt') != reused['effective_prompt']:
            raise ValueError('Cambió un tramo conservado; no se regenera automáticamente.')
        segment = plan['segments'][reused['segment_index']]
        if segment.get('job_id') != reused['id'] or json_sha(segment.get('prompt_analysis')) != reused['analysis_sha256']:
            raise ValueError('Cambió el vínculo o prompt de un tramo conservado.')


@qr.bound
def analysis_checkpoint(service, parent, segment):
    path = service.root/'jobs'/parent['id']/f"shot-prompt-{segment['index']}.json"
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None


@qr.bound
def retry_options(service, parent, segment, checkpoint=None):
    if parent['parameters'].get('engine') != 'h3_direct' or parent['status'] not in ('completed','failed','cancelled') or segment.get('passthrough'):
        return []
    child = service.store.job(segment.get('job_id',''))
    if child and child['status'] not in ('completed','failed','cancelled'):
        return []
    if checkpoint and not checkpoint.get('vram_released'):
        return []
    if child:
        if parent['parameters'].get('direct_dynamic_prompts'):
            return (['generation'] if segment.get('prompt_analysis') and checkpoint and checkpoint.get('status')=='completed' else [])+['analysis']
        return ['generation']
    return ['analysis'] if parent['parameters'].get('direct_dynamic_prompts') else ['generation']


@qr.bound
def parent_of(service,job):
    if job['parameters'].get('operation')=='long_assembly':
        job=service.store.job(job['parameters']['long_parent_id'])
    if not job or job['parameters'].get('operation')!='long_character':raise ValueError('Selecciona un vídeo largo de GEN STUDIO.')
    return job


@qr.bound
def load_plan(service,parent):
    path=service.root/'jobs'/parent['id']/'long-plan.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


@qr.bound
def accepted_child(service,parent):
    if parent['parameters'].get('operation')!='long_character':return None
    plan=load_plan(service,parent)
    for segment in (plan or {}).get('segments',[]):
        child=service.store.job(segment.get('job_id') or '')
        cp=(child or {}).get('parameters',{})
        if (child and child.get('prompt_id') and child['status'] not in {'completed','failed','cancelled'}
                and cp.get('long_parent_id')==parent['id']
                and cp.get('long_segment_index')==segment['index']
                and cp.get('video_id')==segment.get('asset_id')):
            return child
    return None

@qr.bound
def create_retry_variant(service, parent, body):
    request = {'parent_id':parent['id'], **body.model_dump(mode='json', exclude={'request_key','seed'})}
    if body.seed is not None:
        if body.mode!='generation':raise ValueError('La semilla alternativa sólo se aplica al rehacer la generación.')
        request['seed']=body.seed
    with service.store.connect() as db:
        row = db.execute('SELECT state FROM jobs WHERE request_key=?',(str(body.request_key),)).fetchone()
    if row:
        existing = json.loads(row[0])
        if existing['parameters'].get('long_variant_request') != request:
            raise ValueError('Esta clave ya corresponde a otra recuperación.')
        return existing
    if parent['status'] not in ('failed','cancelled','completed'):
        raise ValueError('Espera a que el vídeo termine o se detenga; no se repiten envíos inciertos.')
    plan = load_plan(service,parent)
    validate_retry_plan(service,parent,plan)
    validate_variant(service,parent,plan)
    if body.segment_index >= len(plan['segments']):
        raise ValueError('El tramo seleccionado no existe.')
    selected = plan['segments'][body.segment_index]
    checkpoint = analysis_checkpoint(service,parent,selected)
    if body.mode not in retry_options(service,parent,selected,checkpoint):
        raise ValueError('Este tramo no admite ese reintento; revisa su estado y la liberación del análisis.')
    child = service.store.job(selected.get('job_id',''))
    if child and child['status']=='completed' and not body.reject_completed:
        raise ValueError('Confirma que quieres reemplazar este tramo completado en una copia nueva.')
    if body.mode=='generation' and parent['parameters'].get('direct_dynamic_prompts') and (not selected.get('prompt_analysis') or not checkpoint or checkpoint.get('status')!='completed'):
        raise ValueError('Primero reintenta el análisis de este tramo.')
    origin_folder = service.root/'jobs'/parent['id']
    source_plan_sha = digest(origin_folder/'long-plan.json')
    affected=regeneration_indices(plan,body.segment_index)
    checkpoints = []
    reused = []
    contents = {}
    for s in plan['segments']:
        other = service.store.job(s.get('job_id',''))
        if s.get('prompt_analysis') and not (s['index']==body.segment_index and body.mode=='analysis'):
            from .shot_prompts import validate_preserved_analysis
            validate_preserved_analysis(parent['parameters'],s,analysis_checkpoint(service,parent,s),s['prompt_analysis'],parent['parameters']['direct_reference_hashes'])
        if s['index'] not in affected and other:
            if other['status'] != 'completed':
                raise ValueError('Otro tramo también necesita recuperación; selecciona primero ese tramo fallido.')
            reused.append({'id':other['id'],'segment_index':s['index'],'payload_sha256':json_sha(other['parameters']),
                'result_sha256':other['direct_result_sha256'],'effective_prompt':other.get('direct_effective_prompt'),
                'analysis_sha256':json_sha(s.get('prompt_analysis'))})
        for stem in ('shot-prompt','shot-english'):
            name = f"{stem}-{s['index']}.json"
            path = origin_folder/name
            if s['index']==body.segment_index and body.mode=='analysis':
                continue
            if path.is_file():
                content = path.read_bytes()
                if stem=='shot-prompt':
                    previous = json.loads(content)
                    if not previous.get('vram_released'):
                        raise ValueError('Un análisis no confirmó la liberación del modelo; revisa el trabajo original.')
                    if previous.get('status')!='completed':
                        raise ValueError('Otro análisis también necesita recuperación; selecciona ese tramo primero.')
                contents[name] = content
                checkpoints.append({'name':name,'sha256':hashlib.sha256(content).hexdigest()})
    next_plan = json.loads(json.dumps(plan))
    variant_id = str(uuid5(NAMESPACE_URL,'gen-studio/long-variant/'+str(body.request_key)))
    target = next_plan['segments'][body.segment_index]
    previous_job_id = target.get('job_id')
    for index in affected:
        s=next_plan['segments'][index]
        s['retry_of_job_id']=s.get('job_id')
        s['job_id']=None
        s['asset_id']=str(uuid5(NAMESPACE_URL,variant_id+'/segment-asset/'+str(index)))
        s.pop('continuity_guide',None)
    target['job_id'] = None
    target['asset_id'] = str(uuid5(NAMESPACE_URL,variant_id+'/segment-asset/'+str(body.segment_index)))
    target['retry_of_job_id'] = previous_job_id
    if body.seed is not None:target['generation_seed']=body.seed
    target.pop('reuse_prompt_analysis',None)
    if body.mode=='analysis':
        target.pop('prompt_analysis',None)
    else:
        target['reuse_prompt_analysis']=True
    traits = next((s.get('prompt_analysis',{}).get('proposal',{}).get('reference_traits') for s in plan['segments']
        if s.get('prompt_analysis',{}).get('proposal',{}).get('reference_traits')),None)
    if traits:
        next_plan['recovery_reference_traits'] = traits
    record = {'version':'long-recovery-v1','parent_id':parent['id'],'source_plan_sha256':source_plan_sha,
        'source_sha256':plan['source_sha256'],'reference_hashes':parent['parameters']['direct_reference_hashes'],
        'request':request,'replaced_job_id':previous_job_id,'reused_children':reused,'checkpoints':checkpoints,
        'selected_analysis_sha256':json_sha(target.get('prompt_analysis')) if body.mode=='generation' else None,
        'recovery_reference_traits_sha256':json_sha(next_plan.get('recovery_reference_traits')),
        'reused_analyses':[{'index':s['index'],'sha256':json_sha(s['prompt_analysis'])} for s in plan['segments'] if s['index']!=body.segment_index and s.get('prompt_analysis')]}
    if 'generation_seed' in target:record['selected_seed']=target['generation_seed']
    if len(affected)>1:record['replaced_segment_indices']=affected
    folder = service.root/'jobs'/variant_id
    folder.mkdir(parents=True,exist_ok=True)
    for name,content in contents.items():
        write_once(folder/name,content)
    write_once(folder/'long-plan.json',json.dumps(next_plan,ensure_ascii=False,indent=2).encode('utf-8'))
    write_once(folder/'long-recovery.json',json.dumps(record,ensure_ascii=False,indent=2).encode('utf-8'))
    p = {**parent['parameters'],'operation':'long_character','long_variant_parent_id':parent['id'],
        'long_variant_root_id':parent['parameters'].get('long_variant_root_id',parent['id']),
        'long_variant_revision':parent['parameters'].get('long_variant_revision',0)+1,
        'long_variant_request':request,'long_variant_retry_index':body.segment_index,
        'long_variant_mode':body.mode,'long_variant_manifest_sha256':digest(folder/'long-recovery.json')}
    result=service.store.create_job(variant_id,str(body.request_key),p)
    if continuous_chunk_size(p) and parent.get('direct_prompt_language'):
        from .prompt_language import validate_record
        language=validate_record(parent['direct_prompt_language'])
        result=service.store.update(variant_id,direct_prompt_language=language,direct_effective_prompt=language['prompt'])
    return result


@qr.bound
def approved_geometry(service,scene):
    if scene['parameters'].get('engine')!='h3_direct':return None
    copy=next((j for j in service.store.jobs() if j['parameters'].get('operation')=='geometry_copy'
        and j['parameters'].get('geometry_parent_id')==scene['id'] and j['status']=='completed'
        and j.get('result_available') and j.get('geometry_review',{}).get('approved')),None)
    if not copy:return None
    source=service.root/'results'/(scene['id']+'.mp4');path=service.root/'results'/(copy['id']+'.mp4')
    report=json.loads((service.root/'jobs'/copy['id']/'geometry-report.json').read_text(encoding='utf-8'))
    if not source.is_file() or digest(source)!=copy['parameters']['geometry_source_sha256'] or digest(source)!=scene.get('direct_result_sha256'):
        raise ValueError('Cambió un tramo original usado por un ajuste aprobado.')
    if not path.is_file() or digest(path)!=report['result_sha256'] or digest(path)!=copy.get('geometry_result_sha256') or report.get('frames')!=scene['parameters']['frames']:
        raise ValueError('Cambió una copia geométrica aprobada; no se monta otro vídeo.')
    return {'id':copy['id'],'path':str(path),'sha256':report['result_sha256']}


@qr.bound
def preferred_segments(service,parent,plan,use_approved):
    if not isinstance(plan,dict):raise ValueError('La preparación original no está disponible.')
    result=[];applied=0
    for segment in plan['segments']:
        s={**segment};job=service.store.job(s.get('job_id',''))
        if not s['passthrough']:
            if not job or job['status']!='completed' or not job.get('result_available'):
                raise ValueError('Primero deben terminar todos los tramos de la escena base.')
            s.update(path=str(service.root/'results'/(job['id']+'.mp4')))
            if job['parameters'].get('engine')=='h3_direct' and (not Path(s['path']).is_file() or digest(Path(s['path']))!=job.get('direct_result_sha256')):
                raise ValueError('Cambió una generación H3 directa de tramo.')
            if use_approved:
                geometry=approved_geometry(service,job)
                if geometry:
                    s.update(path=geometry['path'],approved_geometry_id=geometry['id']);applied+=1
                approved=next((j for j in service.store.jobs() if not geometry and j['parameters'].get('anchor_method')=='guided_composition'
                    and j['parameters'].get('anchor_parent_id')==job['id'] and j['status']=='completed'
                    and j.get('face_review',{}).get('identity_approved') and j.get('face_result_available')),None)
                if approved:
                    approved_head(service,job,approved['parameters']['guided_candidate'])
                    path=service.root/'results'/(approved['id']+'.face.mp4')
                    checkpoint=json.loads((service.root/'jobs'/approved['id']/'anchor-report.json').read_text())
                    if not path.is_file() or digest(path)!=checkpoint['result_sha256']:raise ValueError('Una composición aprobada cambió.')
                    s.update(path=str(path),approved_composition_id=approved['id']);applied+=1
        s['path']=str(local_path(service.root,s['path']))
        if not Path(s['path']).is_file():raise ValueError('Falta una salida de tramo.')
        s['sha256']=digest(Path(s['path']));result.append(s)
    return result,applied


@qr.bound
async def media(service,job,mode,inputs):
    folder=service.root/'jobs'/job['id'];folder.mkdir(parents=True,exist_ok=True)
    run=folder/(mode+'-'+str(uuid4()));run.mkdir()
    manifest={**inputs,'mode':mode,'run_root':str(run),'parent_pid':os.getpid()}
    path=run/'manifest.json';path.write_text(json.dumps(manifest),encoding='utf-8')
    process=None
    with (run/'stderr.log').open('wb') as errors:
        try:
            process=await asyncio.create_subprocess_exec(str(ROOT/'.venv/Scripts/python.exe'),'-u',
                str(ROOT/'scripts/long_video_media.py'),str(path),stdout=asyncio.subprocess.PIPE,stderr=errors,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            while line:=await process.stdout.readline():
                try:
                    e=json.loads(line.decode('utf-8'))
                    if 'stage' in e:service.store.update(job['id'],stage=e['stage'],progress={'value':e['value'],'max':e['max']})
                except (ValueError,UnicodeDecodeError):pass
            if await process.wait():
                error=run/'error.json'
                raise ValueError(json.loads(error.read_text())['error'] if error.exists() else 'Falló el montaje del vídeo. Consulta el registro.')
        finally:
            if process and process.returncode is None:
                if job.get('video_sequence_version') or job['parameters'].get('ninja_segments_version') or job['parameters'].get('sequence_parent_id') or job['parameters'].get('hybrid_parent_id'):
                    from .process_cleanup import stop_process_tree
                    await stop_process_tree(process)
                else:
                    process.terminate()
                    try:await asyncio.wait_for(process.wait(),5)
                    except TimeoutError:process.kill();await process.wait()
    return json.loads((run/'report.json').read_text(encoding='utf-8'))


@qr.bound
async def process(service,job):
    parent=parent_of(service,job);p=parent['parameters'];folder=service.root/'jobs'/parent['id'];folder.mkdir(parents=True,exist_ok=True)
    chunks=continuous_chunk_size(p)
    from . import continuity
    guided=continuity.enabled(p)
    if guided and job['parameters'].get('operation')=='long_character' and not load_plan(service,parent):
        catalog=await service.get(service.settings['comfy_url'],'/object_info/'+continuity.NODE)
        continuity.validate_catalog(catalog)
    plan_segments(p['frames'],scene_cuts=p.get('direct_scene_cuts',False),chunk_frames=chunks,validate_only=True)
    def stopped():
        if job['parameters'].get('operation')=='long_character' and service.store.job(job['id']).get('long_stop_requested'):
            if accepted_child(service,job):return False
            for child in service.store.unfinished_jobs():
                cp=child['parameters']
                if cp.get('long_parent_id')==job['id'] and type(cp.get('long_segment_index')) is int and child['status']=='queued' and not child.get('prompt_id'):
                    service.store.update(child['id'],status='cancelled',stage='Detenido antes del siguiente tramo',progress=None)
            service.store.update(job['id'],status='cancelled',stage='Vídeo detenido; los tramos terminados se conservan',progress=None)
            return True
        return False
    if stopped():return
    asset=service.store.asset(p['video_id']);source=local_path(service.root,service.root/'inputs'/asset['filename'])
    source_sha=digest(source);width,height=map(int,p['resolution'].split('x'))
    if p.get('engine')=='h3_direct' and p.get('direct_source_sha256')!=source_sha:
        raise ValueError('El vídeo fuente cambió desde el envío; se conservan los archivos.')
    plan=load_plan(service,parent)
    reviewed = None
    if p.get('direct_shot_plan_id'):
        from .studio_editor import reviewed_plan
        reviewed = await asyncio.to_thread(reviewed_plan,service,p)
    if plan:
        if source_sha!=plan['source_sha256']:raise ValueError('El vídeo fuente cambió. Se conservan los tramos anteriores.')
        if chunks:
            expected=plan_segments(p['frames'],plan.get('cuts',[]),chunk_frames=chunks)
            if guided:expected=continuity.contextual_segments(expected)
            if guided and plan.get('continuity_version')!=continuity.VERSION:raise ValueError('Cambió la política de continuidad del plan.')
            if (plan.get('chunk_frames')!=chunks or plan.get('start_frame')!=round(p.get('direct_start_seconds',0)*24)
                    or len(plan['segments'])!=len(expected)
                    or any(any(s.get(k)!=e[k] for k in ('index','start','keep','render_frames','passthrough')+(('context_frames','input_start','input_keep') if guided else ())) for s,e in zip(plan['segments'],expected))):
                raise ValueError('Cambió la duración o distribución de los tramos automáticos.')
        for s in plan['segments']:
            if digest(local_path(service.root,s['path']))!=s['sha256']:raise ValueError('Un tramo preparado cambió.')
        validate_variant(service,parent,plan)
        if reviewed:
            from .studio_editor import apply_reviewed_plan
            checked=await asyncio.to_thread(apply_reviewed_plan,service,p,plan)
            if any(s.get('shot_edit')!=e.get('shot_edit') or s.get('passthrough')!=e.get('passthrough') for s,e in zip(plan['segments'],checked['segments'])):
                raise ValueError('Cambió la edición de un plano revisado.')
    else:
        if job['parameters'].get('operation')=='long_assembly':raise ValueError('La preparación original no está disponible.')
        inputs={'source':str(source),'source_sha256':source_sha,'frames':p['frames'],'width':width,'height':height,'reference_audio':p.get('engine')=='h3_direct','scene_cuts':p.get('direct_scene_cuts',False)}
        if chunks:inputs.update(chunk_frames=chunks,start_frame=round(p.get('direct_start_seconds',0)*24))
        if guided:inputs['continuity_version']=continuity.VERSION
        if reviewed:
            inputs.update(reviewed_cuts=reviewed['cuts'],reviewed_passthrough_indices=[e['index'] for e in p.get('direct_shot_edits',[]) if e.get('passthrough')])
        plan=await media(service,job,'prepare',inputs)
        if reviewed:
            from .studio_editor import apply_reviewed_plan
            plan=await asyncio.to_thread(apply_reviewed_plan,service,p,plan)
        for s in plan['segments']:
            s.update(asset_id=str(uuid4()),job_id=None)
        (folder/'long-plan.json').write_text(json.dumps(plan,indent=2),encoding='utf-8')
    if job['parameters'].get('operation')=='long_character':
        from .visual_targets import validate_plan_cuts
        validate_plan_cuts(p,plan)
        shared_language=None
        if chunks:
            from .prompt_language import for_job
            await for_job(service,service.store.job(parent['id']))
            shared_language=service.store.job(parent['id'])['direct_prompt_language']
        if p.get('direct_dynamic_prompts') and not job.get('assembly_only_retry'):
            from .shot_prompts import prepare_all
            if not await prepare_all(service, job, parent, plan, stopped):return
            (folder/'long-plan.json').write_text(json.dumps(plan,indent=2),encoding='utf-8')
        if job.get('assembly_only_retry'):
            preferred_segments(service,parent,plan,False)
        for i,s in enumerate(plan['segments']):
            if stopped():return
            if s['passthrough']:continue
            guide=None
            if guided and s['context_frames']:
                previous=plan['segments'][i-1];previous_job=service.store.job(previous['job_id'])
                if not previous_job or previous_job['status']!='completed' or not previous_job.get('result_available'):
                    raise ValueError('Primero debe terminar el tramo que aporta la continuidad.')
                previous_path=service.root/'results'/(previous_job['id']+'.mp4')
                previous_sha=previous_job.get('direct_result_sha256')
                if not previous_path.is_file() or digest(previous_path)!=previous_sha:raise ValueError('Cambió el tramo anterior de continuidad.')
                frame_index=previous.get('context_frames',0)+previous['keep']-1
                guide=s.get('continuity_guide')
                if guide:
                    if (guide.get('previous_job_id')!=previous_job['id'] or guide.get('source_sha256')!=previous_sha
                            or guide.get('frame_index')!=frame_index or digest(local_path(service.root,guide['path']))!=guide['sha256']):
                        raise ValueError('La guía guardada no corresponde al tramo anterior.')
                else:
                    extracted=await media(service,job,'guide',{'source':str(previous_path),'source_sha256':previous_sha,
                        'frame_index':frame_index,'width':width,'height':height})
                    guide={**extracted,'version':continuity.VERSION,'previous_job_id':previous_job['id']}
                    s['continuity_guide']=guide
                    (folder/'long-plan.json').write_text(json.dumps(plan,indent=2),encoding='utf-8')
            clip_id=s['asset_id'];clip=service.root/'inputs'/(clip_id+'.mp4')
            if not clip.exists():shutil.copy2(s['path'],clip)
            if digest(clip)!=s['sha256']:raise ValueError('Una entrada de tramo cambió.')
            if not service.store.asset(clip_id):
                service.store.add_asset({'id':clip_id,'filename':clip.name,'name':f'Tramo {i+1} · {s["start"]/24:.2f}–{(s["start"]+s["keep"])/24:.2f} s',
                    'kind':'video','duration':s['render_frames']/24,'audio':s.get('audio_reference',{}).get('status')=='preserved','width':width,'height':height,'sha256':s['sha256']})
            if not s['job_id']:
                child_p={k:v for k,v in p.items() if k not in ('operation','direct_chunk_frames','direct_chunk_version','direct_continuity','direct_continuity_version')}
                child_p.update(video_id=clip_id,frames=s['render_frames'],long_parent_id=parent['id'],long_segment_index=i,
                    long_keep_frames=s['keep'],preserve_face=False)
                if 'generation_seed' in s:child_p['seed']=s['generation_seed']
                if guided:child_p['long_context_frames']=s['context_frames']
                if guide:child_p['direct_continuity_guide']=guide
                if p.get('direct_scene_cuts'):child_p['direct_scene_cuts']=False
                if s.get('shot_edit'):
                    from .studio_editor import shot_payload
                    child_p=shot_payload(child_p,s)
                child_p['direct_dynamic_prompts']=False
                if p.get('direct_dynamic_prompts'):
                    proposal=s['prompt_analysis']['proposal']
                    child_p.update(prompt=proposal.get('prompt_original',proposal['prompt']), direct_binding_mode='manual',
                                   direct_direction='', shot_prompt_analysis_id=s['prompt_analysis']['id'])
                if child_p.get('engine')=='h3_direct':child_p.update(direct_source_sha256=s['sha256'],direct_start_seconds=0)
                child_p.pop('direct_shot_plan_id',None)
                child_p.pop('direct_shot_edits',None)
                for name in list(child_p):
                    if name.startswith('long_variant_'):child_p.pop(name)
                if p.get('direct_shot_plan_id'):
                    child_p.update(long_reviewed_plan_id=p['direct_shot_plan_id'],long_shot_edit=s.get('shot_edit'))
                child=service.store.create_job(str(uuid4()),str(uuid5(NAMESPACE_URL,parent['id']+f'/segment/{i}')),child_p)
                language=shared_language or (s.get('prompt_analysis') or {}).get('language')
                if language:
                    from .prompt_language import validate_record
                    validate_record(language)
                    if child_p['prompt']!=language['original']:raise ValueError('El tramo cambió después de traducir.')
                    service.store.update(child['id'],direct_prompt_language=language,direct_effective_prompt=language['prompt'])
                s['job_id']=child['id']
                (folder/'long-plan.json').write_text(json.dumps(plan,indent=2),encoding='utf-8')
            child=service.store.job(s['job_id'])
            if bool(child['parameters'].get('direct_video_only')) != bool(p.get('direct_video_only')):
                raise ValueError('La opción de audio del tramo no corresponde al vídeo completo.')
            if guided and (child['parameters'].get('long_context_frames')!=s['context_frames']
                    or child['parameters'].get('direct_continuity_guide')!=guide):
                raise ValueError('El hijo no conserva la guía de continuidad registrada.')
            if child['status']=='submission_unknown':
                service.store.update(job['id'],status='submission_unknown',stage=f'Tramo {i+1}: envío sin confirmar',error='Revisa ComfyUI y el tramo antes de repetir.');return
            if child['status'] in ('failed','cancelled'):raise ValueError(f'Tramo {i+1}: '+(child.get('error') or child['status']))
            if child['status']!='completed':
                if job.get('assembly_only_retry'):
                    raise ValueError('Un tramo dejó de estar terminado; el reintento sólo permite montaje, no regeneración.')
                service.store.update(job['id'],status='running',stage=f'Generando tramo {i+1} de {len(plan["segments"])}',progress={'value':i,'max':len(plan['segments'])},long_active_child=child['id'])
                if p.get('engine')=='h3_direct':await service._run(child)
                else:await service.run(child)
                child=service.store.job(s['job_id'])
                if child['status']=='queued':service.store.update(job['id'],status='queued',stage='Esperando la cola externa para continuar');return
                if child['status']=='submission_unknown':
                    service.store.update(job['id'],status='submission_unknown',error=f'Tramo {i+1}: envío sin confirmar; no se repetirá automáticamente.');return
                if child['status']!='completed':raise ValueError(f'Tramo {i+1}: '+(child.get('error') or child['status']))
        if stopped():return
    checkpoint=service.root/'jobs'/job['id']/'long-result.json';target=service.root/'results'/(job['id']+'.mp4')
    if checkpoint.exists():
        result=json.loads(checkpoint.read_text())
        if not target.exists() or digest(target)!=result['sha256']:raise ValueError('La salida registrada cambió; se conserva el registro.')
    else:
        assembly=job['parameters'].get('operation')=='long_assembly'
        segments,applied=preferred_segments(service,parent,plan,assembly)
        if assembly and not applied:raise ValueError('Primero aprueba una copia ajustada de al menos un tramo.')
        # Guided face compositions are 2x; uniform geometry preserves native size.
        if any(s.get('approved_composition_id') for s in segments):width*=2;height*=2
        join_inputs={'segments':segments,'audio_source':str(source),'audio_source_sha256':source_sha,
            'frames':p['frames'],'width':width,'height':height,'scene_cuts':p.get('direct_scene_cuts',False)}
        if chunks:join_inputs.update(chunk_frames=chunks,audio_start_frame=round(p.get('direct_start_seconds',0)*24))
        if guided:join_inputs['continuity_version']=continuity.VERSION
        result=await media(service,job,'join',join_inputs)
        result.update(approved_detail_segments=applied,total_segments=len(segments),source_job=parent['id'],
            approved_geometry_segments=sum(bool(s.get('approved_geometry_id')) for s in segments),
            segment_sources=[{'job_id':s.get('job_id'),'approved_composition_id':s.get('approved_composition_id'),'approved_geometry_id':s.get('approved_geometry_id'),'sha256':s['sha256'],'keep':s['keep']} for s in segments])
        target.parent.mkdir(exist_ok=True)
        if target.exists() and digest(target)!=result['sha256']:raise ValueError('Existe otro resultado; no se sobrescribe.')
        if not target.exists():shutil.copy2(result['path'],target)
        checkpoint.write_text(json.dumps(result,indent=2),encoding='utf-8')
    service.store.update(job['id'],status='completed',result_available=True,progress=None,stage='Vídeo completo listo para revisión',
        scene_resolution=f'{width}x{height}',long_review={'identity_approved':False,'review_required':True},
        long_summary={'segments':result['total_segments'],'approved_detail_segments':result['approved_detail_segments'],'approved_geometry_segments':result.get('approved_geometry_segments',0),'frames':p['frames']})
    service.event(job['id'],'Tramos unidos en CPU. Audio conservado desde el vídeo fuente; revisión de identidad pendiente.')


def register(app,settings):
    @app.post('/api/jobs/{job_id}/retry-long-segment',status_code=202)
    async def retry_segment(job_id:UUID,body:SegmentRetry):
        service=app.state.jobs
        async with service.lock:
            parent=service.store.job(str(job_id))
            if not parent:raise HTTPException(404,'Trabajo no encontrado.')
            with service.store.connect() as db:
                prior=db.execute('SELECT state FROM jobs WHERE request_key=?',(str(body.request_key),)).fetchone()
            if not prior and not settings['generation_enabled']:raise HTTPException(503,'Procesamiento desactivado.')
            try:result=await asyncio.to_thread(create_retry_variant,service,parent,body)
            except (ValueError,KeyError,OSError,TypeError) as error:raise HTTPException(409,str(error)) from error
            service.wake.set()
            return result
    @app.post('/api/jobs/{job_id}/retry-long-assembly',status_code=202)
    async def retry_assembly(job_id:UUID,body:AssembleRequest):
        if not settings['generation_enabled']:raise HTTPException(503,'Procesamiento desactivado.')
        service=app.state.jobs
        async with service.lock:
            job=service.store.job(str(job_id))
            if not job:raise HTTPException(404,'Trabajo no encontrado.')
            if job.get('long_assembly_retry_key')==str(body.request_key):return job
            try:
                if job['parameters'].get('operation')!='long_character' or job['status']!='failed' or job.get('long_stop_requested'):
                    raise ValueError('Selecciona un vídeo completo cuyo montaje falló y cuyos tramos ya terminaron.')
                plan_segments(job['parameters']['frames'],scene_cuts=job['parameters'].get('direct_scene_cuts',False),chunk_frames=continuous_chunk_size(job['parameters']),validate_only=True)
                preferred_segments(service,job,load_plan(service,job),False)
                source=service.root/'inputs'/service.store.asset(job['parameters']['video_id'])['filename']
                if digest(source)!=load_plan(service,job)['source_sha256']:raise ValueError('La fuente cambió; no se reintenta el montaje.')
                if (service.root/'results'/(job['id']+'.mp4')).exists():raise ValueError('Ya existe una salida; se conserva y no se sobrescribe.')
                failures=job.get('long_assembly_failures',[])+[{'error':job.get('error'),'updated_at':job.get('updated_at')}]
                result=service.store.update(job['id'],status='queued',stage='Reintentando montaje con los tramos terminados',error=None,progress=None,
                    assembly_only_retry=True,long_assembly_retry_key=str(body.request_key),long_assembly_failures=failures)
            except (ValueError,KeyError,OSError) as error:raise HTTPException(409,str(error)) from error
            service.wake.set();return result

    @app.post('/api/jobs/{job_id}/stop-long-video')
    async def stop(job_id:UUID):
        service=app.state.jobs
        async with service.lock:
            job=service.store.job(str(job_id))
            if not job:raise HTTPException(404,'Trabajo no encontrado.')
            if job['parameters'].get('operation')!='long_character' or job['status'] not in ('queued','preparing','running'):
                raise HTTPException(409,'Selecciona un vídeo largo que siga en preparación o generación.')
            return service.store.update(job['id'],long_stop_requested=True,stage='Se detendrá al terminar el tramo actual')

    @app.get('/api/jobs/{job_id}/long-segments')
    async def segments(job_id:UUID):
        service=app.state.jobs;job=service.store.job(str(job_id))
        if not job:raise HTTPException(404,'Trabajo no encontrado.')
        try:parent=parent_of(service,job)
        except ValueError as e:raise HTTPException(409,str(e)) from e
        plan=load_plan(service,parent)
        result=[]
        for s in (plan or {}).get('segments',[]):
            child=service.store.job(s.get('job_id',''))
            geometry=None
            if child and child['status']=='completed':geometry=approved_geometry(service,child)
            checkpoint=analysis_checkpoint(service,parent,s) if parent['parameters'].get('direct_dynamic_prompts') else None
            analysis={k:checkpoint.get(k) for k in ('status','error','stage','vram_released')} if checkpoint else None
            if analysis and checkpoint.get('proposal',{}).get('needs_clarification'):
                analysis['error']=' '.join(checkpoint['proposal'].get('warnings',[])) or 'La asignación requiere aclaración.'
            result.append({k:s[k] for k in ['index','start','keep','passthrough','job_id']}|{
                'regeneration_indices':regeneration_indices(plan,s['index']),
                'prompt_analysis':s.get('prompt_analysis'),'analysis_status':analysis,'job':child,'approved_geometry':geometry,
                'passthrough_reason':s.get('passthrough_reason') or ('Conservado por decisión del usuario.' if s.get('shot_edit',{}).get('passthrough') else None),
                'shot_edit':s.get('shot_edit'),'retry_modes':retry_options(service,parent,s,checkpoint)})
        return {'parent_id':parent['id'],'frames':parent['parameters']['frames'],'segments':result,'cuts':(plan or {}).get('cuts',[]),
                'start_seconds':parent['parameters'].get('direct_start_seconds',0) if parent['parameters'].get('direct_chunk_frames') else 0,
                'parent_status':parent['status'],'variant_parent_id':parent['parameters'].get('long_variant_parent_id'),'variant_revision':parent['parameters'].get('long_variant_revision',0)}

    @app.post('/api/jobs/{job_id}/assemble-approved-segments',status_code=202)
    async def assemble(job_id:UUID,body:AssembleRequest):
        if not settings['generation_enabled']:raise HTTPException(503,'Generación desactivada.')
        service=app.state.jobs
        async with service.lock:
            job=service.store.job(str(job_id))
            if not job:raise HTTPException(404,'Trabajo no encontrado.')
            try:
                parent=parent_of(service,job)
                plan_segments(parent['parameters']['frames'],scene_cuts=parent['parameters'].get('direct_scene_cuts',False),chunk_frames=continuous_chunk_size(parent['parameters']),validate_only=True)
                if parent['status']!='completed':raise ValueError('Espera a que termine el vídeo base completo.')
                _,count=preferred_segments(service,parent,load_plan(service,parent),True)
                if not count:raise ValueError('Primero aprueba una copia ajustada de tramo.')
                child=service.store.create_job(str(uuid4()),str(body.request_key),{**parent['parameters'],'operation':'long_assembly','long_parent_id':parent['id']})
            except (ValueError,KeyError,OSError) as e:raise HTTPException(409,str(e)) from e
            service.wake.set();return child

    @app.post('/api/jobs/{job_id}/review-long-video')
    async def review(job_id:UUID,body:LongReview):
        service=app.state.jobs
        async with service.lock:
            job=service.store.job(str(job_id))
            if not job:raise HTTPException(404,'Trabajo no encontrado.')
            try:
                parent_of(service,job)
                if job['status']!='completed' or not job.get('result_available'):raise ValueError('Espera al vídeo completo.')
                report=json.loads((service.root/'jobs'/job['id']/'long-result.json').read_text())
                if digest(service.root/'results'/(job['id']+'.mp4'))!=report['sha256']:raise ValueError('La salida cambió; no se guarda su revisión.')
                return service.store.update(job['id'],long_review={'identity_approved':body.approved,'review_required':not body.approved,
                    'human_review':'approved' if body.approved else 'rejected','human_feedback':body.feedback},
                    stage='Vídeo completo revisado por el usuario' if body.approved else 'Vídeo completo · Incidencia registrada')
            except (ValueError,KeyError,OSError) as e:raise HTTPException(409,str(e)) from e
