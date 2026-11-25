"""Audited Ref2VA character replacement without SAM, ControlNet or facial passes."""
import asyncio,json,os,shutil,subprocess
import httpx
from pathlib import Path
from typing import Literal
from uuid import uuid4
from uuid import UUID
from fastapi import HTTPException
from pydantic import BaseModel,Field
from .workflows import ROOT,check_graph
from .head import digest
from . import character_lora, direct_audio
from . import h3_swap_policy as swap
from .guided_head import phase
from .hands_recipe import apply as apply_hands, render_frames
from . import nsfw_adapters
from .video_sequences import record_rejected_preview

VENDOR=ROOT/'workflows/vendor/thebeast/MiniMax_H3_Character_Swap_LoRA.json'
VENDOR_SHA='0a4b22fd4e27cdbe76789d1c584b300ffa0b33c39d7ff5399a53f8f6951dec67'
TURBO='minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors'
SOURCE='https://github.com/TheBeast-Spec/minimax-h3-character-swap-lora-comfyui'

class Review(BaseModel):
    approved:bool
    feedback:str=Field(default='',max_length=1000)


def joint_reference_prompt(info, characters, count, direction):
    from .h3_visible_appearance import actor_entry, ensemble_direction
    description = (f'Recreate both performers together in <Video 1>. <Picture {count}> shows the approved cast '
                   'and costumes only. Keep the camera, body positions, gestures, expression changes and timing of this video. ')
    for side, index in enumerate(info['cast_layout']['order']):
        actor = info['actors'][index]
        reviewed = actor_entry(info, actor)
        name = reviewed['name'] if reviewed else characters[index]['name']
        description += f"The {'left' if side == 0 else 'right'} performer is {name} from <Picture {actor['face_picture']+1}>"
        if reviewed:
            description += ". " + reviewed['description'] + " "
        else:
            description += " wearing " + actor['appearance']['clothing'] + ". "
    return description, description + ensemble_direction(info, direction)


def rejected_segment(service,job,final,review,message):
    clip=review.get('corrected') or {'path':final['video'],'sha256':final['files'][final['video']]}
    record_rejected_preview(service,job,clip)
    return ValueError(message)


def graph(payload,video_name,references,job_id,audio=False):
    if payload.get('direct_preset')=='fasth3_omni':
        from .fasth3_omni import graph as omni_graph
        return omni_graph(payload,video_name,references,job_id,audio)
    character_version=payload.get('direct_character_version','v1')
    if payload.get('direct_preset') in ('realism','hands'):
        from .character_update import graph as updated, NATIVE_ENCODER, OFFICIAL_REALISM
        width,height=map(int,payload['resolution'].split('x'))
        result=updated(video_name,references,prompt=payload['prompt'],width=width,height=height,
            frames=render_frames(payload['frames']) if payload.get('direct_preset')=='hands' else payload['frames'],seed=payload['seed'],encoder=NATIVE_ENCODER,realism=OFFICIAL_REALISM,
            attention_mode='native',resize_method='lanczos',character_version=character_version)
        result['28']['inputs']['filename_prefix']=f'gen_studio/{job_id}/character-realism'
        if payload.get('direct_preset')=='hands':apply_hands(result,character_version)
        from .vsa_recipe import apply as apply_vsa
        from .motion_identity import adapt
        from .motion_scene import apply_guide as scene_guide
        return direct_audio.apply(scene_guide(adapt(apply_vsa(result,payload),payload),payload,head_video='head-reference-preflight.mp4' if payload.get('motion_head_guide') else None),payload)
    if digest(VENDOR)!=VENDOR_SHA:raise ValueError('La copia del workflow directo cambió; no se ejecuta.')
    if not 1<=len(references)<=9:raise ValueError('H3 directo requiere de 1 a 9 imágenes ordenadas.')
    vendor={n['id']:n for n in json.loads(VENDOR.read_text(encoding='utf-8'))['nodes']}
    def node(kind,**inputs):return {'class_type':kind,'inputs':inputs}
    width,height=map(int,payload['resolution'].split('x'))
    result={
      '1':node('UNETLoader',unet_name=vendor[127]['widgets_values'][0],weight_dtype='default'),
      '2':node('LoraLoaderModelOnly',model=['1',0],lora_name=character_lora.filename(character_version),strength_model=payload['lora_strength']),
      '3':node('CLIPLoader',clip_name=vendor[128]['widgets_values'][0],type='minimax',device='default'),
      '4':node('VAELoader',vae_name=vendor[119]['widgets_values'][0]),
      '5':node('VAELoader',vae_name=vendor[120]['widgets_values'][0]),
      '7':node('LoadVideo',file=video_name),
      '17':node('GetVideoComponents',video=['7',0]),
      '8':node('MiniMaxH3ReferenceToVideo',clip=['3',0],vae=['4',0],audio_vae=['5',0],
          prompt=payload['prompt'],width=width,height=height,length=payload['frames'],ref_image_size='max',
          **{'ref_videos.ref_video_0':['17',0]}),
      '9':node('BasicGuider',model=['2',0],conditioning=['8',0]),
      '10':node('BasicScheduler',model=['2',0],scheduler=vendor[124]['widgets_values'][0],steps=payload['steps'],denoise=1),
      '11':node('KSamplerSelect',sampler_name=vendor[123]['widgets_values'][0]),
      '12':node('RandomNoise',noise_seed=payload['seed']),
      '13':node('SamplerCustomAdvanced',noise=['12',0],guider=['9',0],sampler=['11',0],sigmas=['10',0],latent_image=['8',1]),
      '14':node('VAEDecode',samples=['13',0],vae=['4',0]),
      '18':node('VAEDecodeAudio',samples=['13',0],vae=['5',0]),
      '15':node('CreateVideo',images=['14',0],audio=['18',0],fps=24),
      '16':node('SaveVideo',video=['15',0],filename_prefix=f'gen_studio/{job_id}/h3-direct',format='mp4',codec='h264')}
    if audio:result['8']['inputs']['ref_video_audios.ref_video_audio_0']=['17',1]
    else:
        result['15']['inputs'].pop('audio');result.pop('18')
    for i,name in enumerate(references):
        key=str(30+i);result[key]=node('LoadImage',image=name)
        result['8']['inputs'][f'ref_images.ref_image_{i}']=[key,0]
    if payload.get('direct_preset')=='turbo8':
        result['29']=node('LoraLoaderModelOnly',model=['1',0],lora_name=TURBO,strength_model=1)
        result['2']['inputs']['model']=['29',0]
    return direct_audio.apply(result,payload)


def description():
    from .prompt_language import VERSION as language_policy
    return {'id':'h3-direct','name':'H3 directo · Referencias múltiples','source':SOURCE,
      'sha256':digest(VENDOR),'integrity_ok':digest(VENDOR)==VENDOR_SHA,
      'prompt_language':'en','prompt_language_policy':language_policy,
      'no_sam':True,'no_controlnet':True,'no_face_postprocessing':True,'maximum_references':9,
      'video_only_supported':True,'video_only_preserves_final_audio':True,
      'defaults':{'resolution':'1344x768','frames':90,'steps':20,'seed':314159,'lora_strength':1,'direct_preset':'base'},
      'turbo_model':TURBO,'original_workflow_commit':'5df268280a8cb74fdcc94a68866798db689025e7',
      'adaptation':'Set/Get tags become direct links. Clip is normalized to 24 fps and bounded to requested frames. Original source audio is retained. Base mode bypasses the absent Turbo and uses 20 steps; turbo8 uses the author settings.'}


