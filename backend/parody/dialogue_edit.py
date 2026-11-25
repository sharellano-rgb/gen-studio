"""Frame-accurate dialogue edits for Ref2VA's coupled native audio and video."""
import math
import subprocess
from array import array
from statistics import fmean


def envelope(path):
    pcm = subprocess.run(
        ['ffmpeg', '-nostdin', '-v', 'error', '-i', str(path), '-map', '0:a:0', '-t', '6',
         '-af', 'highpass=f=90,lowpass=f=3500', '-ac', '1', '-ar', '8000', '-f', 's16le', '-'],
        capture_output=True, check=True, timeout=20,
    ).stdout
    samples = array('h', pcm)
    return [math.sqrt(sum(x*x for x in samples[i:i+80])/80)
            for i in range(0, len(samples)-79, 80)]


def similarity(a, b):
    x, y = fmean(a), fmean(b)
    covariance = sum((i-x)*(j-y) for i, j in zip(a, b))
    power = sum((i-x)**2 for i in a) * sum((j-y)**2 for j in b)
    return covariance / math.sqrt(power) if power else 0


def native_silence(native, start, end, line):
    """When the voice is re-performed, cut only near-digital silence."""
    full = [[start,end]]
    if not native or max(native)<500:
        return full
    floor = max(4,min(28,sorted(native)[int(.9*len(native))]*.012))
    active = [i for i,x in enumerate(native) if x>floor]
    if len(active)<30:
        return full
    lead = max(12,line.get('pause_before_ms',0)/10)
    tail = max(12,line.get('pause_after_ms',0)/10)
    a,b=start,end
    if active[0]-start*100/24>65:
        a=max(a,math.floor((active[0]-lead)*24/100))
    if end*100/24-active[-1]>65:
        b=min(b,math.ceil((active[-1]+1+tail)*24/100))
    if b<=a:
        return full
    spans=[]
    for x,y in zip(active,active[1:]):
        if y-x<100:
            continue
        lo,hi=math.ceil((x+16)*24/100),math.floor((y-16)*24/100)
        if a<lo<hi<b:
            spans.append([a,lo])
            a=hi
    return spans+[[a,b]]


