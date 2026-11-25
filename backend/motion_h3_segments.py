"""Experimental bounded H3 windows; originals and independent phase receipts survive."""
import json
import shutil
from pathlib import Path

from .head import digest
from .guided_composition import local_path
from .guided_head import phase
from .motion_h3_plan import plan_segments


def raw_manifest(service, source):
    from .motion_h3 import parameters_digest,reviewed_view
    from .motion_transfer import inputs
    path=service.root/'jobs'/source['id']/'motion-h3-segments.json'
    if not path.is_file() or digest(path)!=source.get('motion_h3_raw_sha256'):
        raise ValueError('Cambió el registro de los tramos H3 originales.')
    saved=json.loads(path.read_text(encoding='utf-8'))
    if saved.get('version')!=1 or saved['generation_parameters_sha256']!=parameters_digest(source):
        raise ValueError('Los parámetros de los tramos originales cambiaron.')
    if saved.get('views')!=source['parameters']['motion_h3_views']:
        raise ValueError('Las referencias de los tramos originales cambiaron.')
    if saved['cuts']!=source['parameters'].get('motion_h3_cut_frames',[]):
        raise ValueError('Los cortes del registro no corresponden a la generación.')
    if saved['files'].get(saved['prepared_video'])!=saved['prepared_sha256']:
        raise ValueError('Falta el hash del tramo fuente preparado.')
    ready_path=service.root/'jobs'/source['parameters']['motion_anchor_id']/'motion-prepare.json'
    ready=json.loads(ready_path.read_text(encoding='utf-8'))
    if ready['video']!=saved['prepared_video'] or ready['files'].get(ready['video'])!=saved['prepared_sha256']:
        raise ValueError('Cambió la preparación del tramo fuente.')
    inputs(service,source['parameters'])
    for view in saved['views']:
        reference,_=reviewed_view(service,view['job_id'],view['sha256'])
        inputs(service,reference['parameters'])
    expected=plan_segments(source['parameters']['frames'],saved['cuts'])
    if saved['plan']!=expected or len(saved['segments'])!=len(expected):
        raise ValueError('La cobertura de los tramos H3 cambió.')
    for segment,plan in zip(saved['segments'],expected):
        if any(segment.get(k)!=v for k,v in plan.items()):
            raise ValueError('Un tramo no corresponde a su intervalo original.')
    for filename,sha in saved['files'].items():
        file=local_path(service.root,filename)
        if not file.is_file() or digest(file)!=sha:
            raise ValueError('Cambió una entrada o un tramo H3 registrado.')
    # Require each raw in the protected file ledger, not just arbitrary extra entries.
    if any(saved['files'].get(s['path'])!=s['sha256'] for s in saved['segments']):
        raise ValueError('Falta el hash de una generación H3 original.')
    return {**saved,'path':str(path),'sha256':digest(path),'segmented':True}


def segment_prompt(p,segment,view_count,continued):
    text=p['motion_h3_prompt']
    start=segment['input_start']/24
    text+=f' This source excerpt starts at {start:.3f} seconds on the complete selected take. '
    text+='All reference times and additional timing directions above refer to that complete take; subtract the excerpt start to locate an action in this excerpt. '
    if continued:
        text+=f'<Picture {view_count+1}> is a generated continuity reference at the beginning of this excerpt. Maintain its appearance, scale and lighting; follow the source video for every pose and action, without freezing on this image. '
    return text


