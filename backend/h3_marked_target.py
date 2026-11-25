"""Shot-local spatial protection for confirmed H3 Direct character assignments."""
VERSION = 'confirmed-source-person-1'
FIELD = 'direct_target_policy'
CAST_FIELD = 'direct_marked_cast_policy'
CAST_VERSION = 'scene-cast-5'
REVIEW_FIELD = 'direct_marked_identity_review'
REVIEW_VERSION = 'held-facing-1'


def isolated_band(rows, mark, width, *, confirmed_body=False):
    """A fixed boundary is usable only while the same faces stay separated."""
    if not rows or not (0 <= mark['frame'] < len(rows)):
        return None
    marked = rows[mark['frame']]
    px, py = mark['point']
    # A click on the head may include hair or the ear outside the detector box.
    def near(box):
        x,y,r,b = box
        return x-.6*(r-x) <= px <= r+.6*(r-x) and y-.3*(b-y) <= py <= b+.4*(b-y)
    hits = [i for i,box in enumerate(marked) if near(box)]
    if any(len(row) not in (1,2) for row in rows):return None
    paired=[row for row in rows if len(row)==2]
    if len(paired)*2<len(rows):return None
    left_edge=max(a[2]+.12*(a[2]-a[0]) for a,b in paired)
    right_edge=min(b[0]-.12*(b[2]-b[0]) for a,b in paired)
    if len(marked)==2 and len(hits)==1:
        side=hits[0]
    elif not hits and (len(marked)==1 or confirmed_body):
        # A profile or the back of the selected head can drop out of the detector.
        # Require agreement with the opening target box and an uninterrupted bystander.
        bbox=mark.get('opening_bbox')
        if not bbox or not bbox[0]<=px<=bbox[2] or not bbox[1]<=py<=bbox[3]:return None
        if px<left_edge and bbox[2]<right_edge:side=0
        elif px>right_edge and bbox[0]>left_edge:side=1
        else:return None
        if len(marked)==2:
            # A confirmed click on hair, ear or torso can miss both face boxes.
            # Accept only when the selected face also lies in its opening person box.
            x,y,r,b=marked[side]
            if not (bbox[0] <= (x+r)/2 <= bbox[2] and bbox[1] <= (y+b)/2 <= bbox[3]):
                return None
    else:return None
    previous=None
    for row in rows:
        protected=row[1-side] if len(row)==2 else row[0]
        if (side==0 and protected[0]<right_edge-.13*(protected[2]-protected[0])) or (side==1 and protected[2]>left_edge+.13*(protected[2]-protected[0])):return None
        if previous and abs((protected[0]+protected[2]-previous[0]-previous[2])/2)>.5*(previous[2]-previous[0]):return None
        previous=protected
    if right_edge-left_edge < 8:
        return None
    split = 2*round((left_edge+right_edge)/4)
    return [0,split] if side==0 else [split,width]


