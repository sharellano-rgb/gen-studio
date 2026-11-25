"""Offline CPU worker for acting-reference audio. JSON on stdout."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np

ROOT = Path(os.environ.get('GEN_STUDIO_ACTING_ASR_ROOT',
                            Path.home()/'.gen-studio'/'media-engines'))
RATE = 16000
FRAME = 320


def runs(mask):
    edges = np.flatnonzero(np.diff(np.r_[False, mask, False].astype(int)))
    return list(zip(edges[::2], edges[1::2]))


def windows(signal):
    padded = np.pad(signal, (0, (-len(signal)) % FRAME))
    rms = np.sqrt(np.mean(padded.reshape(-1, FRAME) ** 2, axis=1))
    db = 20 * np.log10(rms + 1e-8)
    gate = max(-53, min(-28, float(np.percentile(db, 85)) - 24))
    active = runs(db > gate)
    groups = []
    for a, b in active:
        if groups and a - groups[-1][1] < 18 and b - groups[-1][0] < 280:
            groups[-1][1] = b
        else:
            groups.append([a, b])
    for a, b in groups:
        for start in range(a, b, 280):
            stop = min(start + 280, b)
            if stop - start < 5:
                continue
            levels = db[start:stop]
            peaks = runs(levels > max(gate, float(np.percentile(levels, 85)) - 8))
            pulses = [(x, y) for x, y in peaks if 2 <= y-x <= 14]
            yield start, stop, pulses


def estimated_pitch(frame):
    """First credible periodic peak in a short window; unvoiced frames have no pitch."""
    frame = frame.astype(np.float64)
    frame -= frame.mean()
    if np.sqrt(np.mean(frame ** 2)) < 0.008:
        return None
    # Normalize each lag by the energies of the two compared slices.
    lags = np.arange(RATE // 550, RATE // 65 + 1)
    corr = np.correlate(frame, frame, mode='full')[len(frame) - 1:]
    energy = np.r_[0., np.cumsum(frame * frame)]
    scores = corr[lags] / np.sqrt(np.maximum(1e-12, energy[-lags-1] * (energy[-1] - energy[lags])))
    peaks = np.flatnonzero((scores[1:-1] > scores[:-2]) & (scores[1:-1] >= scores[2:])) + 1
    if not len(peaks):
        return None
    credible = peaks[scores[peaks] >= max(0.72, 0.9 * scores[peaks].max())]
    if not len(credible):
        return None
    peak = credible[0]
    left, center, right = scores[peak-1:peak+2]
    offset = 0.5 * (left - right) / (left - 2 * center + right)
    return RATE / (lags[peak] + offset)


def pitch_contour(signal):
    """Describe coarse pitch movement in time order, omitting unreliable intervals."""
    window = RATE // 25
    count = min(10, max(3, round(len(signal) / (RATE * 0.1))))
    cells = [[] for _ in range(count)]
    for start in range(0, len(signal) - window + 1, FRAME):
        pitch = estimated_pitch(signal[start:start+window])
        if pitch is not None:
            cells[(start + window // 2) * count // len(signal)].append(pitch)
    medians = [round(float(np.median(cell))) if len(cell) >= 2 else None for cell in cells]
    return medians if sum(v is not None for v in medians) >= 3 else None


def main():
    raw = subprocess.run(['ffmpeg','-nostdin','-v','error','-i',sys.argv[1],'-map','0:a:0',
                          '-t','120','-ac','1','-ar',str(RATE),'-f','f32le','pipe:1'],
                         capture_output=True,timeout=45)
    if raw.returncode:
        raise ValueError('El clip no contiene una pista de audio decodificable.')
    signal = np.frombuffer(raw.stdout, dtype='<f4')
    groups = list(windows(signal)) if len(signal) else []
    if not groups:
        return []
    sys.path[:0] = [str(ROOT/'dependencies'),str(ROOT/'lipsync/latentsync/whisper')]
    import torch
    import whisper
    torch.set_num_threads(4)
    weights = ROOT/'models/latentsync/whisper/tiny.pt'
    if hashlib.sha256(weights.read_bytes()).hexdigest() != '65147644a518d12f04e32d6f3b26facc3f8dd46e5390956a9424a650c0ce22b9':
        raise ValueError('No se pudo verificar el modelo local de transcripción.')
    model = whisper.load_model(str(weights), device='cpu')
    result = []
    for a,b,pulses in groups:
        audio = signal[max(0,a-5)*FRAME:(b+5)*FRAME].copy()
        mel = whisper.log_mel_spectrogram(whisper.pad_or_trim(audio))
        decoded = whisper.decode(model, mel, whisper.DecodingOptions(fp16=False,language=None,temperature=0,without_timestamps=True,sample_len=100))
        text = decoded.text.strip() if decoded.no_speech_prob < .75 and decoded.avg_logprob > -1.5 else ''
        row = dict(start_ms=int(a*20),end_ms=int(min(b*20,len(signal)*1000//RATE)),text=text)
        contour = pitch_contour(signal[a*FRAME:b*FRAME])
        if contour:
            row['pitch_hz'] = contour
        if len(pulses)>=3:
            x,y=pulses[0][0],pulses[-1][1]
            rate=(len(pulses)-1)/((pulses[-1][0]-x)*.02)
            if 2.5<=rate<=9:
                row['burst']=dict(start_ms=int((a+x)*20),end_ms=int((a+y)*20),count=len(pulses),rate=round(rate,1))
            if 'burst' in row:
                event = signal[(a+x)*FRAME:(a+y)*FRAME]
                first = np.sqrt(np.mean(event[:len(event)//2]**2))
                last = np.sqrt(np.mean(event[len(event)//2:]**2))
                row['burst']['contour'] = ('más intenso al principio' if first>1.4*last else
                                           'más intenso al final' if last>1.4*first else 'intensidad similar al principio y al final')
        result.append(row)
    return result


if __name__=='__main__':
    try:
        print(json.dumps(main(),ensure_ascii=True))
    except ValueError as error:
        print(str(error),file=sys.stderr)
        sys.exit(2)
