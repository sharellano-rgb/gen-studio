"""Hard-cut candidates compared with local motion, using FFmpeg frame metrics."""
import re
from statistics import median


def parse_metrics(log):
    rows=[]
    for line in log.decode('utf-8',errors='replace').splitlines():
        frame=re.search(r'frame:(\d+)',line)
        if frame:rows.append({'frame':int(frame[1])})
        metric=re.search(r'lavfi\.scd\.(mafd|score)=([0-9.]+)',line)
        if metric and rows:rows[-1][metric[1]]=float(metric[2])
    return [row for row in rows if 'mafd' in row and 'score' in row]


def detect_cuts(rows,frames):
    candidates=[]
    for i,row in enumerate(rows):
        if not 0<row['frame']<frames or row['score']<3:continue
        neighbors=[other['mafd'] for other in rows[max(1,i-6):i+7]
                   if abs(other['frame']-row['frame'])>1]
        baseline=max(.25,median(neighbors) if neighbors else .25)
        ratio=row['mafd']/baseline
        candidates.append({**row,'local_motion':baseline,'motion_ratio':ratio,
                           'accepted':ratio>=3})
    # scdet can report the rebound immediately after a cut as another event.
    selected=[]
    for row in sorted((r for r in candidates if r['accepted']),key=lambda r:r['score'],reverse=True):
        if any(abs(row['frame']-frame)<=2 for frame in selected):
            row['accepted']=False;row['reason']='adjacent_peak'
        else:selected.append(row['frame'])
    return {'detector':'local-motion-v1','minimum_score':3,'minimum_motion_ratio':3,
            'cuts':sorted(selected),'candidates':candidates}