def separated_cast(rows, marks, width):
    """Require distinct confirmed sides and a corridor between every observed face."""
    if len(marks)!=2 or not rows or any(len(row) not in (1,2) for row in rows):
        return None
    ordered=sorted(range(2),key=lambda i:marks[i]['point'][0])
    paired=[row for row in rows if len(row)==2]
    if 2*len(paired)<len(rows):return None
    # Infer the corridor from the observed faces. Clicks on hair need not be
    # symmetric about the corridor, particularly for a back-of-head selection.
    mid=(max(row[0][2] for row in paired)+min(row[1][0] for row in paired))/2
    sides=[[],[]]
    pairs=0
    for row in rows:
        seen=set()
        for box in row:
            if box[2]<mid:side=0
            elif box[0]>mid:side=1
            else:return None
            if side in seen:return None
            seen.add(side);sides[side].append(box)
        pairs+=len(seen)==2
    if pairs*2<len(rows) or not all(sides):return None
    for side,index in enumerate(ordered):
        mark=marks[index];x,y=mark['point'];frame=mark['frame']
        if not 0<=frame<len(rows):return None
        hits=[b for b in rows[frame] if b[0]-.35*(b[2]-b[0])<=x<=b[2]+.35*(b[2]-b[0])
              and b[1]-.2*(b[3]-b[1])<=y<=b[3]+.45*(b[3]-b[1])]
        if not hits and mark.get('opening_bbox'):
            # The UI accepts a point on the performer, including the jacket or torso.
            # Require a unique body assignment and independent face evidence.
            l,t,r,b=mark['opening_bbox']
            if not (l<=x<=r and t<=y<=b and (x>mid)==bool(side)):return None
            other_bounds=marks[ordered[1-side]].get('opening_bbox')
            if not other_bounds:return None
            ol,ot,orr,ob=other_bounds
            if ol<=x<=orr and ot<=y<=ob:return None
            visible=[box for box in rows[frame] if (box[0]>mid)==bool(side)]
            if visible:
                a,c,d,e=visible[0]
                # In the confirmed box and vertically aligned under this face.
                if not (l<=(a+d)/2<=r and t<=(c+e)/2<=b):return None
                torso=y>=e and a-.35*(d-a)<=x<=d+.35*(d-a)
                # A deep profile puts the detected face beside the center of its own jacket.
                central_jacket=(l+.28*(r-l)<=x<=r-.28*(r-l) and t+.4*(b-t)<=y<=t+.82*(b-t))
                if d-a<.65*(e-c) and y>=e and central_jacket:
                    torso=torso or abs(x-(a+d)/2)<=.85*(e-c)
                # A profile detector covers the visible face, not the back of the skull.
                # Accept an adjacent head click inside the unique confirmed body only.
                face_height=e-c
                profile_head=(c<=y<=e and d-a<.65*face_height
                              and a-.45*face_height<=x<=d+.45*face_height)
                if not (torso or profile_head):return None
            else:
                nearby=sorted((abs(i-frame),i) for i in range(max(0,frame-12),min(len(rows),frame+13))
                              if len(rows[i])==2)
                if not nearby:return None
                a,c,d,e=rows[nearby[0][1]][side]
                if not (a-.35*(d-a)<=x<=d+.35*(d-a) and c-.2*(e-c)<=y<=e+.45*(e-c)):return None
        elif len(hits)!=1 or (hits[0][0]>mid)!=bool(side):return None
    left=max(b[2] for b in sides[0]);right=min(b[0] for b in sides[1])
    if right-left<max(width*.06,48):return None
    split=2*round((left+right)/4)
    margin=2*int(min((right-left)/5,width*.025)/2)
    return {'order':ordered,'split':split,'margin':margin}


def slot_faces(boxes, layout):
    """Assign only faces wholly within the confirmed person's side."""
    assigned=[None,None]
    split=layout['split']
    for side,index in enumerate(layout['order']):
        hits=[box for box in boxes if (box[2]<=split if side==0 else box[0]>=split)]
        if len(hits)==1:
            assigned[index]=hits[0]
    return assigned


def reacquire_missing(boxes, assigned, actors, size):
    """Only reacquire an unclaimed detection uniquely inside one opening subject."""
    width,height=size
    candidates=[]
    for slot, person in enumerate(actors):
        bounds=person.get('bbox')
        if assigned[slot] or not bounds:
            candidates.append([])
            continue
        left,top,right,bottom=bounds
        hits=[]
        for box in boxes:
            if box in assigned:
                continue
            x=(box[0]+box[2])*500/width
            y=(box[1]+box[3])*500/height
            if left <= x <= right and top <= y <= top+.75*(bottom-top):
                hits.append(box)
        candidates.append(hits)
    return [current if current else (hits[0] if len(hits)==1 and
            sum(hits[0] in options for options in candidates)==1 else None)
            for current,hits in zip(assigned,candidates)]


