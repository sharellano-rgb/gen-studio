"""Own offline CPU process; reads existing VoiceStudio models without importing its service.

Invoked by the Gen Studio adapter only. Never writes to VoiceStudio/Henge paths.
"""
import os
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',PYTHONDONTWRITEBYTECODE='1',CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='4')
import fcntl
import hashlib
import json
import random
import sys
import threading
import time
from pathlib import Path


def main():
    folder=Path(sys.argv[1]).resolve()
    # Workspaces and test data are beneath this project's verified physical root.
    root=Path('/mnt/d/AI/projects/GEN-STUDIO').resolve()
    if not folder.is_relative_to(root) or folder.parent.name!='voices':raise ValueError('Invalid private voice folder')
    from uuid import UUID
    UUID(folder.name)
    req=json.loads((folder/'request.json').read_text())
    if json.loads((folder/'lease.json').read_text())['token']!=req['lease_token']:raise ValueError('Lease token mismatch')
    def watch():
        while True:
            try:
                if (folder/'cancel.flag').exists() or time.time()-(folder/'lease.json').stat().st_mtime>12:os._exit(76)
            except (OSError,ValueError,KeyError):os._exit(76)
            time.sleep(.25)
    threading.Thread(target=watch,daemon=True).start()
    # Existing cooperative lock, read-only handle: do not create or rewrite it.
    lock=open('/home/opencast/henge-speech/production-voice.lock','rb')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if req.get('lease_test'):
        (folder/'lease-ready').write_text('ready')
        while True:time.sleep(.2)
    mode=req['mode']
    if mode not in ('design','clone') or req['language'] not in ('Spanish','English') or not 1<=len(req['text'])<=650 or type(req['seed']) is not int or not 0<=req['seed']<2**32:
        raise ValueError('Invalid voice request')
    base=Path('/home/opencast/henge-speech')
    name,manifest,revision=('qwen-voice-design','qwen-sources.json','5ecdb67327fd37bb2e042aab12ff7391903235d3') if mode=='design' else ('qwen-base','qwen-base-sources.json','fd4b254389122332181a7c3db7f27e918eec64e3')
    if json.loads((base/manifest).read_text())['revision']!=revision:raise ValueError('Model revision mismatch')
    os.environ['HF_HOME']=str(folder/'cache')
    os.environ['TMPDIR']=str(folder/'tmp');(folder/'tmp').mkdir(exist_ok=True)
    import numpy as np
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel
    torch.set_num_threads(4)
    started=time.monotonic()
    model=Qwen3TTSModel.from_pretrained(str(base/name),device_map='cpu',dtype=torch.float32,attn_implementation='sdpa',local_files_only=True)
    torch.manual_seed(req['seed']);random.seed(req['seed']);np.random.seed(req['seed'])
    (folder/'progress.json').write_text(json.dumps({'stage':'Sintetizando voz en CPU'}))
    @torch.inference_mode()
    def synthesize(text):
        common=dict(text=text,language=req['language'],max_new_tokens=640,temperature=.7,top_p=.9,do_sample=True)
        if mode=='design':
            if not 5<=len(req['description'])<=700:raise ValueError('Invalid voice description')
            waves,sr=model.generate_voice_design(instruct=req['description'],**common)
        else:
            ref=folder/'reference.wav'
            if not req['authorized'] or len(req['permission_note'])<10 or hashlib.sha256(ref.read_bytes()).hexdigest()!=req['reference_sha256'] or not 3<=sf.info(ref).duration<=25 or not 5<=len(req['transcript'])<=1600:raise ValueError('Invalid authorized reference')
            waves,sr=model.generate_voice_clone(ref_audio=str(ref),ref_text=req['transcript'],x_vector_only_mode=False,**common)
        return np.asarray(waves[0],dtype=np.float32).reshape(-1),sr
    if 'phrase_context' in req:
        excerpt=folder/'phrase.wav'
        if hashlib.sha256(excerpt.read_bytes()).hexdigest()!=req['phrase_sha256']:
            raise ValueError('Cambió el fragmento de la guía de voz.')
        cue,sr=sf.read(excerpt,dtype='float32')
        if sr!=24000 or cue.ndim!=1 or not 0.23<len(cue)/sr<10.1 or np.max(np.abs(cue))<.001:
            raise ValueError('El fragmento seleccionado no contiene audio utilizable.')
        parts=[]
        for index,text in enumerate(req['phrase_context']):
            if index==1:parts.append(cue)
            if any(c.isalnum() for c in text):
                part,rate=synthesize(text)
                if rate!=sr:raise ValueError('Frecuencias de voz incompatibles.')
                # Keep short breathing margins while joining to a spoken excerpt.
                active=np.flatnonzero(abs(part)>max(.003,np.max(abs(part))*.022))
                if not len(active):raise ValueError('La voz sintetizada está vacía.')
                part=part[max(0,active[0]-1200):active[-1]+1680].copy()
                fade=min(len(part)//2,120)
                if fade:
                    part[:fade]*=np.linspace(0,1,fade);part[-fade:]*=np.linspace(1,0,fade)
                parts.append(part)
        wav=np.concatenate(parts)
    else:
        wav,sr=synthesize(req['text'])
    if sr!=24000 or not .2<len(wav)/sr<48 or not np.isfinite(wav).all() or np.max(np.abs(wav))<.001:raise ValueError('Invalid audio output')
    sf.write(folder/'speech.wav',wav,sr,subtype='PCM_16')
    proof=dict(engine='Qwen3-TTS-12Hz-1.7B-'+('VoiceDesign' if mode=='design' else 'Base'),revision=revision,device='cpu',sample_rate=sr,
               duration=len(wav)/sr,seconds=round(time.monotonic()-started,3),seed=req['seed'],
               text_sha256=hashlib.sha256(req['text'].encode()).hexdigest(),sha256=hashlib.sha256((folder/'speech.wav').read_bytes()).hexdigest(),
               reference_sha256=req.get('reference_sha256'),listening_review='pending',synthetic=True)
    if 'phrase_sha256' in req:
        proof['authorized_clip_guide_sha256']=req['phrase_sha256']
    (folder/'result.json').write_text(json.dumps(proof),encoding='utf-8')


if __name__=='__main__':main()
