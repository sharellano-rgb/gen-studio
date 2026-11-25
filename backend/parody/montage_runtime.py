"""Bounded subprocesses, private segment cache and validated assembly."""
import asyncio
import json
import os
import shutil
import subprocess
import time
import wave
from pathlib import Path
from uuid import uuid4
from .analysis_media import sha256
from .montage import MontageStore, result_path
from .voice_store import VoiceStore
from .voice_runtime import atomic_json
from .timing_integration import TimingReviewRequired
from .reframe import VERSION as REFRAME_VERSION,crop_filter


class MontageCancelled(Exception):pass
class CleanupFailed(Exception):pass


async def command(args,stopped,timeout=300,log=None,lease=None):
    process=await asyncio.create_subprocess_exec(*map(str,args),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    reader=asyncio.create_task(process.communicate());started=time.monotonic()
    try:
        while not reader.done():
            stopped()
            if lease:os.utime(lease,None)
            if time.monotonic()-started>timeout:raise TimeoutError('La etapa superó el tiempo permitido; progreso guardado.')
            await asyncio.wait({reader},timeout=.25)
        out,err=await reader
        if log:Path(log).write_bytes(err+out)
        if process.returncode:raise ValueError('Falló el proceso local: '+err.decode(errors='replace')[-600:])
        return out
    finally:
        if process.returncode is None:
            from ..process_cleanup import stop_process_tree
            try:await asyncio.shield(stop_process_tree(process))
            except BaseException as error:raise CleanupFailed('No se confirmó el cierre del proceso propio; turno retenido.') from error
        await asyncio.shield(reader)


async def inspect(path,frames,width,height,stopped):
    raw=await command(['ffprobe','-v','error','-count_frames','-show_streams','-show_format','-of','json',path],stopped)
    info=json.loads(raw);video=next(s for s in info['streams'] if s['codec_type']=='video');audio=next(s for s in info['streams'] if s['codec_type']=='audio')
    if (video['width'],video['height'],video['r_frame_rate'],int(video['nb_read_frames']))!=(width,height,'24/1',frames):
        raise ValueError('El resultado no conserva dimensiones, FPS o fotogramas.')
    if abs(float(info['format']['duration'])-frames/24)>.05:raise ValueError('La duración final no coincide.')
    await command(['ffmpeg','-v','error','-nostdin','-i',path,'-f','null','-'],stopped)
    return dict(sha256=sha256(path),frames=frames,fps=24,width=width,height=height,duration=frames/24,audio_codec=audio['codec_name'])


def cache_path(store,digest):
    root=(store.folder/'cache').resolve();path=(root/digest).resolve()
    if len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest) or not path.is_relative_to(store.folder.resolve()):raise ValueError('Caché fuera del espacio.')
    return path


def segment_source(service,inputs):
    if inputs.get('version') in ('parody-original-segment-1','parody-original-segment-2',REFRAME_VERSION):
        from .voice_runtime import local_input
        return local_input(service,inputs['source'])
    if inputs.get('speech_render'):
        from .render import RenderStore
        from .speech import result_path as speech_path
        binding=inputs['speech_render'];record=RenderStore(service.root).read(binding['project_id'],binding['plan_id'])
        source=speech_path(service,record)
        if sha256(source)!=inputs['video_sha256']:raise ValueError('Cambió el plano aprobado de voz H3.')
    else:
        job=service.store.job(inputs['job_id']);source=result_path(service,job)
    return source


