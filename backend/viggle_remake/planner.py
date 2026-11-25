# SPDX-License-Identifier: Apache-2.0
"""Official Viggle hop planner, with explicit per-shot resets and short-shot padding.
Derived from Viggle/Viggle-Remake mcp/viggle_remake_mcp/kit.py,
revision21665fa7a1e038e0a5312fb15f37b18d0f06f117 (Apache-2.0).
No H3/Pika one-frame guide; no frame interpolation or fades.
"""
from .profiles import CONTINUITY,LENGTHS
LENS=list(LENGTHS)
P=17

def _official_windows(cuts, n, maxlen, maxcuts):
    """Windows end on cuts, at most `maxlen` frames and, where the cuts allow, at most `maxcuts` cuts inside the render
    (the model drops and reorders short shots in a window with many cuts); `render` is 17k+5 long and may run past `own`
    (dropped frames). A shot longer than one window is not split into independent windows (the seam jumps the face)
    but continued: the next window is a hop, "prefix_of" the window before it, whose take's frames [prefix_at,
    prefix_at + P) are this render's first P frames (render[0] = own[0] - P). The last window is aligned to the clip
    end; when it is a hop the window before it gives up the frames it needs."""
    ls = [x for x in LENS if x <= maxlen]
    rnd = lambda m: min(x for x in ls if x >= m)  # noqa: E731
    out, s, p = [], 0, 0
    while True:
        r = s - p
        # a render that would run past the clip end is no candidate (the cond would come out shorter than num_frames)
        cand = [e for e in cuts if r + ls[0] <= e <= r + ls[-1] and s < e < n and r + rnd(e - r) <= n]
        if n - r <= ls[-1] and (not cand or sum(s < c < n for c in cuts) <= maxcuts):
            break
        ok = [e for e in cand if sum(r < c < r + rnd(e - r) for c in cuts) <= maxcuts]
        e = max(ok) if ok else min(cand) if cand else r + ls[-1]
        out.append({"render": [r, r + rnd(e - r)], "own": [s, e],
                    **({"prefix_of": len(out) - 1, "prefix_at": r - out[-1]["render"][0]} if p else {})})
        s, p = e, 0 if e in cuts else P
    ln = rnd(max(n - s + p, ls[0]))
    if p:  # the hop starts P frames into its render: own[0] moves back to n - ln + P, out of the window before it
        s = out[-1]["own"][1] = n - ln + P
        out.append({"render": [n - ln, n], "own": [s, n], "prefix_of": len(out) - 1, "prefix_at": n - ln - out[-1]["render"][0]})
    else:
        out.append({"render": [n - ln, n], "own": [s, n]})
    return out

def plan(frames,cuts=()):
    if type(frames) is not int or not 5<=frames<=2880:raise ValueError('Duración admitida: entre5 fotogramas y120 segundos.')
    if any(type(c) is not int or not 0<c<frames for c in cuts) or list(cuts)!=sorted(set(cuts)):raise ValueError('Cortes inválidos o desordenados.')
    result=[];bounds=[0,*cuts,frames]
    for shot,(begin,end) in enumerate(zip(bounds,bounds[1:])):
        n=end-begin
        # The official CLI rejects clips shorter than90 and its end-aligned
        # planner can start beforezero for a lone non-native window. Pad only
        # this shot's tail, then discard padding; never borrow another shot.
        local=([{'render':[0,next(x for x in LENGTHS if x>=n)],'own':[0,n]}] if n<=124 else _official_windows([],n,124,0))
        offset=len(result)
        for i,w in enumerate(local):
            a,b=w['render'];c,d=w['own'];entry={
              'index':len(result),'shot':shot,'render':[begin+a,begin+b],
              'source':[begin+a,min(begin+b,end)],'own':[begin+c,begin+d],
              'render_frames':b-a,'padding_frames':max(0,b-n),'trim_start':c-a,'keep':d-c,
              'context_frames':17 if 'prefix_of' in w else 0,'starts_at_cut':i==0 and shot>0}
            if 'prefix_of' in w:entry.update(prefix_of=offset+w['prefix_of'],prefix_at=w['prefix_at'])
            result.append(entry)
    assert sum(w['keep'] for w in result)==frames
    return {'version':CONTINUITY,'frames':frames,'fps':24,'cuts':list(cuts),'windows':result}

def validate(record):
    if record!=plan(record.get('frames'),record.get('cuts',[])):raise ValueError('Cambió el plan aceptado de ventanas Viggle.')
    return record