def decide(native, guide, start_frame, end_frame, line):
    full = [[start_frame, end_frame]]
    def result(ranges, status):
        return dict(version='ref2va-dialogue-edit-1', ranges=ranges, status=status,
                    removed_frames=end_frame-start_frame-sum(b-a for a,b in ranges))

    def unaligned():
        spans=native_silence(native,start_frame,end_frame,line)
        return result(spans,'silencio_nativo' if spans!=full else 'sin_coincidencia')

    if min(len(native),len(guide)) < 40 or not any(guide):
        return result(full, 'sin_evidencia')
    # Locate the guide in the generated soundtrack, including ambient lead-ins.
    # Require the start AND ending to agree before trimming any part of the take.
    gate = max(22, sorted(guide)[int(.92*len(guide))]*.025)
    voiced = [i for i, level in enumerate(guide) if level >= gate]
    if not voiced or voiced[-1]-voiced[0] < 35:
        return result(full, 'sin_evidencia')
    left, right = voiced[0], voiced[-1]+1
    # A synthesized guide can open with a quiet click followed by room tone.
    # Keep soft syllables adjacent to speech; skip only an isolated short onset.
    peak = max(guide)
    strong = next(i for i,x in enumerate(guide) if x>=.09*peak)
    if strong>=35 and max(guide[:strong-12],default=0)<.065*peak:
        early=[i for i in voiced if i<strong-12 and guide[i]>=.02*peak]
        if early and early[-1]-early[0]<18 and strong-early[-1]>=30:
            left=next((i for i in voiced if i>=strong-12),strong)
    ref = guide[left:right]
    if len(ref)>len(native)+4:
        return unaligned()
    padded = [0]*2 + native + [0]*2
    scores = [(similarity(ref,padded[i:i+len(ref)]), i-2)
              for i in range(len(padded)-len(ref)+1)]
    if not scores:
        return unaligned()
    score, first = max(scores)
    match = padded[first+2:first+2+len(ref)]
    edge = min(65,len(ref)//2)
    if (score < .77 or min(similarity(ref[:edge],match[:edge]),
                           similarity(ref[-edge:],match[-edge:])) < .63
            or any(s > score-.045 and abs(i-first)>12 for s,i in scores)):
        return unaligned()
    shift = first-left
    # Work in the untouched native timebase. Never cut into a breath or phoneme
    # to achieve a target duration. Preserve the user's explicit script holds.
    before = max(10,line.get('pause_before_ms',0)/10)
    after = max(10,line.get('pause_after_ms',0)/10)
    def floor_frame(t): return math.floor(t*24/100)
    def ceil_frame(t): return math.ceil(t*24/100)
    first_frame = max(start_frame, floor_frame(first-before))
    last_frame = min(end_frame, ceil_frame(right+shift+after))
    if first_frame>=last_frame:
        return result(full,'sin_dialogo_en_intervalo')
    # Only shorten internal rests when BOTH the guide and the actual soundtrack
    # are quiet. This retains laughs, reactions over ambience, and held vowels.
    spans = []
    cursor = first_frame
    native_gate = max(32,sorted(native)[int(.92*len(native))]*.045)
    for a,b in zip(voiced, voiced[1:]):
        if b-a < 90:
            continue
        lo,hi = a+shift+15,b+shift-15
        quiet = native[max(0,lo):min(len(native),hi)]
        cut_from,cut_to = ceil_frame(lo),floor_frame(hi)
        if (quiet and max(quiet)<native_gate and cursor<cut_from<cut_to<last_frame):
            spans.append([cursor,cut_from])
            cursor=cut_to
    spans.append([cursor,last_frame])
    return result(spans,'recortado' if spans!=full else 'conservado')


def match_spoken_groups(native,guide,start,end,line):
    """Allow pauses to move independently where several spoken groups still align."""
    peak=max(guide,default=0)
    if peak<500:return None
    groups=[]
    for i,x in enumerate(guide):
        if x<peak*.10:continue
        if not groups or i-groups[-1][1]>=15:groups.append([i,i+1])
        else:groups[-1][1]=i+1
    anchors=[]
    for a,b in groups:
        if b-a<25:continue
        ref=guide[a:b]
        scores=[(similarity(ref,native[i:i+len(ref)]),i) for i in range(len(native)-len(ref)+1)]
        if not scores:return None
        score,i=max(scores)
        if score<.925 or any(s>score-.04 and abs(j-i)>12 for s,j in scores):return None
        if anchors and i<anchors[-1][3]:return None
        anchors.append((a,b,i,i+len(ref)))
    if len(anchors)<2:return None
    a,_,first,_=anchors[0]
    _,b,_,last=anchors[-1]
    # Preserve unmatched cue material at both ends, including short soft words.
    leading=0 if max(guide[:max(0,a-16)],default=0)<peak*.10 else a
    trailing=len(guide)-b
    first=max(start,math.floor((first-leading-max(14,line.get('pause_before_ms',0)/10))*24/100))
    last=min(end,math.ceil((last+trailing+max(12,line.get('pause_after_ms',0)/10))*24/100))
    if first>=last:return None
    kept=[]
    for left,right in zip(anchors,anchors[1:]):
        if min(right[0]-left[1],right[2]-left[3])<75:continue
        lo,hi=left[3]+18,right[2]-18
        if (max(guide[left[1]+12:right[0]-12],default=peak)>.07*peak or
                max(native[lo:hi],default=peak)>.08*max(native)):continue
        cut,back=math.ceil(lo*24/100),math.floor(hi*24/100)
        if first<cut<back<last:
            kept.append([first,cut])
            first=back
    return kept+[[first,last]]


def analyze(video, voice, start, end, line, version=1):
    native,guide=envelope(video),envelope(voice)
    edit=decide(native,guide,start,end,line)
    if version==2 and edit['status']=='sin_coincidencia':
        ranges=match_spoken_groups(native,guide,start,end,line)
        if ranges and ranges!=[[start,end]]:
            return dict(version='ref2va-dialogue-edit-2',ranges=ranges,status='recortado',
                        removed_frames=end-start-sum(b-a for a,b in ranges))
    return edit


async def render(inputs, source, folder, stopped):
    from .montage_runtime import command, inspect
    ranges = inputs['dialogue_edit']['ranges']
    filters, pairs = [], []
    for i,(a,b) in enumerate(ranges):
        count = (b-a)*2000
        filters.extend([
            f'[0:v]trim=start_frame={a}:end_frame={b},setpts=PTS-STARTPTS[v{i}]',
            f'[0:a]aresample=48000,atrim=start_sample={a*2000}:end_sample={b*2000},'
            f'asetpts=PTS-STARTPTS,apad=whole_len={count},atrim=end_sample={count},'
            f'afade=t=in:ns=144,afade=t=out:ss={count-144}:ns=144[a{i}]',
        ])
        pairs.append(f'[v{i}][a{i}]')
    filters.append(''.join(pairs)+f'concat=n={len(ranges)}:v=1:a=1[v][mix]')
    filters.append('[mix]asplit[a][pcm]')
    output = folder/'segment.mp4'
    await command(['ffmpeg','-v','error','-nostdin','-n','-i',source,'-filter_complex',
                   ';'.join(filters),'-map','[v]','-map','[a]','-c:v','libx264','-crf','17',
                   '-pix_fmt','yuv420p','-c:a','aac','-b:a','192k',output,
                   '-map','[pcm]','-ac','2','-ar','48000','-c:a','pcm_s16le',folder/'audio.wav'],stopped)
    return await inspect(output,inputs['frames'],inputs['width'],inputs['height'],stopped)