async def process(service,job,prepared,views):
    from .motion_transfer import cpu,upload,inputs
    from .motion_h3 import graph,raw_source,parameters_digest,reviewed_view
    p=job['parameters'];folder=service.root/'jobs'/job['id'];folder.mkdir(parents=True,exist_ok=True)
    if p.get('motion_h3_source_job'):
        parent=service.store.job(p['motion_h3_source_job'])
        _,raw=raw_source(service,parent)
        if not raw.get('segmented') or raw['sha256']!=p['motion_h3_raw_sha256']:
            raise ValueError('Cambió el registro de los tramos que se iban a ajustar.')
        service.store.update(job['id'],stage='Ajustando el tiempo de cada tramo en una copia · CPU')
    else:
        # A rapid turn can trigger scene detectors. Only explicit reviewed cuts
        # divide shots; fixed windows handle a continuous take by default.
        cuts={'cuts':p.get('motion_h3_cut_frames',[])}
        plan=plan_segments(p['frames'],cuts['cuts'])
        rendered=[];files={str(prepared):digest(prepared)}
        manifest_path=folder/'motion-h3-segments.json'
        if manifest_path.is_file():
            # Completion may have happened before the final join or a safe restart.
            source=service.store.job(job['id'])
            raw=raw_manifest(service,source)
        else:
            for segment in plan:
                index=segment['index'];tag=f'h3-window-{index:02}'
                current=service.store.job(job['id'])
                completed_window=(folder/(tag+'-complete.json')).is_file()
                if current.get('motion_h3_stop_after_window') and not completed_window and not (current.get('guided_phase')==tag and current.get('prompt_id')):
                    service.store.update(job['id'],status='cancelled',stage='Detenido entre tramos · Diagnóstico conservado',progress=None)
                    return
                service.store.update(job['id'],motion_h3_segment_progress={'value':index,'max':len(plan)},
                    stage=f'Preparando tramo H3 {index+1} de {len(plan)}')
                window=await cpu(service,job,'h3_window',{'video':str(prepared),'video_sha256':digest(prepared),
                    'segment':segment,'frames':p['frames'],'resolution':p['resolution']},checkpoint_name=tag)
                continued=bool(rendered and segment['shot']==rendered[-1]['shot'] and not p.get('motion_vocal_performance') and not p.get('original_reference') and p.get('motion_appearance_recipe') not in ('style_v2','style_v3'))
                audio=None
                if p.get('motion_vocal_performance'):
                    audio=await cpu(service,job,'h3_audio',{'video':str(prepared),'video_sha256':digest(prepared),
                        'segment':segment,'frames':p['frames'],'resolution':p['resolution']},checkpoint_name=tag+'-audio')
                    files.update(audio['files'])
                guide=None
                if continued:
                    previous=rendered[-1]
                    # The current excerpt begins inside the last context frames of the previous output.
                    frame_index=previous['context']+p['timing_offset_frames']+segment['input_start']-previous['output_start']
                    guide=await cpu(service,job,'h3_guide',{'video':previous['path'],'video_sha256':previous['sha256'],
                        'frame_index':frame_index,'frames':p['frames'],'resolution':p['resolution']},checkpoint_name=tag+'-guide')
                    files.update(guide['files'])
                names_path=folder/(tag+'-uploads.json')
                if names_path.is_file():names=json.loads(names_path.read_text(encoding='utf-8'))
                else:
                    names={'video':await upload(service,window['video'],job['id']),
                        'views':[await upload(service,path,job['id']) for _,path in views]}
                    if guide:names['views'].append(await upload(service,guide['frame'],job['id']))
                    if audio:names['audio']=await upload(service,audio['audio'],job['id'])
                    names_path.write_text(json.dumps(names,indent=2),encoding='utf-8')
                segment_p={**p,'motion_h3_prompt':segment_prompt(p,segment,len(views),continued)}
                if index==0 and p.get('motion_h3_reuse_first_job'):
                    from .motion_h3 import reusable_first_take
                    first,path=reusable_first_take(service,p,p['motion_h3_reuse_first_job'],p['motion_h3_reuse_first_sha256'])
                    target=folder/(tag+'.mp4')
                    if target.exists() and digest(target)!=p['motion_h3_reuse_first_sha256']:raise ValueError('Cambió la primera ventana reutilizada.')
                    if not target.exists():shutil.copyfile(path,target)
                    output={'path':str(target),'sha256':digest(target),'prompt_id':None,'reused_from_job':first['id']}
                    marker=folder/(tag+'-complete.json')
                    if marker.exists() and json.loads(marker.read_text())!=output:raise ValueError('Cambió el recibo de la ventana reutilizada.')
                    if not marker.exists():marker.write_text(json.dumps(output,indent=2),encoding='utf-8')
                else:
                    output=await phase(service,job,tag,graph(segment_p,names['video'],names['views'],job['id']+f'/segment-{index:02}',names.get('audio')),'16','.mp4')
                if output is None:return
                rendered.append({**segment,**output})
                files.update(window['files']);files[output['path']]=output['sha256']
            saved={'version':1,'cuts':cuts['cuts'],'plan':plan,'segments':rendered,'files':files,
                'views':p['motion_h3_views'],
                'prepared_video':str(prepared),'prepared_sha256':digest(prepared),
                'generation_parameters_sha256':parameters_digest(job),'continuity_reference':
                    'original photograph; no generated pose image' if p.get('original_reference') else 'reviewed prepared appearance in every window; no generated style feedback' if p.get('motion_appearance_recipe') in ('style_v2','style_v3') else 'reviewed appearance views; fixed source audio; no generated pose image' if p.get('motion_vocal_performance') else 'previous generated frame; not a temporal guarantee'}
            manifest_path.write_text(json.dumps(saved,indent=2),encoding='utf-8')
            service.store.update(job['id'],motion_h3_raw_sha256=digest(manifest_path))
            raw=raw_manifest(service,service.store.job(job['id']))
    if str(prepared)!=raw['prepared_video'] or digest(prepared)!=raw['prepared_sha256']:
        raise ValueError('Cambió la preparación del tramo fuente; se conservan los originales.')
    service.store.update(job['id'],stage='Uniendo los tramos H3 y conservando el audio fuente',motion_h3_raw_sha256=raw['sha256'])
    final=await cpu(service,job,'h3_join',{'segments':raw['segments'],'frames':p['frames'],'resolution':p['resolution'],
        'timing_offset_frames':p['timing_offset_frames'],'audio_source':raw['prepared_video'],'audio_source_sha256':raw['prepared_sha256']})
    inputs(service,p)
    for view in p['motion_h3_views']:reviewed_view(service,view['job_id'],view['sha256'])
    target=service.root/'results'/(job['id']+'.mp4');target.parent.mkdir(exist_ok=True)
    if target.exists() and digest(target)!=final['files'][final['video']]:raise ValueError('Existe otro resultado; se conserva.')
    if not target.exists():shutil.copy2(final['video'],target)
    service.store.update(job['id'],status='completed',result_available=True,progress=None,
        stage='Motion Transfer H3 unido · Revisa giro, tiempo y uniones',scene_resolution=p['resolution'],
        motion_result_sha256=digest(target),motion_audio=final['audio'],motion_h3_review_required=True,
        motion_h3_segment_progress={'value':len(raw['segments']),'max':len(raw['segments'])},
        motion_h3_segments={'count':len(raw['segments']),'cuts':raw['cuts'],'joins':final['joins'],'experimental':True},
        motion_timing={'offset_frames':p['timing_offset_frames'],'automatic_alignment':False,
            'tail_hold_frames':final['tail_hold_frames'],
            'source_padding_output_frames':final.get('total_source_padding_output_frames'),
            'scope':'manual per-window frame offset; no blend or spatial warp'})
