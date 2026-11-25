"""Re-render a verified H3 pass with its archived conditioning and ordered references."""
import hashlib
import json
import shutil
from copy import deepcopy
from pathlib import Path

from .guided_composition import local_path
from .head import digest
from .job_cancel import thread_checkpoint
from .video_sequences import save


OPERATION = 'h3_recorded_render'
JOINT = 'direct-cast-side-joint-reference'
OPENING_RECEIPT = 'recorded-opening-replay.json'


def verified_opening(folder, job, original_sha):
    receipt = json.loads((folder / OPENING_RECEIPT).read_text('utf8'))
    params = job['parameters']
    keep = params.get('sequence_keep', params.get('frames'))
    count = job.get('reference_opening_frames_repaired')
    if (receipt.get('policy') != 'verified-opening-v1' or receipt.get('job') != folder.name
            or receipt.get('before_sha256') != original_sha
            or receipt.get('reproduced_sha256') != job.get('direct_result_sha256')
            or (job.get('direct_review') or {}).get('human_review') != 'approved'
            or type(count) is not int or not 1 <= count <= 3 or receipt.get('opening_frames') != count
            or params.get('sequence_context',0) or type(keep) is not int or not count < keep <= params['frames']
            or receipt.get('visible_interval') != [0, keep]):
        raise ValueError('La verificación del inicio no corresponde al tramo aprobado.')
    return receipt


def recorded_pass(folder, job, uploads):
    checksum = job.get('direct_result_sha256')
    if not checksum:
        raise ValueError('Falta la huella del resultado.')
    if len(uploads['references']) == 1:
        phase, label = 'direct', 'direct-finalize'
    else:
        review = job.get('direct_review') or {}
        if (not review.get('identity_approved') or review.get('review_required')
                or review.get('human_review') == 'rejected' or review.get('approved_result_sha256') != checksum):
            raise ValueError('La aprobación del plano conjunto no corresponde a este vídeo.')
        phase, label = JOINT, 'direct-joint-cast-reference-finalize'
    final = json.loads((folder/(label+'.json')).read_text('utf8'))
    historical_sha = final['files'][final['video']]
    if historical_sha != checksum:
        if phase != JOINT or not (folder / OPENING_RECEIPT).is_file():
            raise ValueError('El vídeo seleccionado no corresponde a esta pasada archivada.')
        verified_opening(folder, job, historical_sha)
    if (final.get('reference_opening_frames_repaired')
            or not final.get('no_facial_postprocessing') or not (folder/(phase+'-history.json')).is_file()):
        raise ValueError('El vídeo seleccionado no corresponde a esta pasada archivada.')
    return phase, label, final


def reference_nodes(count):
    return ['16', *(f'reference_{index}' for index in range(1, count))]


def executed_graph(history):
    # ComfyUI may annotate nodes with its runtime cache fingerprint.
    return {key: {k:v for k,v in node.items() if k != 'is_changed'}
            for key, node in history['prompt'][2].items()}


def available_source(root, job):
    if job['status']!='completed' or not job.get('result_available'):return None
    p=job['parameters']
    if (p.get('engine')!='h3_direct' or p.get('direct_preset')!='hands' or p.get('operation')
            or p.get('sequence_context') or not p.get('direct_video_only')):return None
    source=(job.get('sequence_recovered_version') or {}).get('source_job',job['id'])
    folder=root/'jobs'/source
    try:
        uploads=json.loads((folder/'direct-uploads.json').read_text('utf8'))
        recorded_pass(folder, job, uploads)
        return source
    except (KeyError,OSError,ValueError,TypeError):
        pass
    return None