def register(app,settings):
    @app.get('/api/workflows/h3-direct')
    async def info():return description()

    @app.get('/api/workflows/h3-direct/preflight')
    async def preflight(character_version:Literal['v1','v1.5']='v1'):
        intel_configured=bool(settings.get("qwen_director_enabled") and settings.get("h3_intel_planner"))
        from .h3_intel_planner import MULTITAKE_PROTOCOL, multitake_available
        profile = settings.get("h3_intel_planner") or {}
        if intel_configured and profile.get("protocol") == MULTITAKE_PROTOCOL:
            intel_configured = await multitake_available(profile)
        from . import h3_intel_vision
        visual_ready = bool(profile and settings.get('qwen_director_enabled') and await h3_intel_vision.available(profile))
        intel_status = {'intel_location_ready': visual_ready,'intel_planner_enabled': bool(profile and settings.get('qwen_director_enabled')),
                        'intel_planner_configured': intel_configured,
                        'intel_parallel_worker_active': bool(settings.get('h3_parallel_planning')
                            and app.state.jobs.current.intel_worker and not app.state.jobs.current.intel_worker.done())}
        results={}
        try:
            from .fasth3_omni import graph as omni_graph
            og=omni_graph({'resolution':'864x480','frames':124,'steps':8,'seed':42,'prompt':'Replace in <Video 1> using <Picture 1>.'},'preflight.mp4',['preflight.png'],'preflight')
            results['fasth3_omni']=await check_graph(app.state.client,settings['comfy_url'],og,skip_inputs={('7','file')})
        except (ValueError,OSError,httpx.HTTPError) as error:
            results['fasth3_omni']={'ok':False,'errors':[str(error)]}
        try:
            character_lora.verify(character_version)
        except (ValueError,OSError) as error:
            return {**description(),**intel_status,'character_version':character_version,
                    'presets':{**{preset:{'ok':False,'errors':[str(error)]} for preset in ('base','turbo8','realism','hands')},**results}}
        try:
            for preset,steps in [('base',20),('turbo8',8)]:
                g=graph({'resolution':'1344x768','frames':90,'steps':steps,'seed':42,'lora_strength':1,'prompt':'Replace in <Video 1> using <Picture 1>.','direct_preset':preset,'direct_character_version':character_version},'preflight.mp4',['preflight.png'],'preflight')
                results[preset]=await check_graph(app.state.client,settings['comfy_url'],g,skip_inputs={('7','file')})
        except httpx.HTTPError:
            return {**description(),**intel_status,'character_version':character_version,'presets':{preset:{'ok':False,
                'errors':['El motor de vídeo no responde. Puedes recuperarlo con Reiniciar Gen Studio.']} for preset in ('base','turbo8','realism','hands')}}
        continuity_check={'ok':False,'errors':['La guía de continuidad no está disponible en este motor.']}
        try:
            from .character_update import native_candidate
            response=await app.state.client.get(settings['comfy_url']+'/object_info');response.raise_for_status()
            g=native_candidate(response.json(),'preflight.mp4','preflight.png',duration=107/24,
                frames=107,prompt='Replace in <Video 1> using <Picture 1>.',width=768,height=1344,character_version=character_version)
            results['hands']=await check_graph(app.state.client,settings['comfy_url'],apply_hands(json.loads(json.dumps(g)),character_version),skip_inputs={('14','file')})
            results['realism']=await check_graph(app.state.client,settings['comfy_url'],g,skip_inputs={('14','file')})
            if character_version=='v1.5':
                from .continuity import apply_guide
                continuity_check=await check_graph(app.state.client,settings['comfy_url'],
                    apply_guide(apply_hands(json.loads(json.dumps(g)),character_version),'continuity-preflight.png'),
                    skip_inputs={('14','file'),('continuity-image','image')})
        except (ValueError,OSError,httpx.HTTPError) as error:
            results['realism']={'ok':False,'errors':[str(error)]}
            results['hands']={'ok':False,'errors':[str(error)]}
        vsa={'ok':False,'errors':['VSA requiere Realismo Manos v1.5.']}
        if character_version=='v1.5' and results['hands']['ok']:
            try:
                from .vsa_recipe import apply as apply_vsa,verify_assets
                vg=apply_vsa(apply_hands(json.loads(json.dumps(g)),character_version),{'direct_vsa':True,'direct_preset':'hands','direct_character_version':character_version})
                vsa=await check_graph(app.state.client,settings['comfy_url'],vg,skip_inputs={('14','file')})
                vsa['verification']=verify_assets()
            except (ValueError,OSError,KeyError) as error:vsa={'ok':False,'errors':[str(error)]}
        return {**description(),**intel_status,'character_version':character_version,'presets':results,'vsa':vsa,'continuity':continuity_check}

    @app.post('/api/jobs/{job_id}/review-direct')
    async def review(job_id:UUID,body:Review):
        service=app.state.jobs
        async with service.lock:
            job=service.store.job(str(job_id))
            if not job:raise HTTPException(404,'Trabajo no encontrado.')
            if job['parameters'].get('engine')!='h3_direct' or job['parameters'].get('operation') or job['status']!='completed':
                raise HTTPException(409,'Selecciona una toma directa terminada; el vídeo largo tiene su propia revisión.')
            path=service.root/'results'/(job['id']+'.mp4')
            if not path.is_file() or digest(path)!=job.get('direct_result_sha256'):
                raise HTTPException(409,'La salida cambió; no se registra su aprobación.')
            approved_hash = {'approved_result_sha256':job['direct_result_sha256']} if body.approved else {}
            return service.store.update(job['id'],direct_review={'identity_approved':body.approved,'review_required':not body.approved,
                'human_review':'approved' if body.approved else 'rejected','human_feedback':body.feedback,**approved_hash},
                stage='H3 directo revisado por el usuario' if body.approved else 'H3 directo · Incidencia registrada')


