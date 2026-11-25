"""Choose a full-frame appearance reference from an earlier reviewed two-shot."""
import json
import subprocess

from .head import digest
from .video_sequences import child_id, file_checked, save
from . import h3_swap_policy as swap

LEGACY = 'same-sequence-cast-1'
POLICY = 'same-sequence-cast-2'


def cast_signature(parameters):
    cards = parameters.get('direct_characters', [])
    ids = parameters.get('direct_reference_ids', [])
    hashes = parameters.get('direct_reference_hashes', [])
    if len(cards) != 2 or len(ids) != len(hashes):
        return None
    assets = dict(zip(ids, hashes))
    signature = {}
    for card in cards:
        refs, views = card.get('reference_ids', []), card.get('views', [])
        if not refs or len(refs) != len(views) or not all(assets.get(ref) for ref in refs):
            return None
        # Supplementary profiles can be refined between shots. The current
        # individual profiles still go to H3 alongside the ensemble photo.
        appearance = tuple((view, assets[ref]) for ref, view in zip(refs, views) if view != 'profile')
        if not appearance or not card.get('id'):
            return None
        signature[card['id']] = appearance
    return signature if len(signature) == 2 else None


def screen_order(info, cards):
    layout = info.get('cast_layout')
    if layout and sorted(layout.get('order', [])) == [0, 1]:
        return [cards[i]['id'] for i in layout['order']]
    track = info.get('body_track')
    if track:
        hidden = track['actor']
        orders = set()
        for frame, sample in track['samples'].items():
            peer = info['scan'][frame]['boxes'][1-hidden]
            if not peer:
                return None
            box = sample['box']
            orders.add((box[0]+box[2]) < (peer[0]+peer[2]))
        if len(orders) == 1:
            first = hidden if orders.pop() else 1-hidden
            return [cards[first]['id'], cards[1-first]['id']]
    return None


def compatible_target(info, *, manual_closeup=False):
    return info and info.get('confirmed_cast') and info.get('integral') and (
        info.get('body_track') or (manual_closeup and info.get('close_cast') and info.get('cast_layout'))
        or (info.get('cast_layout') and any(
            row.get('face_count') == 1 for row in info['scan'].values())))


def donor_frame(job, contract, order, *, trust_approved=False, target_positions=None):
    p = job['parameters']
    if screen_order(contract, p['direct_characters']) != order:
        return None
    review = job.get('direct_fidelity_review') or {}
    decision = job.get('direct_review') or {}
    if decision.get('human_review') == 'rejected':
        return None
    manual = decision.get('approved_result_sha256') == job['direct_result_sha256']
    checks = review.get('checks', {})
    actors = review.get('actors', [])
    if target_positions is not None and not manual:
        if ('fail' in checks.values() or any(
                checks.get(k) != 'pass' for k in ('identity', 'proportions', 'visible_wardrobe'))):
            return None
    if not manual and not (len(actors) == 2 and all(
            a['checks'].get('marked_identity', {}).get('verdict') == 'pass' for a in actors)
            and all(checks.get(k) == 'pass' for k in ('hairstyle', 'wardrobe', 'framing', 'integration'))):
        return None
    start = p['sequence_context']
    end = start + p['sequence_keep']
    candidates = []
    for frame in contract.get('review_frames', []):
        row = contract['scan'].get(str(frame), {})
        measured = review.get('measurements', {}).get(str(frame), [])
        boxes = row.get('boxes', [])
        if (start < frame < end and row.get('face_count') == 2 and len(boxes) == 2 and all(boxes)
                and ((trust_approved and manual and not measured) or (len(measured) == 2 and all(
                    m and abs(m['height_error']) <= .15 and m['center_error'] <= .15 for m in measured)))):
            first = int(boxes[0][0]+boxes[0][2] > boxes[1][0]+boxes[1][2])
            if p['direct_characters'][first]['id'] == order[0]:
                if target_positions is not None and not swap.framing_matches(
                        swap.face_positions(contract, frame, p['direct_characters'], order), target_positions):
                    continue
                candidates.append((min(b[3]-b[1] for b in boxes), frame))
    return max(candidates)[1] if candidates else None