async def segment(service,store,inputs,folder,stopped,progress=lambda text:None):
    source=segment_source(service,inputs)
    if inputs.get('dialogue_edit',{}).get('removed_frames'):
        from .dialogue_edit import render
        return await render(inputs,source,folder,stopped)
    sel=inputs['selection'];duration=inputs['frames']/24
    start=sel['start_frame']/24;frames=inputs['frames'];audio=folder/'audio.wav';video=folder/'visual.mp4';output=folder/'segment.mp4'
    original=inputs.get('version') in ('parody-original-segment-1','parody-original-segment-2',REFRAME_VERSION)
    if original:start=inputs['source_start_seconds']
    copy_visual=False
    if sel.get('timing') and sel['start_frame']==0:
        probe=json.loads(await command(['ffprobe','-v','error','-count_frames','-show_streams','-of','json',source],stopped))
        v=next(s for s in probe['streams'] if s['codec_type']=='video')
        copy_visual=v.get('codec_name')=='h264' and int(v['nb_read_frames'])==frames and v['r_frame_rate']==v['avg_frame_rate']=='24/1'
    if original:
        w,h=inputs['width'],inputs['height']
        crop=crop_filter(sel['reframe']) if inputs.get('version')==REFRAME_VERSION else ''
        filters=(f'trim=start={start}:duration={duration},setpts=PTS-STARTPTS,fps=24,'
            f'{crop}'
            f'scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2:reset_sar=1,'
            f'pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1')
        await command(['ffmpeg','-v','error','-nostdin','-n','-i',source,'-an','-vf',filters,'-frames:v',frames,'-c:v','libx264','-crf','17','-pix_fmt','yuv420p',video],stopped)
    elif copy_visual:
        await command(['ffmpeg','-v','error','-nostdin','-n','-i',source,'-map','0:v:0','-c:v','copy','-an',video],stopped)
    else:
        await command(['ffmpeg','-v','error','-nostdin','-n','-i',source,'-an','-vf',f"trim=start_frame={sel['start_frame']}:end_frame={sel['end_frame']},setpts=PTS-STARTPTS",'-frames:v',frames,'-c:v','libx264','-crf','17','-pix_fmt','yuv420p',video],stopped)
    if sel['audio']=='source':
        probe=json.loads(await command(['ffprobe','-v','error','-show_streams','-of','json',source],stopped))
        if any(s['codec_type']=='audio' for s in probe['streams']):
            if inputs.get('version') in ('parody-original-segment-2',REFRAME_VERSION):
                first=round(start*48000);count=frames*2000
                filters=f'aresample=48000,atrim=start_sample={first}:end_sample={first+count},asetpts=N/SR/TB,apad=whole_len={count},atrim=end_sample={count}'
                await command(['ffmpeg','-v','error','-nostdin','-n','-i',source,'-vn','-af',filters,'-ar','48000','-ac','2','-c:a','pcm_s16le',audio],stopped)
            else:
                await command(['ffmpeg','-v','error','-nostdin','-n','-i',source,'-vn','-af',f'atrim=start={start}:duration={duration},asetpts=PTS-STARTPTS,apad','-t',duration,'-ar','48000','-ac','2','-c:a','pcm_s16le',audio],stopped)
        else:await command(['ffmpeg','-v','error','-nostdin','-n','-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-t',duration,'-c:a','pcm_s16le',audio],stopped)
    else:
        args=['ffmpeg','-v','error','-nostdin','-n','-f','lavfi','-i','anullsrc=r=48000:cl=stereo'];filters=[];labels=['[0:a]']
        for index,voice in enumerate(inputs['voices'],1):
            path=VoiceStore(service.root).directory(voice['id'])/'preview.wav'
            if sha256(path)!=voice['sha256']:raise ValueError('La voz cambió antes de montar.')
            args+=['-i',path];label=f'v{index}';labels.append(f'[{label}]')
            filters.append(f"[{index}:a]aresample=48000,adelay={voice['offset_ms']}:all=1[{label}]")
        filters.append(''.join(labels)+f'amix=inputs={len(labels)}:normalize=0:duration=first,atrim=duration={duration}[mix]')
        await command(args+['-filter_complex',';'.join(filters),'-map','[mix]','-t',duration,'-ar','48000','-ac','2','-c:a','pcm_s16le',audio],stopped)
    if sel['lipsync']=='latentsync':
        from .lipsync_runtime import synchronize
        video=await synchronize(service,inputs,folder,video,audio,stopped)
    timing_report=None;timing_context=None
    if sel.get('timing'):
        from .timing_integration import apply_timing
        adjusted,timing_report,timing_context=await apply_timing(service,inputs,folder,video,audio,stopped,progress)
        # Keep original PCM for diagnostics; cache and final assembly adopt the verified shifted PCM.
        shutil.copyfile(audio,folder/'audio-before-timing.wav');shutil.copyfile(adjusted,audio)
    await command(['ffmpeg','-v','error','-nostdin','-n','-i',video,'-i',audio,'-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-b:a','192k','-t',duration,'-movflags','+faststart','-metadata','comment=GEN STUDIO - PARODIA SINTETICA - REVISION PENDIENTE',output],stopped)
    meta=await inspect(output,frames,inputs['width'],inputs['height'],stopped)
    if timing_report is not None:
        from .timing_integration import postcheck
        from .audio_timing_runtime import video_hash
        if await video_hash(video,stopped)!=await video_hash(output,stopped):raise ValueError('El ajuste temporal modificó el bitstream del plano.')
        timing_report=await postcheck(service,inputs,folder,output,timing_report,timing_context,stopped,progress)
        atomic_json(folder/'timing-report.json',timing_report)
        meta['timing']=dict(mode=timing_report['mode'],delay_ms=timing_report['delay_samples']/48,
            residual_ms=timing_report.get('residual_ms'),residual_verified=timing_report.get('residual_verified',False),
            explanation=timing_report.get('decision',{}).get('explanation','Ajuste manual revisado por el usuario.'),
            visual_bitstream_preserved=copy_visual)
    return meta


