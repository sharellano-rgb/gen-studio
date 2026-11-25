"""Per-phrase acting evidence. The performance clip never replaces the scene or voice."""
import json
import os
from pathlib import Path
from ..media import probe
from .repository import Problem
from .voice_runtime import local_input
from .montage_runtime import command


def bind(service,project,rule):
    actor=next(c for c in project['cast']['characters'] if c['id']==rule['character_id'])
    ref=next((r for r in actor['references'] if r['id']==rule['reference_id'] and r['role']=='acting'),None)
    if not ref:
        raise Problem(422,'PHRASE_CLIP','Selecciona un clip del personaje que dice la frase.')
    clip={**rule,'asset_id':ref['asset_id'],'sha256':ref['sha256']}
    info=probe(local_input(service,clip))
    if not info['audio'] or clip['end_ms']>round(info['duration']*1000):
        raise Problem(422,'PHRASE_INTERVAL','Elige un intervalo dentro del vídeo, con la frase audible.')
    return clip


async def observe(service,clip,folder,stopped):
    source=local_input(service,clip)
    seconds=(clip['end_ms']-clip['start_ms'])/1000
    base=['ffmpeg','-v','error','-nostdin','-n','-ss',str(clip['start_ms']/1000),'-t',str(seconds),'-i',source]
    sheet=folder/'phrase-gestures.jpg'
    await command(base+['-vf',f'fps={4/seconds}:start_time=0,scale=384:256:force_original_aspect_ratio=decrease,pad=384:256:(ow-iw)/2:(oh-ih)/2,tpad=stop_mode=clone:stop=4,tile=2x2',
                        '-frames:v','1',sheet],stopped)
    wav=folder/'phrase-performance.wav'
    await command(base+['-map','0:a:0','-ar','16000','-ac','1',wav],stopped)
    rows=await transcribe(wav,stopped)
    if not rows:
        raise ValueError('No se detectó voz en el intervalo de la frase. Revisa el clip.')
    evidence=dict(phrase=clip['text'],duration_ms=round(seconds*1000),audio_observations=rows)
    return sheet,evidence


async def transcribe(wav,stopped):
    python=os.environ.get('GEN_STUDIO_ACTING_ASR_PYTHON','D:/AI/apps/ComfyUI-ComfyField/.venv/Scripts/python.exe')
    worker=Path(__file__).with_name('acting_audio_worker.py')
    return json.loads(await command([python,'-B','-X','utf8',worker,wav],stopped,timeout=120))


def matches_recording(reference,native):
    from .dialogue_edit import similarity
    peak=max(reference,default=0)
    active=[i for i,x in enumerate(reference) if x>max(30,.025*peak)]
    if not active:return False
    ref=reference[active[0]:active[-1]+1]
    if len(ref)<35 or len(ref)>len(native):return False
    half=len(ref)//2
    for i in range(len(native)-len(ref)+1):
        audio=native[i:i+len(ref)]
        if (similarity(ref,audio)>.92 and similarity(ref[:half],audio[:half])>.87
                and similarity(ref[half:],audio[half:])>.87):
            return True
    return False


async def check_audio_reference(service,inputs,rule,wav,folder,stopped):
    from .dialogue_edit import envelope
    from .render import RenderStore
    import asyncio
    binding=inputs.get('speech_render')
    if not binding:return False
    take=RenderStore(service.root).read(binding['project_id'],binding['plan_id'])
    clip=take['plan']['speech'].get('phrase_clip')
    if not clip or not clip.get('use_clip_audio') or any(clip[k]!=rule[k] for k in ('reference_id','start_ms','end_ms')):
        return False
    duration=(clip['end_ms']-clip['start_ms'])/1000
    if duration>inputs['frames']/24+.04:return False
    path=await asyncio.to_thread(local_input,service,clip)
    ref=folder/(wav.stem+'-phrase-ref.wav')
    await command(['ffmpeg','-v','error','-nostdin','-n','-ss',str(clip['start_ms']/1000),'-i',path,
                   '-t',str(duration),'-map','0:a:0','-ac','1','-ar','16000',ref],stopped)
    return await asyncio.to_thread(lambda:matches_recording(envelope(ref),envelope(wav)))