async def cpu(service,job,mode,inputs,*,label=None):
    folder=service.root/'jobs'/job['id'];name=label or mode;checkpoint=folder/(f'direct-{name}.json')
    if checkpoint.exists():
        report=json.loads(checkpoint.read_text())
        for path,sha in report['files'].items():
            if digest(Path(path))!=sha:raise ValueError('Una entrada o salida preparada cambió; crea otra copia.')
        return report
    if mode == 'finalize':
        if job.get('reference_opening_guard')==1 and 'opening_references' not in inputs:
            prep=json.loads((folder/'direct-prepare.json').read_text(encoding='utf8'))
            sheets={path:prep['files'][path] for path in prep['references']}
            inputs={**inputs,'opening_references':sheets}
            if swap.applies(job):
                params = job['parameters']
                start = params.get('sequence_context', 0)
                inputs['visible_reference_interval'] = [start, start + params.get('sequence_keep', inputs['frames']-start)]
        service.store.update(job['id'],stage='Comprobando el vídeo y conservando el audio original',progress=None)
    run=folder/(f'direct-{name}-'+str(uuid4()));run.mkdir()
    manifest={'mode':mode,'run_root':str(run),'parent_pid':os.getpid(),'model_root':service.settings['face_models'],**inputs}
    path=run/'manifest.json';path.write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    process=None
    with (run/'stderr.log').open('wb') as errors:
        try:
            process=await asyncio.create_subprocess_exec(service.settings['face_python'],'-u',str(ROOT/'scripts/prepare_direct_character.py'),str(path),
                stdout=asyncio.subprocess.PIPE,stderr=errors,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),
                env={**os.environ,'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','PYTHONIOENCODING':'utf-8'})
            while line:=await process.stdout.readline():
                try:
                    event=json.loads(line.decode('utf-8'))
                    if event.get('type')=='progress':service.store.update(job['id'],stage=event['stage'])
                except (ValueError,UnicodeDecodeError):pass
            if await process.wait():
                error=run/'error.json';raise ValueError(json.loads(error.read_text())['error'] if error.exists() else 'Falló la preparación del workflow directo.')
        finally:
            if process and process.returncode is None:
                if job.get('qwen_director_version') or job['parameters'].get('engine') in ('h3_alisson','h3_mdmz','h3_secourses') or job['parameters'].get('hybrid_parent_id') or job['parameters'].get('operation')=='omni_background':
                    from .process_cleanup import stop_process_tree
                    try:await stop_process_tree(process)
                    except Exception as error:
                        service.store.update(job['id'],cancel_local_cleanup_error=str(error))
                        raise
                else:
                    process.terminate()
                    try:await asyncio.wait_for(process.wait(),5)
                    except TimeoutError:process.kill();await process.wait()
    report=json.loads((run/'report.json').read_text());checkpoint.write_text(json.dumps(report,indent=2),encoding='utf-8');return report


