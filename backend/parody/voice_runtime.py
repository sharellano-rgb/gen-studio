"""CPU-only preview adapter, scoped subprocess lifetime and private artifacts."""
import asyncio
import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid
import wave
from .analysis_media import command, sha256
from .voice_store import VoiceStore

DISTRO='OpenCast-Ubuntu'
PYTHON='/home/opencast/henge-speech/qwen-env/bin/python'
WORKER=Path(__file__).with_name('voice_worker.py')
CPU_LOCK=Path(os.environ.get('GEN_STUDIO_VOICE_CPU_LOCK',Path.home()/'.gen-studio'/'ollama-cpu.lock'))


def linux(path):
    value=Path(path).resolve().as_posix()
    if len(value)<3 or value[1:3]!=':/':raise ValueError('Se requiere una ruta local de Windows.')
    return '/mnt/'+value[0].lower()+value[2:]


async def availability():
    if os.name!='nt' or not shutil.which('wsl.exe') or not CPU_LOCK.is_file():
        return dict(available=False,reason='Falta el entorno local o su bloqueo cooperativo.')
    code="from pathlib import Path;import json;p=Path('/home/opencast/henge-speech');assert (p/'production-voice.lock').is_file();assert (p/'qwen-voice-design/model.safetensors').is_file();assert (p/'qwen-base/model.safetensors').is_file();assert json.loads((p/'qwen-sources.json').read_text())['revision']=='5ecdb67327fd37bb2e042aab12ff7391903235d3';assert json.loads((p/'qwen-base-sources.json').read_text())['revision']=='fd4b254389122332181a7c3db7f27e918eec64e3';print('ready')"
    try:
        out,_=await command(['wsl.exe','-d',DISTRO,'--',PYTHON,'-B','-c',code],timeout=15)
        ready=out.strip()==b'ready'
        return dict(available=ready,reason='Pesos y entorno existentes verificados; vista previa en CPU.' if ready else 'No se pudo verificar VoiceStudio/Qwen local.',
                    device='cpu',languages=['Spanish','English'],design=True,clone=True,voice_directed_duration=False,clone_acting_instructions=False)
    except Exception:
        return dict(available=False,reason='El entorno WSL de voz no respondió o cambió su revisión.')


def free_ram():
    class Memory(ctypes.Structure):
        _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(name,ctypes.c_ulonglong) for name in ('total','available','page_total','page_available','virtual_total','virtual_available','extended')]
    memory=Memory();memory.length=ctypes.sizeof(memory)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):raise ValueError('No se pudo medir RAM disponible.')
    return memory.available


def local_input(service,reference):
    asset=service.store.asset(reference['asset_id'])
    if not asset:raise ValueError('Referencia no encontrada en este espacio.')
    root=(service.root/'inputs').resolve();path=(root/asset['filename']).resolve()
    if not root.is_relative_to(service.root.resolve()) or not path.is_relative_to(root) or not path.is_file():raise ValueError('Referencia fuera del espacio.')
    if sha256(path)!=reference['sha256']:raise ValueError('Cambió el archivo de referencia. Revisa y guarda otra versión.')
    return path


def atomic_json(path,value):
    temporary=path.with_suffix('.tmp.json')
    temporary.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8');temporary.replace(path)


class VoiceCancelled(Exception):pass
class CleanupFailed(Exception):pass