async def execute(service,record):
    store=MontageStore(service.root);p=record['project_id'];ident=record['id'];started=time.monotonic();attempt=store.directory(ident)/uuid4().hex
    attempt.mkdir(parents=True,exist_ok=False);receipts=[];released=True
    def save(**patch):return store.update(p,ident,**patch)
    def stopped():
        if store.read(p,ident)['cancel_requested']:raise MontageCancelled()
    try:
        for index,item in enumerate(record['plan']['segments']):
            stopped();folder=cache_path(store,item['input_hash']);receipt=folder/'receipt.json';reused=False
            source=segment_source(service,item['inputs'])
            if sha256(source)!=item['inputs']['video_sha256']:raise ValueError('Cambió la toma antes de reutilizarla.')
            for voice in item['inputs']['voices']:
                if sha256(VoiceStore(service.root).directory(voice['id'])/'preview.wav')!=voice['sha256']:raise ValueError('Cambió la frase antes de reutilizarla.')
            if receipt.is_file():
                meta=json.loads(receipt.read_text());cached=folder/meta['file']
                if not cached.resolve().is_relative_to(folder) or not cached.is_file() or sha256(cached)!=meta['sha256']:raise ValueError('Cambió un segmento guardado; no se reutiliza.')
                if sha256(folder/'audio.wav')!=meta['audio_sha256']:raise ValueError('Cambió la pista PCM guardada.')
                reused=True
            else:
                work=attempt/f'segment-{index:03d}';work.mkdir()
                save(stage=f'Preparando segmento {index+1}/{len(record["plan"]["segments"])}',progress=round(index/len(record['plan']['segments'])*90))
                meta=await segment(service,store,item['inputs'],work,stopped,lambda text:save(stage=f'Plano {index+1}: {text}'));stopped()
                folder.mkdir(parents=True,exist_ok=True);cached=folder/'segment.mp4'
                shutil.copyfile(work/'segment.mp4',cached);shutil.copyfile(work/'audio.wav',folder/'audio.wav')
                meta.update(file='segment.mp4',input_hash=item['input_hash'],audio_sha256=sha256(folder/'audio.wav'))
                atomic_json(receipt,meta)
            # Simple relative names avoid escaping arbitrary Windows paths in concat scripts.
            shutil.copyfile(cached,attempt/f'clip-{index:03d}.mp4')
            shutil.copyfile(folder/'audio.wav',attempt/f'audio-{index:03d}.wav')
            detail=dict(index=index,input_hash=item['input_hash'],reused=reused,sha256=meta['sha256'])
            if 'timing' in meta:detail['timing']=meta['timing']
            receipts.append(detail)
            save(segments=receipts)
        stopped();save(stage='Ensamblando cortes y validando audio',progress=95)
        (attempt/'concat.txt').write_text(''.join(f"file 'clip-{i:03d}.mp4'\nduration {s['inputs']['frames']/24}\n" for i,s in enumerate(record['plan']['segments'])),encoding='utf-8')
        # Join exact PCM samples, not AAC packets with encoder delay at each cut.
        with wave.open(str(attempt/'mix.wav'),'wb') as target:
            target.setnchannels(2);target.setsampwidth(2);target.setframerate(48000)
            for index,item in enumerate(record['plan']['segments']):
                stopped()
                with wave.open(str(attempt/f'audio-{index:03d}.wav'),'rb') as source:
                    if (source.getnchannels(),source.getsampwidth(),source.getframerate(),source.getnframes())!=(2,2,48000,item['inputs']['frames']*2000):raise ValueError('La pista PCM no coincide con el segmento.')
                    target.writeframes(source.readframes(source.getnframes()))
        single_timed=len(record['plan']['segments'])==1 and record['plan']['segments'][0]['inputs']['selection'].get('timing')
        source_args=['-i',attempt/'clip-000.mp4'] if single_timed else ['-f','concat','-safe','1','-i',attempt/'concat.txt']
        encode_args=['-c:v','copy'] if single_timed else ['-vf','setpts=N/(24*TB)','-c:v','libx264','-crf','17','-r','24']
        await command(['ffmpeg','-v','error','-nostdin','-n',*source_args,'-i',attempt/'mix.wav','-map','0:v:0','-map','1:a:0',*encode_args,'-c:a','aac','-b:a','192k','-t',record['plan']['frames']/24,'-movflags','+faststart','-metadata','comment=GEN STUDIO - PARODIA SINTETICA',attempt/'final.mp4'],stopped)
        width,height=record['plan']['resolution'];meta=await inspect(attempt/'final.mp4',record['plan']['frames'],width,height,stopped);stopped()
        meta['file']=str((attempt/'final.mp4').relative_to(store.directory(ident))).replace('\\','/')
        stage='Montaje listo para revisar'
        if rule:=record['plan'].get('required_phrase'):
            from .phrase_reference import transcribe
            from .script import phrase_line
            detected=False
            for i in rule['segments']:
                stopped()
                try:
                    rows=await transcribe(attempt/f'audio-{i:03d}.wav',stopped)
                except (OSError,ValueError,TimeoutError):
                    continue
                if phrase_line(rule,dict(character_id=rule['character_id'],text=' '.join(r.get('text','') for r in rows))):
                    detected=True
                    break
            meta['phrase_asr_match']=detected
            stage=('Frase detectada en la transcripción; revisa la interpretación' if detected else
                   'Escucha el montaje: la transcripción no confirmó la frase obligatoria')
            if rule.get('use_clip_audio') and not detected:
                from .phrase_reference import check_audio_reference
                for i in rule['segments']:
                    stopped()
                    try:
                        same=await check_audio_reference(service,record['plan']['segments'][i]['inputs'],rule,attempt/f'audio-{i:03d}.wav',attempt,stopped)
                    except (OSError,ValueError,subprocess.SubprocessError,TimeoutError):
                        continue
                    if same:
                        meta['phrase_reference_audio_match']=True
                        stage='El audio coincide con el clip de la frase; escucha las palabras y revisa el gesto'
                        break
        save(status='review_pending',stage=stage,artifact=meta,progress=100,review_required=True)
    except TimingReviewRequired as error:
        save(status='timing_review_required',stage='Revisa el ajuste de voz de este plano',timing_review=dict(index=index,**error.report),
             error=None,artifact=None)
    except MontageCancelled:save(status='cancelled',stage='Montaje cancelado; segmentos validados conservados')
    except asyncio.CancelledError:
        save(status='interrupted',stage='Montaje interrumpido; puede reanudarse');raise
    except CleanupFailed as error:
        released=False;save(status='cleanup_failed',stage='Comprobar proceso propio antes de liberar turno',error=str(error))
    except Exception as error:save(status='failed',stage='Montaje no completado; progreso conservado',error=str(error)[:800])
    finally:
        save(resources_released=released,elapsed_seconds=round(time.monotonic()-started,3))
        if released:service.enhancer_active=False;service.release_analysis();service.wake.set()