async def process(service,job):
    p=job['parameters'];folder=service.root/'jobs'/job['id'];folder.mkdir(parents=True,exist_ok=True)
    current=service.store.job(job['id'])
    if current.get('status')=='submission_unknown':return
    resumed=(folder/'direct-complete.json').exists() or (current.get('guided_phase')=='direct' and current.get('prompt_id'))
    saved_graph=None
    if resumed:
        saved_graph=json.loads((folder/'direct-workflow.api.json').read_text(encoding='utf-8'))
    character_version=p.get('direct_character_version','v1')
    if not resumed and p.get('direct_preset')!='fasth3_omni':character_lora.verify(character_version)
    if not resumed and p.get('omni_experiment')=='character_v15':character_lora.verify('v1.5')
    source=service.root/'inputs'/service.store.asset(p['video_id'])['filename']
    references=[service.root/'inputs'/service.store.asset(i)['filename'] for i in p['direct_reference_ids']]
    protected={str(source):p['direct_source_sha256'],**{str(path):sha for path,sha in zip(references,p['direct_reference_hashes'])}}
    for path,sha in protected.items():
        if digest(Path(path))!=sha:raise ValueError('Una referencia del trabajo directo cambió; no se ejecuta.')
    from .motion_scene import head_reference
    head_motion=head_reference(service,p)
    if head_motion:protected[str(head_motion)]=p['motion_head_guide']['sha256']
    from .continuity import validate_guide,apply_guide
    continuity_image=validate_guide(service,job)
    if continuity_image:protected[str(continuity_image)]=p['direct_continuity_guide']['sha256']
    if not resumed:
        from .prompt_language import for_job
        p={**p,'prompt':await for_job(service,current)}
    width,height=map(int,p['resolution'].split('x'))
    hands=p.get('direct_preset')=='hands'
    model_frames=render_frames(p['frames']) if hands or p.get('direct_preset')=='fasth3_omni' or p.get('hybrid_parent_id') else p['frames']
    prepared=await cpu(service,job,'prepare',{'source':str(source),'references':list(map(str,references)),
        'protected':protected,'width':width,'height':height,'frames':p['frames'],'render_frames':model_frames,'start_frame':round(p.get('direct_start_seconds',0)*24),
        'crop_face':False if hands or p.get('direct_preset')=='fasth3_omni' else p.get('direct_crop_face',True),'reference_type':p.get('direct_reference_type','auto'),
        'scene_cut_policy':'warn' if p.get('direct_preset') in ('hands','realism') else 'strict',
        'video_only':bool(p.get('direct_video_only'))})
    from . import h3_face_fidelity as fidelity
    if (not resumed and swap.applies(job) and not prepared.get('suspected_scene_cut_seconds')
            and not current.get('guided_phase') and not (folder/'direct-workflow.api.json').exists()):
        prior = swap.tail_inputs(service,job)
        if prior and not fidelity.explicit_feedback(fidelity.director.records.read(service.store,job['id']) or {}):
            from .job_cancel import thread_checkpoint
            from .video_sequences import file_checked
            service.store.update(job['id'],stage='Comprobando la oclusión y el movimiento dentro del encuadre')
            tail = await cpu(service,job,'occlusion-tail',{
                **prior,'source':prepared['video'],'source_sha256':prepared['files'][prepared['video']],
                'frames':p['frames'],'width':width,'height':height,
                'reference_sheets':{name:prepared['files'][name] for name in prepared['references']}})
            if (tail['applicable'] and tail['files'].get(prior['previous']) == prior['previous_sha256']
                    and await swap.verify_tail(service,job,tail) and swap.tail_inputs(service,job) == prior):
                for input_path,expected in {**protected,prior['previous']:prior['previous_sha256']}.items():
                    await thread_checkpoint(service,job['id'],file_checked,service,input_path,expected)
                path = tail['video']
                sha = tail['files'][path]
                target = service.root/'results'/(job['id']+'.mp4')
                target.parent.mkdir(exist_ok=True)
                if not target.exists():
                    part = folder/'occlusion-result.part'
                    await thread_checkpoint(service,job['id'],shutil.copyfile,path,part)
                    await thread_checkpoint(service,job['id'],file_checked,service,part,sha)
                    part.replace(target)
                await thread_checkpoint(service,job['id'],file_checked,service,target,sha)
                return service.store.update(job['id'],status='completed',result_available=True,progress=None,
                    stage='Continuación con oclusión del original · Revisar el cierre',scene_resolution=p['resolution'],
                    direct_swap_strategy='source_occlusion',direct_result_sha256=sha,
                    direct_fidelity_review={'status':'review','checks':{'occlusion':'pass'},
                        'notes':['Qwen confirmó la oclusión en tres instantes. El cierre queda disponible para revisión.']},
                    direct_review={'identity_approved':False,'review_required':True})
    checked=fidelity.enabled(job)
    face_info=await fidelity.prepare(service,job,prepared) if checked else None
    from . import h3_cast_wardrobe as costume
    verify_outfits=job.get(costume.FIELD)==costume.VERSION
    if verify_outfits and not ((face_info or {}).get('confirmed_cast') and face_info.get('integral')
                               and all(a.get('upper_outfit') for a in face_info['actors'])):
        raise ValueError('No se pudo vincular el vestuario de referencia a las dos personas marcadas.')
    whole_frame_cast=job.get('direct_joint_cast')=='whole-frame-cast-1' and len(p.get('direct_characters',[]))>1
    if whole_frame_cast and face_info:
        face_info['whole_frame_cast']=True
        face_info['skip_mouth_correction']=True
        from .h3_close_cast import eligible
        face_info['close_cast']=eligible(job,face_info)
    joint_cast=bool(job.get('direct_joint_cast') and (face_info or {}).get('cast_layout'))
    h3_video,h3_audio=direct_audio.prepared_input(p,prepared)
    primary_sheet=bool((face_info or {}).get('primary_sheet'))
    whole_identity=primary_sheet or p.get('whole_shot_identity')==1
    if whole_identity and not (checked and face_info and len(face_info['actors'])==1):
        raise ValueError('Falta la asignación de identidad para regenerar el plano completo.')
    target_band=None if whole_identity else (face_info or {}).get('target_band')
    spatial_options={'target_band':target_band} if target_band else {}
    if target_band:
        isolated=await cpu(service,job,'marked-input',{'source':h3_video,'source_sha256':prepared['files'][h3_video],
            'band':target_band,'width':width,'height':height,'frames':model_frames})
        h3_video=isolated['video']
    if not service.store.job(job['id']).get('direct_reference_preparation'):
        service.store.update(job['id'],direct_reference_preparation={'type':prepared.get('reference_type','auto'),
            'human_crop_applied':bool(prepared.get('face_crop_box')),'notes':prepared.get('reference_notes',[]),
            'suspected_scene_cut_seconds':prepared.get('suspected_scene_cut_seconds',[])})
        for note in prepared.get('reference_notes',[]):service.event(job['id'],note)
    uploads=folder/'direct-uploads.json'
    if uploads.exists():names=json.loads(uploads.read_text())
    else:
        with Path(h3_video).open('rb') as stream:
            uploaded=await service.post(service.settings['comfy_url'],'/upload/image',files={'image':(f'gen-studio-direct-{job["id"]}.mp4',stream)},data={'overwrite':'false'})
        names={'video':uploaded['name'],'references':[]}
        for i,path in enumerate(prepared['references']):
            with Path(path).open('rb') as stream:
                image=await service.post(service.settings['comfy_url'],'/upload/image',files={'image':(f'picture-{i+1}.png',stream)},
                    data={'overwrite':'false','subfolder':f'gen_studio/{job["id"]}/direct'})
            names['references'].append('/'.join(x for x in [image.get('subfolder',''),image['name']] if x))
        if continuity_image:
            with continuity_image.open('rb') as stream:
                image=await service.post(service.settings['comfy_url'],'/upload/image',
                    files={'image':('continuity.png',stream)},data={'overwrite':'false','subfolder':f'gen_studio/{job["id"]}/direct'})
            names['continuity']='/'.join(x for x in [image.get('subfolder',''),image['name']] if x)
        if head_motion:
            with head_motion.open('rb') as stream:
                extra=await service.post(service.settings['comfy_url'],'/upload/image',files={'image':('head-motion.mp4',stream)},data={'overwrite':'false','subfolder':f'gen_studio/{job["id"]}/head'})
            names['head_motion']='/'.join(x for x in [extra.get('subfolder',''),extra['name']] if x)
        uploads.write_text(json.dumps(names,indent=2),encoding='utf-8')
    if head_motion and not names.get('head_motion'):raise ValueError('La guía de cabeza no está en las entradas enviadas al motor.')
    if continuity_image and not names.get('continuity'):raise ValueError('La guía no está incluida en las entradas enviadas al motor.')
    cast_image=p.get('sequence_ensemble_reference')
    from . import h3_sequence_ensemble as ensemble_policy
    automatic=(not cast_image and whole_frame_cast and checked and p.get('sequence_parent_id')
               and p.get('direct_preset')=='hands' and len(names['references'])<9
               and job.get('direct_sequence_ensemble') in (ensemble_policy.LEGACY, ensemble_policy.POLICY)
               and ensemble_policy.compatible_target(face_info, manual_closeup=swap.applies(job) or p.get('manual_closeup_reference')==1))
    if automatic:
        from .job_cancel import thread_checkpoint
        cast_image=await thread_checkpoint(service,job['id'],ensemble_policy.choose,service,job,face_info)
        if cast_image:
            service.store.update(job['id'],direct_ensemble_source={'job':cast_image['source_job'],
                'segment':cast_image['source_index']+1,'frame':cast_image['frame']})
    if cast_image:
        if not automatic and not (whole_frame_cast and checked and face_info.get('body_track') and face_info.get('confirmed_cast')):
            raise ValueError('La referencia conjunta requiere dos personajes confirmados y seguimiento del perfil oculto.')
        appearance=await fidelity.upload(service,job,fidelity.path_for(service,cast_image))
        names={**names,'references':[*names['references'],appearance]}
    if primary_sheet:
        names={**names,'references':names['references'][:1]}
    elif whole_identity:
        solo_actor=face_info['actors'][0]
        reference=Path(prepared['references'][solo_actor['face_picture']])
        portrait=await fidelity.cpu(service,job,'whole-shot-portrait',{'mode':'cast-reference-portrait',
            'path':str(reference),'path_sha256':digest(reference),'indices':[],'size':list(face_info['size'])})
        if portrait['crops'] and len(names['references'])<9:
            closeup=await fidelity.upload(service,job,fidelity.path_for(service,portrait['crops'][0]))
            names={**names,'references':[*names['references'],closeup]}
        else:
            closeup=None
    if swap.applies(job):
        if cast_image: strategy='approved_cast_reference'
        elif primary_sheet: strategy='primary_identity_sheet'
        elif (face_info or {}).get('temporal_cast'): strategy='time_specific_cast'
        elif (face_info or {}).get('body_track'): strategy='occluded_profile'
        elif whole_frame_cast: strategy='joint_full_frame'
        else: strategy='source_performance'
        service.store.update(job['id'],direct_swap_strategy=strategy)
    card_retry_direction = ''
    def condition_cast(graph):
        graph=fidelity.apply_identity(graph,face_info)
        if whole_identity and not primary_sheet:
            card=p['direct_characters'][0]
            encoder=next(n['inputs'] for n in graph.values() if n['class_type']=='MiniMaxH3ReferenceToVideo')
            identity=len(names['references']) if closeup else solo_actor['face_picture']+1
            profile=next((i for i,v in enumerate(card['views'],1) if v=='profile'),None)
            direction=(f" Recreate the whole shot with {card['name']} throughout. <Picture {identity}> supplies "
                       "facial identity across expressions, including brows, nose, cheek volume and jaw. ")
            if profile:
                direction+=f"Use <Picture {profile}> for nose and jaw shape as the head turns. "
            direction+=(f"<Picture {solo_actor['appearance_picture']+1}> supplies the outfit and accessories. "
                        "Perform the original eye, mouth, head and body movements with this replacement anatomy. "
                        "Maintain the camera, lighting and background in <Video 1>. ")
            encoder['prompt']=encoder['prompt'].replace('r34l1sm , ultra realistic video. ',
                'r34l1sm , ultra realistic video. '+direction,1)
        if cast_image:
            encoder=next(n['inputs'] for n in graph.values() if n['class_type']=='MiniMaxH3ReferenceToVideo')
            encoder['prompt']+=(
                f" Recreate both assigned performers together in <Video 1>. <Picture {len(names['references'])}> "
                "shows the approved cast together for identity and costume only. Keep the camera distance, "
                "orientation, occlusions, speaking and reactions from this video. Show only the parts of each "
                "face and outfit visible at this angle. Use the individual appearance references for hair and facial structure.")
            if automatic and job.get('direct_sequence_ensemble')==ensemble_policy.POLICY and not face_info.get('feedback'):
                order=ensemble_policy.screen_order(face_info,p['direct_characters'])
                directions=[]
                for card,actor in zip(p['direct_characters'],face_info['actors']):
                    side='left' if card['id']==order[0] else 'right'
                    directions.append(f"On the {side}, replace the marked performer with {card['name']} from "
                                      f"<Picture {actor['face_picture']+1}>. Recreate the visible outfit, hair and worn accessories "
                                      f"from the first frame, including in rear view. {actor['appearance']['accessories']} "
                                      f"{actor['appearance']['clothing']}")
                encoder['prompt']=("r34l1sm , ultra realistic video. Recreate both performers together in <Video 1>. "
                                   f"<Picture {len(names['references'])}> shows both assigned performers in the scene for appearance only. "
                                   "Keep the original shot, scale, orientation, expressions, visible mouth movement and timing. "
                                   "Keep hidden faces hidden. Dress only the parts inside the camera crop. "
                                   +" ".join(directions)+" "+" ".join(face_info.get('retry_corrections',[])))
                if p.get('manual_closeup_reference') and face_info.get('close_cast'):
                    encoder['prompt']+=(' Keep the facial structure from each individual portrait consistent throughout this close shot. '
                                        'Use the approved scene still to match their appearance in film lighting. '
                                        'Take gaze, mouth movement and expression only from the current source video.')
                if p.get('direct_global_goal'):
                    encoder['prompt']+=' '+p['direct_global_goal']
        direction = (p.get('direct_direction') or '').strip()
        if swap.applies(job):
            additions = [direction, p.get('direct_global_goal'), card_retry_direction]
            if cast_image and automatic and not face_info.get('feedback'):
                for card, actor in zip(p['direct_characters'], face_info['actors'], strict=True):
                    moments = actor.get('source_actions') or []
                    if not moments:
                        continue
                    for i in sorted({0, len(moments)//2, len(moments)-1}):
                        m = moments[i]
                        additions.append(f"{card['name']}, source frame {m['frame']}: {m['body_limbs']} "
                                         f"{m['contacts']} {m['head_gaze_face']} {m['uncertainty']}")
            swap.add_directions(graph, additions)
        elif direction and job.get('direct_joint_cast') and not resumed:
            encoder = next(n['inputs'] for n in graph.values() if n['class_type'] == 'MiniMaxH3ReferenceToVideo')
            if direction not in encoder['prompt']:
                encoder['prompt'] = encoder['prompt'].replace('r34l1sm , ultra realistic video. ',
                    'r34l1sm , ultra realistic video. ' + direction + ' ', 1) if encoder['prompt'].startswith('r34l1sm , ultra realistic video. ') else direction + ' ' + encoder['prompt']
        return graph
    if resumed:
        g=saved_graph
    elif p.get('direct_preset') in ('realism','hands'):
        from .character_update import native_candidate
        response=await service.client.get(service.settings['comfy_url']+'/object_info');response.raise_for_status()
        g=native_candidate(response.json(),names['video'],names['references'],duration=model_frames/24,
            frames=model_frames,prompt=p['prompt'],width=width,height=height,seed=p['seed'],character_version=character_version)
        if hands:apply_hands(g,character_version)
        from .vsa_recipe import apply as apply_vsa
        apply_vsa(g,p)
        g['28']['inputs']['filename_prefix']=f'gen_studio/{job["id"]}/character-realism'
    else:
        g=graph({**p,'frames':model_frames},names['video'],names['references'],job['id'],h3_audio)
    if not resumed:
        from .motion_identity import adapt
        adapt(g,p)
        from .motion_scene import apply_guide as scene_guide
        scene_guide(g,p,head_video=names.get('head_motion'))
    if continuity_image and not resumed:apply_guide(g,names['continuity'])
    if resumed and head_motion and 'scene-head-video' not in g:raise ValueError('El envío aceptado no conserva la guía de cabeza.')
    if resumed and p.get('motion_scene_recipe') and 'scene-guide' not in g:raise ValueError('El envío aceptado no conserva la guía de escena.')
    if continuity_image and resumed and 'continuity-guide' not in g:raise ValueError('El envío aceptado no conserva la guía de continuidad.')
    if not resumed:direct_audio.apply(g,p)
    elif p.get('direct_video_only'):direct_audio.validate(g)
    if p.get('direct_video_only'):
        service.store.update(job['id'],direct_audio_input={'mode':'video_only','source_audio_sent':False,
            'h3_video_sha256':isolated['files'][h3_video] if target_band else prepared['files'][h3_video],'original_audio_for_final':bool(prepared['audio'])})
    if p.get('direct_vsa') and not resumed:
        from .vsa_recipe import verify_assets
        verification=verify_assets()
        (folder/'vsa-verification.json').write_text(json.dumps(verification,indent=2),encoding='utf-8')
        service.store.update(job['id'],direct_vsa_verification=verification)
    if service.store.job(job['id']).get('direct_prompt_language'):
        effective=[n['inputs']['prompt'] for n in g.values() if n['class_type']=='MiniMaxH3ReferenceToVideo']
        if len(effective)!=1:raise ValueError('No se pudo registrar el prompt efectivo de H3.')
        service.store.update(job['id'],direct_effective_prompt=effective[0])
    if not resumed:
        g=nsfw_adapters.apply_optional_adapters(g,p)
    if checked and not resumed:
        anchors=[] if whole_identity or cast_image or target_band or ((face_info or {}).get('cast_layout') and not joint_cast) else await fidelity.guides(service,job,face_info)
        if anchors is None:return
        g=condition_cast(g)
        g=fidelity.apply(g,anchors)
        service.store.update(job['id'],direct_fidelity_guide_frames=[item['frame'] for item in anchors])
    if (face_info or {}).get('cast_layout') and not joint_cast:
        from .h3_cast_render import render as render_cast
        rendered=await render_cast(service,job,p,face_info,prepared,names,model_frames)
    else:
        rendered=await phase(service,job,'direct',g,'28' if p.get('direct_preset') in ('realism','hands') else '16','.mp4')
    if rendered is None:return
    final=await cpu(service,job,'finalize',{'video':rendered['path'],'video_sha256':rendered['sha256'],
        'source':prepared['video'],'source_sha256':prepared['files'][prepared['video']], 'frames':p['frames'],'render_frames':model_frames,'width':width,'height':height,**spatial_options})
    def offer_segment(candidate, evaluation):
        for path, sha in protected.items():
            if digest(Path(path))!=sha:raise ValueError('Una referencia cambió durante la generación.')
        clip={'path':candidate['video'],'sha256':candidate['files'][candidate['video']]}
        record_rejected_preview(service,job,clip)
        service.store.update(job['id'],status='cancelled',stage='Tramo generado · Pendiente de tu revisión',
            human_segment_review=True,direct_fidelity_review=evaluation,progress=None)
    review=None
    if checked:
        review=await fidelity.review(service,job,face_info,final,'face-first')
        if p.get('sequence_parent_id'):
            offer_segment(final,review)
            return
        from .h3_marked_target import CAST_VERSION,cast_failures as strict_cast_failures
        from .h3_cast_render import mouth_only_notice
        def cast_failures(evaluation,count):
            failures=strict_cast_failures(evaluation,count,require_wardrobe=verify_outfits)
            if swap.applies(job):
                for actor_number in (row['actor'] for row in evaluation.get('actors', [])):
                    performance = swap.performance_failures(evaluation, actor_number)
                    if performance:
                        reasons=failures.setdefault(actor_number, [])
                        reasons.extend(k for k in performance if k not in reasons)
            if joint_cast and job.get('direct_mouth_advisory') and mouth_only_notice(evaluation,failures):
                return {}
            return failures
        cast_count=len(p.get('direct_characters',[])) if (face_info or {}).get('confirmed_cast') and job.get('direct_marked_cast_policy')==CAST_VERSION else 0
        candidate_review=None
        first_review=review
        def joint_issues(evaluation):
            if not joint_cast:
                return []
            from .h3_cast_render import joint_issues as check_joint
            return check_joint(evaluation)
        first_failures=cast_failures(review,cast_count)
        original_joint_issues=joint_issues(review)
        if swap.applies(job) and not original_joint_issues and swap.needs_source_review(review,first_failures):
            raise rejected_segment(service,job,final,review,'El vestuario no es comprobable en la fuente marcada. '
                                   'Revisa el tramo generado o ajusta la marca; se conserva el vídeo para tu decisión.')
        mouth_notice=(joint_cast and job.get('direct_mouth_advisory') and
                      mouth_only_notice(review,strict_cast_failures(review,cast_count)))
        verify_solo = primary_sheet or bool((face_info or {}).get('verify_solo_identity'))
        primary_unverified=verify_solo and review.get('checks',{}).get('marked_identity')!='pass'
        if (not mouth_notice and (review['drift'] or review['artifact'])) or first_failures or original_joint_issues or primary_unverified:
            intrusions = final.get('reference_intrusion_frames') or []
            if intrusions:
                card_retry_direction = (
                    f"Keep the source scene on screen throughout <Video 1>, including frames {intrusions[0]} "
                    f"through {intrusions[-1]}. Show each assigned person in place, moving in the scene. "
                    "Reference photos supply appearance only. Render one continuous image from the source camera.")
            if face_info and face_info.get('integral'):
                face_info['retry_corrections']=[row['correction'] for row in review.get('actors',[])
                                                if row.get('correction')]
                if face_info.get('performance_checks'):
                    face_info['retry_corrections']=fidelity.integral.retry_directions(review,
                        actors=face_info['actors'] if job.get('direct_close_cast_guide') else None)
            anchors=[] if whole_identity or cast_image or target_band else await fidelity.guides(service,job,face_info,retry=True)
            if anchors is None:return
            if final.get('reference_intrusion_frames') or primary_unverified or original_joint_issues or first_failures or anchors or review.get('identity_mismatch') or (face_info and face_info.get('integral') and review['artifact']):
                # The accepted first pass keeps its receipt and graph. Build a new
                # graph from the same native recipe for the one bounded retry.
                from .character_update import native_candidate
                from .prompt_language import for_job
                retry_prompt=await for_job(service,service.store.job(job['id']))
                response=await service.client.get(service.settings['comfy_url']+'/object_info');response.raise_for_status()
                retry_seed=p['seed'] if anchors else (p['seed']+1)%9007199254739992
                retry=native_candidate(response.json(),names['video'],names['references'],duration=model_frames/24,
                    frames=model_frames,prompt=retry_prompt,width=width,height=height,seed=retry_seed,character_version=character_version)
                apply_hands(retry,character_version)
                from .vsa_recipe import apply as apply_vsa
                apply_vsa(retry,p)
                direct_audio.apply(retry,p)
                retry=nsfw_adapters.apply_optional_adapters(retry,p)
                retry['28']['inputs']['filename_prefix']=f'gen_studio/{job["id"]}/character-fidelity-retry'
                if face_info and face_info.get('identity_priority') and continuity_image:
                    apply_guide(retry,names['continuity'])
                retry=condition_cast(retry)
                retry=fidelity.apply(retry,anchors)
                retry_render=await phase(service,job,'direct-fidelity-retry',retry,'28','.mp4')
                if retry_render is None:return
                candidate=await cpu(service,job,'finalize',{'video':retry_render['path'],'video_sha256':retry_render['sha256'],
                    'source':prepared['video'],'source_sha256':prepared['files'][prepared['video']], 'frames':p['frames'],
                    'render_frames':model_frames,'width':width,'height':height,**spatial_options},label='fidelity-retry-finalize')
                candidate_review=await fidelity.review(service,job,face_info,candidate,'face-retry')
                if face_info and face_info.get('integral'):
                    improved=fidelity.integral.preferred(candidate_review,review)
                else:
                    improved=candidate_review['status']=='checked' or candidate_review['quality']<review['quality']
                if verify_solo:
                    first_ok=review.get('checks',{}).get('marked_identity')=='pass'
                    retry_ok=candidate_review.get('checks',{}).get('marked_identity')=='pass'
                    if first_ok!=retry_ok:improved=retry_ok
                if cast_count:
                    retry_failures=cast_failures(candidate_review,cast_count)
                    # Prefer a strict subset of actor-specific failures. Otherwise an
                    # unrelated uncertain metric can mask a successful partner repair.
                    old_errors={(i,k) for i,keys in first_failures.items() for k in keys}
                    new_errors={(i,k) for i,keys in retry_failures.items() for k in keys}
                    if old_errors<new_errors or new_errors<old_errors:
                        improved=new_errors<old_errors
                if joint_cast and bool(original_joint_issues)!=bool(joint_issues(candidate_review)):
                    improved=not joint_issues(candidate_review) and not cast_failures(candidate_review,cast_count)
                if bool(candidate.get('reference_intrusion_frames')) != bool(final.get('reference_intrusion_frames')):
                    improved = not candidate.get('reference_intrusion_frames')
                if improved:
                    final,review=candidate,candidate_review
                    service.store.update(job['id'],direct_fidelity_guide_frames=[item['frame'] for item in anchors])
                layout=None if joint_cast else (face_info or {}).get('cast_layout')
                if layout and job.get('direct_marked_cast_policy') in ('protected-cast-4',CAST_VERSION):
                    # A joint retry can integrate one performer naturally but leave the other unchanged.
                    verdicts={row['actor']-1:row['checks'].get('marked_identity',{}).get('verdict')
                              for row in candidate_review.get('actors',[])}
                    if cast_count:
                        for number in cast_failures(candidate_review,cast_count):
                            verdicts[number-1]='fail'
                    if set(verdicts.values())=={'pass','fail'} and len(verdicts)==2:
                        actor=next(index for index,status in verdicts.items() if status=='fail')
                        side=layout['order'].index(actor)
                        receipt=json.loads((folder/f'direct-cast-side-{actor}-complete.json').read_text('utf8'))
                        joint=candidate['video']
                        merged=await cpu(service,job,'cast-retry-composite',{
                            'isolated':receipt['path'],'isolated_sha256':receipt['sha256'],
                            'joint':joint,'joint_sha256':candidate['files'][joint],
                            'source':prepared['video'],'source_sha256':prepared['files'][prepared['video']],
                            'frames':p['frames'],'width':width,'height':height,'side':side,
                            'split':layout['split'],'margin':layout['margin']})
                        merged_review=await fidelity.review(service,job,face_info,merged,'face-cast-composite')
                        passed={row['actor'] for row in merged_review.get('actors',[])
                                if row['checks'].get('marked_identity',{}).get('verdict')=='pass'}
                        if passed=={1,2} and not cast_failures(merged_review,cast_count) and (cast_failures(review,cast_count) or fidelity.integral.preferred(merged_review,review)
                                               or review.get('checks',{}).get('marked_identity')=='fail'):
                            final,review=merged,merged_review
                            service.event(job['id'],'Montaje recompuesto y ambas identidades verificadas.')
                if layout and cast_count==2 and cast_failures(review,cast_count):
                    from .h3_cast_render import retry_actor,mouth_repair_actor,repair_actor
                    repair_base,base_review=retry_render,candidate_review
                    index=retry_actor(base_review,layout['order'])
                    if index is None:
                        for base,evaluation in ((retry_render,candidate_review),(rendered,first_review)):
                            index=mouth_repair_actor(evaluation,layout['order'])
                            if index is not None:
                                repair_base,base_review=base,evaluation
                                break
                    if index is not None:
                        guides=[]
                        if mouth_repair_actor(base_review,layout['order'])==index:
                            from .h3_cast_render import expression_guides
                            guides=await expression_guides(service,job,p,face_info,names,index,candidate,candidate_review,model_frames)
                            if guides is None:return
                        repaired=await repair_actor(service,job,p,face_info,names,repair_base,base_review,index,
                                                    expression_anchors=guides)
                        if repaired is None:return
                        repaired_final=await cpu(service,job,'finalize',{'video':repaired['path'],
                            'video_sha256':repaired['sha256'],'source':prepared['video'],
                            'source_sha256':prepared['files'][prepared['video']],'frames':p['frames'],
                            'render_frames':model_frames,'width':width,'height':height},label='cast-repair-finalize')
                        repaired_review=await fidelity.review(service,job,face_info,repaired_final,'face-cast-repair')
                        if guides and not cast_failures(repaired_review,cast_count):
                            from .h3_cast_render import verify_partner_profile
                            checked_clip=repaired_review.get('corrected') or {'path':repaired_final['video'],
                                'sha256':repaired_final['files'][repaired_final['video']]}
                            await verify_partner_profile(service,job,face_info,1-index,checked_clip,'cast-repair-partner-profile',full_preview=True)
                        if not cast_failures(repaired_review,cast_count):
                            final,review=repaired_final,repaired_review
                            service.event(job['id'],'Reemplazo individual revisado junto con su compañero de plano.')
                    elif face_info['end']-face_info['start']<=73:
                        from .h3_cast_render import cast_framing_matches,joint_acting_candidate,timed_cast_graph,verify_partner_profile
                        if joint_acting_candidate(candidate_review):
                            source=prepared['video'];donor=candidate['video']
                            evidence=await fidelity.cpu(service,job,'cast-timed-guides',{
                                'mode':'cast-timed-guides','source':source,'source_sha256':prepared['files'][source],
                                'path':donor,'path_sha256':candidate['files'][donor],
                                'indices':face_info['indices'],'start':face_info['start'],'end':face_info['end'],
                                'size':[width,height],'split':layout['split']})
                            matched=[{'frame':row['frame'],'name':await fidelity.upload(service,job,
                                      fidelity.path_for(service,row))} for row in evidence['anchors']]
                            if matched:
                                native=timed_cast_graph(job,p,face_info,names,matched)
                                shot=await phase(service,job,'direct-cast-side-timed-acting',native,'28','.mp4')
                                if shot is None:return
                                timed=await cpu(service,job,'finalize',{'video':shot['path'],'video_sha256':shot['sha256'],
                                    'source':source,'source_sha256':prepared['files'][source],'frames':p['frames'],
                                    'render_frames':model_frames,'width':width,'height':height},label='cast-timed-finalize')
                                checked_cast=await fidelity.review(service,job,face_info,timed,'face-cast-timed')
                                remaining=cast_failures(checked_cast,cast_count)
                                if remaining:
                                    final,review=timed,checked_cast
                                elif not cast_framing_matches(checked_cast):
                                    raise rejected_segment(service,job,timed,checked_cast,'La reparación conjunta no conservó la escala o posición facial '
                                                     'del plano original. Se conservan las tomas anteriores.')
                                else:
                                    checked_clip=checked_cast.get('corrected') or {
                                        'path':timed['video'],'sha256':timed['files'][timed['video']]}
                                    for actor_index in layout['order']:
                                        await verify_partner_profile(service,job,face_info,actor_index,checked_clip,
                                                                    f'cast-timed-profile-{actor_index}',full_preview=True)
                                    final,review=timed,checked_cast
                                    service.event(job['id'],'Actuación recompuesta con guías de la misma toma y revisada.')
        if joint_cast and job.get('direct_joint_cast')=='profile-context-2' and candidate_review:
            from .h3_cast_render import cast_framing_matches,repair_actor
            # A confirmed partner in a correctly framed joint retry is the moving base.
            if cast_framing_matches(candidate_review):
                rows=candidate_review.get('actors',[])
                targets=[r['actor']-1 for r in rows if r['checks'].get('marked_identity',{}).get('verdict') in ('fail','uncertain')]
                partners=[r for r in rows if r['checks'].get('marked_identity',{}).get('verdict')=='pass']
                if len(targets)==len(partners)==1 and joint_issues(review):
                    repaired=await repair_actor(service,job,p,face_info,names,retry_render,candidate_review,targets[0])
                    if repaired is None:return
                    updated=await cpu(service,job,'finalize',{'video':repaired['path'],'video_sha256':repaired['sha256'],
                        'source':prepared['video'],'source_sha256':prepared['files'][prepared['video']],
                        'frames':p['frames'],'render_frames':model_frames,'width':width,'height':height},label='joint-profile-finalize')
                    verified=await fidelity.review(service,job,face_info,updated,'face-joint-profile')
                    final,review=updated,verified
        reference_guides=job.get('joint_profile_guides')
        if joint_cast and not whole_frame_cast and reference_guides and (joint_issues(review) or cast_failures(review,cast_count)):
            from .h3_cast_render import timed_cast_graph
            if reference_guides['source_sha256']!=prepared['files'][prepared['video']]:
                raise ValueError('Las guías de perfil pertenecen a otro plano.')
            opening=job.get('joint_profile_opening_guides',[])
            label='joint-opening' if opening else 'joint-guides'
            phase_name='direct-cast-side-profile-opening' if opening else 'direct-cast-side-profile-guides'
            anchors=[]
            for row in sorted([*opening,*reference_guides['anchors']],key=lambda r:r['frame']):
                if not face_info['start']<=row['frame']<face_info['end']:
                    raise ValueError('Guía de perfil fuera del tramo.')
                anchors.append({'frame':row['frame'],'name':await fidelity.upload(service,job,fidelity.path_for(service,row))})
            native=timed_cast_graph(job,p,face_info,names,anchors)
            if opening:
                native['28']['inputs']['filename_prefix']=f'gen_studio/{job["id"]}/cast-profile-opening'
            shot=await phase(service,job,phase_name,native,'28','.mp4')
            if shot is None:return
            final=await cpu(service,job,'finalize',{'video':shot['path'],'video_sha256':shot['sha256'],
                'source':prepared['video'],'source_sha256':prepared['files'][prepared['video']],
                'frames':p['frames'],'render_frames':model_frames,'width':width,'height':height},label=label+'-finalize')
            review=await fidelity.review(service,job,face_info,final,'face-joint-profile-opening' if opening else 'face-joint-profile-guides')
        ensemble=job.get('approved_cast_reference')
        if whole_frame_cast and joint_cast and ensemble and (joint_issues(review) or cast_failures(review,cast_count)):
            image=fidelity.path_for(service,ensemble)
            appearance=await fidelity.upload(service,job,image)
            refs=[*names['references'],appearance]
            cast_description,prompt=joint_reference_prompt(face_info,p['direct_characters'],len(refs),ensemble['direction'])
            native=graph({**p,'prompt':prompt,'frames':model_frames},names['video'],refs,job['id'])
            native=nsfw_adapters.apply_optional_adapters(native,p)
            native['28']['inputs']['filename_prefix']=f'gen_studio/{job["id"]}/joint-cast-reference'
            shot=await phase(service,job,'direct-cast-side-joint-reference',native,'28','.mp4')
            if shot is None:return
            final=await cpu(service,job,'finalize',{'video':shot['path'],'video_sha256':shot['sha256'],
                'source':prepared['video'],'source_sha256':prepared['files'][prepared['video']],
                'frames':p['frames'],'render_frames':model_frames,'width':width,'height':height},label='joint-cast-reference-finalize')
            review=await fidelity.review(service,job,face_info,final,'face-joint-cast-reference')
            acting=job.get('joint_acting_direction')
            if acting:
                review=await fidelity.review(service,job,face_info,final,'face-ensemble-mouth-check')
                if cast_failures(review,cast_count) or joint_issues(review):
                    native=graph({**p,'prompt':acting+' '+cast_description,
                                  'frames':model_frames},names['video'],refs,job['id'])
                    native=nsfw_adapters.apply_optional_adapters(native,p)
                    native['28']['inputs']['filename_prefix']=f'gen_studio/{job["id"]}/joint-acting-reference'
                    acted=await phase(service,job,'direct-cast-side-joint-acting-reference',native,'28','.mp4')
                    if acted is None:return
                    final=await cpu(service,job,'finalize',{'video':acted['path'],'video_sha256':acted['sha256'],
                        'source':prepared['video'],'source_sha256':prepared['files'][prepared['video']],
                        'frames':p['frames'],'render_frames':model_frames,'width':width,'height':height},label='joint-acting-reference-finalize')
                    review=await fidelity.review(service,job,face_info,final,'face-ensemble-mouth-render')

        issues=joint_issues(review)
        failures=cast_failures(review,cast_count)
        if final.get('reference_intrusion_frames'):
            raise rejected_segment(service,job,final,review,'Una ficha de personaje aparece en el vídeo. '
                'Fotogramas del tramo pendientes de revisión: '+', '.join(map(str,final['reference_intrusion_frames'])))
        if issues and not failures:
            raise rejected_segment(service,job,final,review,'El plano conjunto requiere revisión: '+', '.join(issues)+'. Se conservan las tomas anteriores.')
        if failures:
            labels={'head_motion':'orientación de la cabeza','mouth_action':'movimiento de la boca','integration':'integración en la escena','identity':'identidad','marked_identity':'persona marcada','unverified_identity':'identidad sin confirmar','visible_wardrobe':'vestuario de referencia','contacts':'contactos y apoyos','motion':'pose y movimiento','extra_mouth_motion':'apertura de boca añadida'}
            reason='; '.join(p['direct_characters'][i-1]['name']+' ('+', '.join(labels[k] for k in keys)+')' for i,keys in failures.items())
            if issues:reason+='; '+', '.join(issues)
            raise rejected_segment(service,job,final,review,'El plano requiere revisión tras el reintento: '+reason+'. Se conservan las tomas anteriores.')
        if review.get('corrected'):
            corrected=fidelity.path_for(service,review['corrected'])
            final={'video':str(corrected),'files':{str(corrected):review['corrected']['sha256']}}
        if (face_info or {}).get('held_facing') and review.get('checks',{}).get('held_facing')=='fail':
            raise rejected_segment(service,job,final,review,'El personaje sigue girando hacia la cámara tras el reintento automático. Se detuvo el plano antes del montaje.')
        if (face_info or {}).get('confirmed_cast'):
            from .h3_marked_target import missing_replacements
            missing=missing_replacements(review)
            if missing:
                cards=p.get('direct_characters',[])
                names=', '.join(cards[i-1]['name'] for i in missing)
                raise rejected_segment(service,job,final,review,f'El reemplazo de {names} no se confirmó en este plano tras el reintento. Se conservan las tomas anteriores.')
        if verify_solo and review.get('checks',{}).get('marked_identity')!='pass':
            raise rejected_segment(service,job,final,review,'No se confirmó la identidad facial de la referencia tras revisar el primer plano completo.')
        if job.get('direct_marked_identity_review') and review.get('checks',{}).get('marked_identity')=='fail':
            raise rejected_segment(service,job,final,review,'El resultado no aplicó la referencia a la persona confirmada. Se detuvo este plano antes del montaje.')
        public={key:review[key] for key in ('status','notes','mouth','measurements','identity_mismatch','checks','actors','body') if key in review}
        service.store.update(job['id'],direct_fidelity_review=public)
    for path,sha in protected.items():
        if digest(Path(path))!=sha:raise ValueError('Una referencia cambió durante la generación; se conserva el diagnóstico.')
    if p.get('sequence_parent_id'):
        offer_segment(final,review)
        return
    target=service.root/'results'/(job['id']+'.mp4');target.parent.mkdir(exist_ok=True)
    if target.exists() and digest(target)!=final['files'][final['video']]:raise ValueError('Ya existe otro resultado; no se sobrescribe.')
    if not target.exists():shutil.copy2(final['video'],target)
    if checked:service.event(job['id'],'H3 directo terminado · Revisión automática de escala, rostro y labios. Audio conservado desde la fuente.')
    else:service.event(job['id'],'H3 directo terminado: sin SAM, ControlNet, máscaras ni corrección facial. Audio conservado desde la fuente.')
    return service.store.update(job['id'],status='completed',result_available=True,progress=None,
        stage=('H3 directo listo · Revisión integral pendiente' if review['status']=='review' else
               'H3 directo listo · Comprobaciones integrales en fotogramas muestreados') if review and 'checks' in review else
            'H3 directo listo · Revisión facial pendiente' if review and review['status']=='review' else
            'H3 directo listo · Revisión facial comprobada' if review else 'H3 directo listo · Revisa identidad y movimiento',scene_resolution=p['resolution'],
        direct_review={'identity_approved':False,'review_required':True},direct_result_sha256=digest(target))
