"""Decisions shared by new source-driven H3 Direct character swaps."""
from pathlib import Path
from .h3_face_policy import FIELD as FACE_POLICY, GUIDED_VERSION, creation_policy


FIELD = 'direct_swap_policy'
VERSION = 'source-shot-1'


def applies(job):
    return job.get(FIELD) == VERSION


def pin(state, parent):
    # Only original, assigned Object Swap requests can enroll. In particular,
    # replaying a recorded graph and continuing a historical job are immutable.
    p = state['parameters']
    if (state.get(FACE_POLICY) != GUIDED_VERSION or p.get('operation') not in (None, 'long_character')
            or creation_policy(p).get(FACE_POLICY) != GUIDED_VERSION):
        return
    if parent is None or applies(parent):
        state[FIELD] = VERSION


def body_region(subject, frame, size):
    """The box describes the visible body at this instant, never a nearby frame."""
    bounds = subject.get('visible_body_boxes', {}).get(str(frame))
    if not bounds:
        return None
    left,top,right,bottom = [round(value * size[i % 2] / 1000) for i, value in enumerate(bounds)]
    return [left,top,right,bottom] if right>left and bottom>top else None


def review_samples(frames, marks):
    choices = set(frames) | set(marks)
    if len(choices)<=6:
        return sorted(choices)
    selected = {min(frames),max(frames),*sorted(set(marks))[:4]}
    while len(selected)<6:
        selected.add(max(choices-selected,key=lambda f:(min(abs(f-other) for other in selected),f)))
    return sorted(selected)


def framing_matches(previous, current):
    """Comparable paired face positions, in normalized source coordinates.

    A genuinely hidden face supplies no constraint. At least one face in the
    current frame must be measurable and belong to the corresponding slot.
    """
    compared = 0
    for prior, now in zip(previous, current, strict=True):
        if now is None:
            continue
        if prior is None:
            return False
        px, py, ph = prior
        x, y, h = now
        if abs(x-px) > .12 or abs(y-py) > .12 or min(h, ph) < .72 * max(h, ph):
            return False
        compared += 1
    return compared > 0


def face_positions(info, frame, cards, order):
    size = info.get('size') or []
    boxes = info.get('scan',{}).get(str(frame),{}).get('boxes',[])
    if len(size)!=2 or min(size)<=0 or len(boxes)!=len(cards):
        return [None]*len(order)
    width,height = size
    by_card = {}
    for card, box in zip(cards, boxes, strict=True):
        by_card[card['id']] = None if not box or box[3] <= box[1] else (
            (box[0]+box[2])/(2*width), (box[1]+box[3])/(2*height), (box[3]-box[1])/height)
    return [by_card[ident] for ident in order]


def frontal_sheet(card, scan):
    views = card.get('views') or []
    return (views[:1] == ['front'] and all(v=='profile' for v in views[1:])
            and bool(scan) and all(row.get('facing')=='frontal' for row in scan.values()))


def identity_sample(prior, contract, index):
    """A comparison face must be attributable to this actor in this saved revision."""
    decision = prior.get('direct_review') or {}
    sha = prior.get('direct_result_sha256')
    if not sha or decision.get('human_review') == 'rejected':
        return None
    manual = decision.get('approved_result_sha256') == sha
    review = prior.get('direct_fidelity_review') or {}
    checks = next((a['checks'] for a in review.get('actors',[]) if a['actor']==index+1),{})
    if not manual and (any(c['verdict']=='fail' for c in checks.values()) or any(
            checks.get(k,{}).get('verdict') != 'pass' for k in ('identity','hairstyle','wardrobe','integration'))):
        return None
    if not contract.get('size') or 'start' not in contract or 'end' not in contract:
        return None
    w,h = contract['size']
    samples = []
    for frame in contract['review_frames']:
        boxes = contract['scan'].get(str(frame),{}).get('boxes',[])
        if not contract['start'] <= frame < contract['end'] or index>=len(boxes) or not boxes[index]:
            continue
        box = boxes[index]
        if box[3]-box[1] < .08*h:
            continue
        metrics = review.get('measurements',{}).get(str(frame),[])
        measured = metrics[index] if len(metrics)>index else None
        if measured and (abs(measured['height_error'])>.15 or measured['center_error']>.25):
            continue
        if manual or measured:
            bbox = [round(max(0,min(1000,value*1000/length))) for value,length in zip(box,(w,h,w,h),strict=True)]
            samples.append((box[3]-box[1], frame, bbox))
    if samples:
        _,frame,bbox = max(samples)
        return frame,bbox
    return None


def add_directions(graph, directions):
    """Keep explicit acting directions ahead of long automatic captions."""
    encoder = next(n['inputs'] for n in graph.values() if n['class_type'] == 'MiniMaxH3ReferenceToVideo')
    prompt = encoder['prompt']
    additions = []
    for item in directions:
        line = (item or '').strip()
        if line and line not in prompt and line not in additions:
            additions.append(line)
    if additions:
        prefix = 'r34l1sm , ultra realistic video. '
        native = prefix if prompt.startswith(prefix) else ''
        encoder['prompt'] = native + '\n'.join(additions) + '\n' + prompt[len(native):]