def composition_sample(service, contract, frame):
    image=contract.get('scan',{}).get(str(frame),{}).get('image') or {}
    if not image.get('path') or not image.get('sha256'):
        return None
    try:
        path=file_checked(service,image['path'],image['sha256'])
    except (OSError,ValueError):
        return None
    result=subprocess.run(['ffmpeg','-v','error','-nostdin','-i',str(path),'-vf','scale=32:18:flags=area',
                           '-frames:v','1','-pix_fmt','rgb24','-f','rawvideo','pipe:1'],
                          capture_output=True,timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    return result.stdout if result.returncode==0 and len(result.stdout)==32*18*3 else None


def choose(service, job, info):
    p = job['parameters']
    folder = service.root/'jobs'/job['id']
    receipt = folder/'automatic-ensemble.json'
    if receipt.exists():
        chosen = json.loads(receipt.read_text('utf8'))['reference']
        if chosen:
            file_checked(service, chosen['path'], chosen['sha256'])
        return chosen
    chosen = None
    parent = service.store.job(p['sequence_parent_id'])
    signature = cast_signature(p)
    order = screen_order(info, p['direct_characters'])
    rank_by_view=job.get('direct_sequence_ensemble')==POLICY
    compare_framing = swap.applies(job)
    target_frame = info['review_frames'][0]
    if compare_framing:
        target_frame = max(info['review_frames'], key=lambda f: sum(bool(b) for b in info['scan'][str(f)]['boxes']))
    source_view=composition_sample(service,info,target_frame) if rank_by_view else None
    positions = swap.face_positions(info,target_frame,p['direct_characters'],order) if compare_framing and order else None
    candidates=[]
    for index in reversed(range(p['sequence_index'])):
        if not signature or not order or not parent or (compare_framing and not source_view):
            break
        donor = service.store.job(child_id(parent, index))
        if not donor or donor['status'] != 'completed' or not donor.get('result_available') or not donor.get('direct_result_sha256'):
            continue
        previous = donor['parameters']
        if (previous.get('sequence_parent_id') != parent['id'] or previous.get('sequence_index') != index
                or previous.get('direct_edit_policy') != p.get('direct_edit_policy') or cast_signature(previous) != signature):
            continue
        contract_file = service.root/'jobs'/donor['id']/'integral-contract.json'
        if not contract_file.exists():
            continue
        contract = json.loads(contract_file.read_text('utf8'))
        frame = donor_frame(donor, contract, order, trust_approved=rank_by_view, target_positions=positions)
        if frame is None:
            continue
        video = service.root/'results'/(donor['id']+'.mp4')
        if not video.is_file():
            continue
        view=composition_sample(service,contract,frame) if source_view else None
        distance=sum(abs(a-b) for a,b in zip(source_view,view))/len(view) if view else 256
        # Two faces on the same sides can belong to a completely different shot.
        # At thumbnail scale, require similar original scene colours AND geometry.
        if compare_framing and distance > 24:
            continue
        candidates.append((distance,-index,donor,video,frame))
    for distance,negative_index,donor,video,frame in sorted(candidates,key=lambda c:c[:2]):
        index=-negative_index
        if digest(video) != donor['direct_result_sha256']:
            continue
        image = folder/'ensemble-appearance.png'
        temp = image.with_suffix('.tmp.png')
        temp.unlink(missing_ok=True)
        result = subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-i',str(video),
                                 '-vf',f'select=eq(n\\,{frame})','-frames:v','1','-threads','1',str(temp)],
                                capture_output=True,timeout=60,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode or not temp.is_file() or not temp.stat().st_size:
            temp.unlink(missing_ok=True)
            continue
        temp.replace(image)
        chosen = dict(path=str(image), sha256=digest(image), source_job=donor['id'], source_index=index,
                      source_sha256=donor['direct_result_sha256'], frame=frame)
        break
    evidence = {'framing_policy': swap.VERSION, 'source_frame': target_frame} if compare_framing else {}
    if compare_framing and chosen:
        evidence['source_colour_distance'] = round(distance, 2)
    save(receipt, {'policy':job.get('direct_sequence_ensemble',LEGACY), 'reference':chosen, **evidence})
    return chosen