def recipe(service, source_id):
    job = service.store.job(source_id)
    if not job or job['status'] != 'completed' or job.get('video_sequence_version'):
        raise ValueError('Selecciona una toma H3 terminada.')
    p = job['parameters']
    if (p.get('engine') != 'h3_direct' or p.get('direct_preset') != 'hands'
            or p.get('operation') or not p.get('direct_video_only') or p.get('sequence_context')):
        raise ValueError('Esta repetición admite una toma directa Realismo Manos con vídeo guía sin audio y sin solapamiento.')
    folder = local_path(service.root, service.root/'jobs'/source_id)
    def record(name):
        return json.loads((folder/name).read_text(encoding='utf8'))
    prep = record('direct-prepare.json')
    uploads = record('direct-uploads.json')
    phase, label, final = recorded_pass(folder, job, uploads)
    graph = record(phase+'-workflow.api.json')
    history = record(phase+'-history.json')
    raw = record(phase+'-complete.json')
    published = local_path(service.root, service.root/'results'/(source_id+'.mp4'))
    if (history['status']['status_str'] != 'success' or executed_graph(history) != graph
            or history['prompt'][1] != raw['prompt_id']
            or digest(published) != job['direct_result_sha256'] or final.get('reference_opening_frames_repaired')
            or final.get('protected',{}).get(prep['video'])!=prep['files'][prep['video']]
            or not final.get('no_facial_postprocessing') or final.get('protected',{}).get(raw['path']) != raw['sha256']):
        raise ValueError('La salida publicada no corresponde a la primera pasada completa de este workflow.')
    refs = list(prep['references'])
    names = list(uploads['references'])
    if len(refs) != len(names) or not names:
        raise ValueError('Faltan referencias de la receta archivada.')
    if phase == JOINT:
        guide = job['approved_cast_reference']
        guide_path = local_path(folder, guide['path'])
        if digest(guide_path) != guide['sha256'] or final['audio'].get('video_reencoded') is not False:
            raise ValueError('Cambió la guía conjunta o el montaje del plano aprobado.')
        names.append(record('face-upload-'+guide['sha256']+'.json')['name'])
        refs.append(str(guide_path))
        prep['files'][str(guide_path)] = guide['sha256']
    nodes = reference_nodes(len(refs))
    if ({k for k,v in graph.items() if v['class_type']=='LoadImage'} != set(nodes)
            or [k for k,v in graph.items() if v['class_type']=='LoadVideo'] != ['14']
            or graph['14']['inputs']['file'] != uploads['video'] or graph['28']['class_type']!='SaveVideo'
            or [graph[n]['inputs']['image'] for n in nodes] != names):
        raise ValueError('Las entradas del workflow no coinciden con las referencias archivadas.')
    paths = [local_path(folder,v) for v in (prep['h3_video'],*refs,prep['video'])]
    for path in paths:
        if digest(path)!=prep['files'][str(path)]:
            raise ValueError('Cambió un archivo de la receta archivada.')
    original_finalizers=[m for m in folder.glob(label+'-*/manifest.json')
                         if m.parent==Path(final['video']).parent]
    if len(original_finalizers)!=1:
        raise ValueError('Falta el manifiesto del montaje original.')
    manifest=json.loads(original_finalizers[0].read_text('utf8'))
    if manifest.get('target_band') or manifest['mode']!='finalize' or manifest['frames']!=p['frames']:
        raise ValueError('La toma incluye una composición espacial adicional.')
    if phase == JOINT and (manifest.get('video') != raw['path'] or manifest.get('video_sha256') != raw['sha256']
            or manifest.get('source') != prep['video'] or manifest.get('source_sha256') != prep['files'][prep['video']]
            or digest(local_path(folder, raw['path'])) != raw['sha256']
            or digest(local_path(folder, final['video'])) != final['files'][final['video']]):
        raise ValueError('No se pudo vincular la pasada conjunta al montaje aprobado.')
    if final['files'][final['video']] != job['direct_result_sha256']:
        proof = verified_opening(folder, job, final['files'][final['video']])
        if (proof['workflow_sha256'] != digest(folder/(phase+'-workflow.api.json'))
                or proof['raw_sha256'] != raw['sha256'] or proof['source_sha256'] != manifest['source_sha256']
                or proof['reference_sha256'] != [prep['files'][str(p)] for p in paths[1:-1]]):
            raise ValueError('Las entradas difieren de la verificación del inicio aprobado.')
        prep['recorded_visible_interval'] = proof['visible_interval']
    prep['recorded_raw'] = raw['path']
    return graph,paths,prep,job