def cast_failures(review, count, *, require_wardrobe=False):
    """An identity match alone cannot approve a failed source performance."""
    rows={row['actor']:row['checks'] for row in review.get('actors',[])}
    failed={}
    for actor in range(1,count+1):
        checks=rows.get(actor,{})
        if 'confirmed_absence' in checks:
            if checks['confirmed_absence']['verdict']!='pass':failed[actor]=['integration']
            continue
        reasons=[key for key in ('identity','head_motion','mouth_action','integration','marked_identity')
                 if checks.get(key,{}).get('verdict')=='fail']
        if require_wardrobe or 'visible_wardrobe' in checks:
            if (checks.get('visible_wardrobe',{}).get('verdict') not in ('pass','not_visible')
                    or checks.get('wardrobe',{}).get('verdict')=='fail'):
                reasons.append('visible_wardrobe')
        if 'marked_appearance' in checks:
            if checks['marked_appearance'].get('verdict')!='pass':reasons.append('unverified_identity')
        elif not checks or all(checks.get(key,{}).get('verdict')!='pass' for key in ('identity','marked_identity')):
            reasons.append('unverified_identity')
        if reasons:failed[actor]=reasons
    return failed


def missing_replacements(review):
    missing=[]
    for row in review.get('actors',[]):
        checks=row['checks']
        verified=checks.get('marked_identity',{}).get('verdict')
        if ('marked_appearance' in checks and checks['marked_appearance'].get('verdict')!='pass'
                or verified=='fail' or (verified!='pass' and checks.get('identity',{}).get('verdict')=='fail')):
            missing.append(row['actor'])
    return missing


def front_state(faces, band):
    """A conservative near-frontal head cue, not an estimate of eye direction."""
    visible = [face for face in faces if band[0] <= face.bbox[0] < face.bbox[2] <= band[1]]
    if len(visible) != 1:
        return 'missing' if not visible else 'ambiguous'
    face = visible[0]
    a, b, nose = face.kps[:3]
    dx, dy = b - a
    width = face.bbox[2] - face.bbox[0]
    if dx < .24 * width or abs(dy) > .6 * dx:
        return 'nonfrontal'
    offset = abs(((nose[0]-(a[0]+b[0])/2)*dx + (nose[1]-(a[1]+b[1])/2)*dy) / (dx*dx+dy*dy))
    return 'frontal' if offset <= .38 else 'nonfrontal'


def facing_mismatch(source, result):
    """Flag a sustained frontal reveal during a visually established off-camera hold."""
    if not source or len(source) != len(result) or 'nonfrontal' not in source:
        return []
    failures = []
    run = []
    for frame, (before, after) in enumerate(zip(source, result)):
        if before in ('nonfrontal', 'missing') and after == 'frontal':
            run.append(frame)
        else:
            if len(run) >= 3:
                failures.extend(run)
            run = []
    if len(run) >= 3:
        failures.extend(run)
    return failures


def profile_guide_path(ticks,options):
    """Select a forward-moving donor sequence for a sustained profile."""
    if not ticks or len(ticks)!=len(options) or any(not pool for pool in options):return []
    states={frame:(cost,[frame]) for cost,frame in options[0]}
    for a,b,pool in zip(ticks,ticks[1:],options[1:]):
        gap=b-a
        if gap<=0:return []
        next_states={}
        for cost,k in pool:
            paths=[(score+cost+.6*abs((k-prev)/gap-1),path+[k])
                   for prev,(score,path) in states.items() if .45*gap<=k-prev<=1.75*gap]
            if paths:next_states[k]=min(paths)
        states=next_states
    return min(states.values())[1] if states else []


def focused_box(box,size):
    """Normalized detector box for Qwen's existing padded detail-crop reader."""
    if not box:return None
    return [min(1000,max(0,round(v*1000/size[i%2]))) for i,v in enumerate(box)]