async def worker(folder,request,stopped,stage):
    token=uuid.uuid4().hex;request=dict(request,lease_token=token)
    atomic_json(folder/'request.json',request)
    # Never replace a file concurrently opened by WSL through DrvFS.
    atomic_json(folder/'lease.json',dict(token=token));proc=None
    try:
        with (folder/'worker.log').open('wb') as log:
            proc=await asyncio.create_subprocess_exec('wsl.exe','-d',DISTRO,'--',PYTHON,'-B',linux(WORKER),linux(folder),stdout=log,stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            started=time.monotonic();progress=None;waiter=asyncio.create_task(proc.wait())
            while proc.returncode is None:
                stopped()
                if time.monotonic()-started>600:raise TimeoutError('La vista previa superó diez minutos; reduce la frase.')
                os.utime(folder/'lease.json',None)
                if (folder/'progress.json').exists() and progress is None:
                    progress=True;stage('Sintetizando voz en CPU')
                try:await asyncio.wait_for(asyncio.shield(waiter),timeout=.5)
                except asyncio.TimeoutError:pass
            if proc.returncode:raise ValueError('El trabajador local de voz no completó la frase. Revisa su registro privado.')
    finally:
        # If signalling fails, the immutable lease expires on its own within 12 s.
        try:(folder/'cancel.flag').write_text(token,encoding='utf-8')
        except OSError:pass
        if proc is not None and proc.returncode is None:
            try:await asyncio.shield(asyncio.wait_for(proc.wait(),20))
            except BaseException as error:raise CleanupFailed('No se confirmó el cierre del trabajador de voz; el turno permanece retenido.') from error


def inspect_wav(path):
    if not path.is_file() or path.stat().st_size>8_000_000:raise ValueError('Salida de voz ausente o demasiado grande.')
    with wave.open(str(path),'rb') as audio:
        if audio.getnchannels()!=1 or audio.getsampwidth()!=2 or audio.getframerate()!=24000 or audio.getnframes()==0:raise ValueError('Formato de voz inesperado.')
        return dict(sample_rate=24000,channels=1,duration_ms=round(audio.getnframes()/24),sha256=sha256(path),bytes=path.stat().st_size)


async def execute(service,record):
    store=VoiceStore(service.root);project=record['project_id'];ident=record['id'];folder=store.directory(ident)
    folder.mkdir(parents=True,exist_ok=True)
    started=time.monotonic();released=True;shared=None
    def save(**patch):return store.update(project,ident,**patch)
    def stopped():
        if store.read(project,ident)['cancel_requested']:raise VoiceCancelled()
    line=record['binding']['line'];raw=folder/'speech.wav';output=folder/'preview.wav'
    ffmpeg=shutil.which('ffmpeg')
    try:
        if not ffmpeg:raise ValueError('FFmpeg no está disponible.')
        stopped()
        if line['kind']=='speech':
            if not (await availability())['available']:raise ValueError('El motor local de voz no está disponible.')
            if free_ram()<16*1024**3:raise ValueError('La vista previa en CPU requiere 16 GiB de RAM libre.')
            import msvcrt
            # Use the existing cooperative lock without changing its bytes or creating it.
            shared=CPU_LOCK.open('r+b');shared.seek(0)
            try:msvcrt.locking(shared.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:shared.close();shared=None;raise ValueError('Otro proyecto está usando el recurso de voz/CPU. Reintenta al terminar.')
            voice=line['voice']
            request=dict(mode=voice['mode'],text=line['text'],language=line['language'],seed=line['seed'],description=voice['description'])
            if voice['mode']=='clone':
                if not voice['authorized'] or len(voice['permission_note'])<10:raise ValueError('Confirma el permiso y la procedencia de la muestra antes de clonar.')
                source=await asyncio.to_thread(local_input,service,record['binding']['reference'])
                await command([ffmpeg,'-v','error','-nostdin','-y','-ss',str(voice['reference_start_ms']/1000),'-i',str(source),'-t',str((voice['reference_end_ms']-voice['reference_start_ms'])/1000),'-vn','-ac','1','-ar','24000','-c:a','pcm_s16le',str(folder/'reference.wav')])
                metadata=inspect_wav(folder/'reference.wav')
                if not 3000<=metadata['duration_ms']<=25000:raise ValueError('El clip de voz decodificado debe durar entre 3 y 25 segundos.')
                request.update(reference_sha256=metadata['sha256'],transcript=voice['transcript'],authorized=True,permission_note=voice['permission_note'])
            if clip:=record['binding'].get('phrase_audio'):
                from .script import split_phrase
                path=await asyncio.to_thread(local_input,service,clip)
                from ..media import probe
                info=await asyncio.to_thread(probe,path)
                if not info['audio'] or clip['end_ms']>round(info['duration']*1000):
                    raise ValueError('Revisa el intervalo y la pista de audio del clip de la frase.')
                seconds=(clip['end_ms']-clip['start_ms'])/1000
                await command([ffmpeg,'-v','error','-nostdin','-y','-ss',str(clip['start_ms']/1000),'-i',str(path),'-t',str(seconds),'-map','0:a:0','-ac','1','-ar','24000','-af',f'afade=t=in:d=0.006,afade=t=out:st={max(0,seconds-.008)}:d=0.008','-c:a','pcm_s16le',str(folder/'phrase.wav')])
                request['phrase_context']=split_phrase(line['text'],clip['text'])
                request['phrase_sha256']=inspect_wav(folder/'phrase.wav')['sha256']
            stopped();save(stage='Cargando Qwen3-TTS en CPU')
            await worker(folder,request,stopped,lambda stage:save(stage=stage))
            proof=json.loads((folder/'result.json').read_text())
            if proof['sha256']!=sha256(raw):raise ValueError('La huella de la voz no coincide.')
        elif line['kind']=='original':
            source=await asyncio.to_thread(local_input,service,record['binding']['source'])
            await command([ffmpeg,'-v','error','-nostdin','-y','-ss',str(line['start_ms']/1000),'-i',str(source),'-t',str((line['end_ms']-line['start_ms'])/1000),'-vn','-ac','1','-ar','24000','-c:a','pcm_s16le',str(raw)])
            proof=dict(engine='original-audio-excerpt',synthetic=False)
        else:
            await command([ffmpeg,'-v','error','-nostdin','-y','-f','lavfi','-i','anullsrc=r=24000:cl=mono','-t',str((line['end_ms']-line['start_ms'])/1000),'-c:a','pcm_s16le',str(raw)])
            proof=dict(engine='silence',synthetic=False)
        stopped();metadata=inspect_wav(raw)
        filters=f"adelay={line['pause_before_ms']}:all=1,apad=pad_dur={line['pause_after_ms']/1000}"
        await command([ffmpeg,'-v','error','-nostdin','-y','-i',str(raw),'-af',filters,'-metadata','comment=GEN STUDIO - PARODIA SINTETICA - VISTA PREVIA','-c:a','pcm_s16le',str(output)])
        artifact=inspect_wav(output);artifact['filename']='preview.wav'
        artifact['fits_interval']=artifact['duration_ms']<=line['end_ms']-line['start_ms']+40
        stopped()
        save(status='review_pending',stage='Voz lista para escuchar y revisar tiempos',artifact=artifact,proof=proof)
    except VoiceCancelled:save(status='cancelled',stage='Vista previa cancelada')
    except asyncio.CancelledError:
        save(status='interrupted',stage='Servicio interrumpido; no se reenvía automáticamente')
        raise
    except CleanupFailed as error:
        released=False;save(status='cleanup_failed',stage='Revisar trabajador de voz',error=str(error))
    except Exception as error:save(status='failed',stage='No se pudo preparar la voz',error=str(error)[:700])
    finally:
        if shared:
            import msvcrt
            shared.seek(0);msvcrt.locking(shared.fileno(),msvcrt.LK_UNLCK,1);shared.close()
        save(resources_released=released,elapsed_seconds=round(time.monotonic()-started,3))
        if released:
            service.enhancer_active=False;service.release_analysis();service.wake.set()