async def process(service, job):
    from .direct_character import cpu
    from .guided_head import phase
    from .h3_face_refine import video_info
    jid=job['id']
    source_id=job['parameters']['recorded_source_id']
    graph,paths,prep,source=await thread_checkpoint(service,jid,recipe,service,source_id)
    if source['direct_result_sha256']!=job['parameters']['recorded_result_sha256']:
        raise ValueError('Cambió el resultado seleccionado.')
    folder=service.root/'jobs'/jid
    folder.mkdir(exist_ok=True,parents=True)
    g=deepcopy(graph)
    upload_record=folder/'repeat-inputs.json'
    if upload_record.is_file():
        uploaded=json.loads(upload_record.read_text('utf8'))
    else:
        uploaded=[]
        names = ['source.mp4','appearance.png', *(f'appearance-{i}.png' for i in range(2,len(paths)-1))]
        for path,name in zip(paths[:-1],names,strict=True):
            basename=f'gen-studio-repeat-{jid}-{name}'
            with path.open('rb') as stream:
                result=await service.post(service.settings['comfy_url'],'/upload/image',
                    files={'image':(basename,stream)},data={'overwrite':'false'})
            uploaded.append('/'.join(x for x in (result.get('subfolder',''),result['name']) if x))
        save(upload_record,uploaded)
    # New media names invalidate the sampler's dependency cache while using the same bytes.
    for name,path in zip(uploaded,paths[:-1],strict=True):
        subfolder,_,filename=name.rpartition('/')
        response=await service.client.get(service.settings['comfy_url']+'/view',
            params={'type':'input','subfolder':subfolder,'filename':filename})
        response.raise_for_status()
        if hashlib.sha256(response.content).hexdigest()!=prep['files'][str(path)]:
            raise ValueError('El motor no recibió exactamente la entrada archivada.')
    g['14']['inputs']['file'] = uploaded[0]
    for node, name in zip(reference_nodes(len(uploaded)-1), uploaded[1:], strict=True):
        g[node]['inputs']['image'] = name
    g['28']['inputs']['filename_prefix']=f'gen_studio/{jid}/repeat'
    shot=await phase(service,job,'recorded-direct',g,'28','.mp4',recorded=True)
    if shot is None:return
    history=json.loads((folder/'recorded-direct-history.json').read_text('utf8'))
    if executed_graph(history)!=g or any('24' in map(str,m[1].get('nodes',[]))
            for m in history['status']['messages'] if m[0]=='execution_cached'):
        raise ValueError('No se confirmó una ejecución nueva del sampler con la receta archivada.')
    p=source['parameters'];width,height=map(int,p['resolution'].split('x'))
    raw_info=await thread_checkpoint(service,jid,video_info,Path(shot['path']))
    source_folder=service.root/'jobs'/source_id
    expected=await thread_checkpoint(service,jid,video_info,local_path(source_folder,prep.pop('recorded_raw')))
    if raw_info!=expected:raise ValueError('El render nuevo difiere en duración o dimensiones del original.')
    save(folder/'direct-prepare.json',prep)
    inputs={'video':shot['path'],'video_sha256':shot['sha256'],
        'source':str(paths[-1]),'source_sha256':prep['files'][str(paths[-1])],'frames':p['frames'],
        'render_frames':raw_info['frames'],'width':width,'height':height}
    if 'recorded_visible_interval' in prep:
        inputs['visible_reference_interval'] = prep['recorded_visible_interval']
        inputs['opening_references'] = {str(path):prep['files'][str(path)] for path in paths[1:-1]}
    result=await cpu(service,job,'finalize',inputs)
    repair = {}
    if 'recorded_visible_interval' in prep:
        count = result.get('reference_opening_frames_repaired')
        if type(count) is not int or not 0 <= count <= 3 or result.get('reference_intrusion_frames') != []:
            raise ValueError('Una ficha requiere revisión dentro del intervalo visible de esta repetición.')
        repair['reference_opening_frames_repaired'] = count
    target=service.root/'results'/(jid+'.mp4')
    checksum=result['files'][result['video']]
    if not target.exists():
        part=folder/'repeat-result.part'
        shutil.copy2(result['video'],part)
        if digest(part)!=checksum:raise ValueError('La copia de salida está incompleta.')
        part.rename(target)
    if digest(target)!=checksum:raise ValueError('Cambió el resultado de la reproducción.')
    same_file=checksum==source['direct_result_sha256']
    stage='Repetición H3 · Archivo idéntico al original' if same_file else 'Repetición H3 lista para comparar'
    service.store.update(jid,status='completed',stage=stage,result_available=True,
        progress=None,direct_result_sha256=checksum,scene_resolution=p['resolution'],
        recorded_source_id=source_id,recorded_same_file=same_file,**repair)