def added_mouth_cycle(samples):
    """Three visible instants must establish an added opening/closing cycle."""
    run = []
    for sample in samples:
        if sample.source not in ('closed','slight_gap') or sample.result in ('hidden','uncertain'):
            run.clear()
            continue
        wide = sample.result == 'wide_open'
        if not run or run[-1][0] != wide:
            run.append((wide, sample.frame))
            if len(run) == 3:
                return [frame for _,frame in run]
    return []


def performance_failures(review, index):
    """Require a documented source/result discrepancy, not a missing measurement."""
    rows = review.get('actors') or []
    checks = next((r['checks'] for r in rows if r['actor'] == index), {})
    if 'confirmed_absence' in checks:
        return [] if checks['confirmed_absence']['verdict']=='pass' else ['integration']
    return [name for name in ('contacts', 'motion', 'extra_mouth_motion')
            if (check := checks.get(name, {})).get('verdict') == 'fail'
            and check.get('frames') and check.get('source_observation') and check.get('result_observation')]


def needs_source_review(review, failures):
    if not failures or review.get('drift') or 'fail' in review.get('checks',{}).values():
        return False
    rows = {r['actor']:r['checks'] for r in review.get('actors',[])}
    return all(reasons == ['visible_wardrobe'] and
               rows.get(i,{}).get('visible_wardrobe',{}).get('reason') == 'unlocated_source'
               for i,reasons in failures.items())


def tail_inputs(service, job):
    """A short continuation can reuse only the verified end of its current shot."""
    from .guided_composition import local_path
    from .video_sequences import child_id
    p = job['parameters']
    if (not applies(job) or p.get('sequence_context') != 1 or not 4 <= p.get('sequence_keep', 0) <= 24
            or not p.get('sequence_parent_id') or not p.get('sequence_previous_id') or p.get('standalone_segment_attempt')
            or p.get('motion_head_guide') or p.get('direct_continuity_guide')):
        return None
    parent = service.store.job(p['sequence_parent_id'])
    index = p['sequence_index']
    if (not parent or index < 1 or child_id(parent,index) != job['id']
            or child_id(parent,index-1) != p['sequence_previous_id']):
        return None
    prior = service.store.job(p['sequence_previous_id'])
    if not prior or prior['status'] != 'completed' or not prior.get('result_available'):
        return None
    previous = prior['parameters']
    frame = p['sequence_previous_frame']
    if (previous.get('sequence_parent_id') != parent['id'] or previous.get('sequence_index') != index-1
            or previous.get('sequence_shot') != p['sequence_shot']
            or previous['sequence_start_frame'] + frame != p['sequence_start_frame']
            or previous['sequence_context'] + previous['sequence_keep'] - 1 != frame):
        return None
    # A changed cast, costume, goal, or user edit calls for a fresh performance.
    for key in ('direct_characters','direct_reference_ids','direct_reference_hashes','direct_edit_policy',
                'resolution','direct_direction','direct_global_goal','direct_visual_target_proofs',
                'engine','direct_preset','direct_character_version','direct_video_only','steps','lora_strength'):
        if previous.get(key) != p.get(key):
            return None
    if {k:v for k,v in p.items() if k.startswith('nsfw_')} != {
            k:v for k,v in previous.items() if k.startswith('nsfw_')}:
        return None
    sha = prior.get('direct_result_sha256')
    decision = prior.get('direct_review') or {}
    if not sha or decision.get('human_review') == 'rejected':
        return None
    manual = decision.get('approved_result_sha256') == sha
    fidelity = prior.get('direct_fidelity_review') or {}
    checked = fidelity.get('status') == 'checked' and all(
        fidelity.get('checks', {}).get(k) == 'pass' for k in ('identity','motion','integration','wardrobe'))
    if not (manual or checked):
        return None
    source = service.store.asset(previous['video_id'])
    if not source or not previous.get('direct_source_sha256'):
        return None
    return {'previous':str(local_path(service.root, service.root/'results'/(prior['id']+'.mp4'))),
            'previous_sha256':sha, 'previous_frame':frame, 'visible':p['sequence_keep'],
            'previous_source':str(local_path(service.root, service.root/'inputs'/source['filename'])),
            'previous_source_sha256':previous['direct_source_sha256']}


async def verify_tail(service, job, tail):
    from .h3_integral_review import ask, Check
    decision = await ask(service, job, 'occlusion-tail-review', Check,
        'Three chronological pairs of full frames. Each odd image is the original, each even image is '
        'the proposed continuation of the approved substitution at the same instant. Pass only for a '
        'fixed camera with opaque foreground surfaces progressively hiding the existing performer(s). '
        'Require the pose, limb contacts, depth ordering, scene and any visible acting to agree with the '
        'source and the replacement appearance to stay consistent with the opening result. Fail for a '
        'new visible person, slide or portrait overlay, lost performance, new camera view, or pasted '
        'edge. Use uncertain whenever the evidence is insufficient. Explain briefly in Spanish.',
        {'paired_frames':[0,(tail['visible_frames']+1)//2,tail['visible_frames']], 'context_frames':1,
         'pair_order':['original scene','proposed continuation']},
        [Path(p) for p in tail['review_pairs']])
    return decision.verdict == 'pass'
