"""Native H3 passes for two confirmed, spatially separated subjects in one camera shot."""
import json
from pathlib import Path
from . import direct_audio, nsfw_adapters
from .guided_head import phase
from .video_sequences import save


def sustained_profile(actions):
    """Keep camera visibility explicit when all source observations agree."""
    import re
    if len(actions)<2:return []
    sides=set()
    for moment in actions:
        head=moment['head_gaze_face'].lower()
        matches=set(re.findall(r'\b(left|right)\b',head))
        if len(matches)!=1:return []
        sides.update(matches)
        visibility=head+' '+moment.get('uncertainty','').lower()
        if not any(term in visibility for term in ('not observable','not visible','unobservable',
                   'obscured','hidden','out of view','cannot be')):return []
        if not any(term in visibility for term in ('profile','back of the head','turned away')):return []
    if len(sides)!=1 or not any(any(term in m['head_gaze_face'].lower() for term in
            ('face is obscured','face is not visible','face remains hidden','back of the skull','back of the head'))
            for m in actions):return []
    d=sides.pop()
    cues=[f'The head is turned in profile to the {d}, showing the ear and the back of the skull. The face is not visible, so gaze and expression are unknown.',
          f"The head maintains a {d}-facing profile orientation. The face remains hidden from the camera's view.",
          f'The head is turned to the {d} in profile. The face is not visible, leaving the expression and gaze unknown.',
          f'The head continues to face {d}, keeping the face out of view. No changes in head angle are apparent.',
          f'The head is still turned to the {d} in profile. The face is not visible to the camera.',
          f'The head remains in a {d}-facing profile at the final sample. The face is not visible.']
    return [{'frame':m['frame'],'head':cues[round(i*5/(len(actions)-1))]} for i,m in enumerate(actions)]


async def verify_partner_profile(service,job,info,index,clip,label,*,full_preview=False):
    from .h3_face_fidelity import cpu
    layout=info['cast_layout'];width,_=info['size']
    prepared=info['prepared'];source=prepared['video']
    band=[0,layout['split']] if layout['order'][0]==index else [layout['split'],width]
    report=await cpu(service,job,label,{'mode':'cast-profile','size':list(info['size']),'indices':[],
        'source':source,'source_sha256':prepared['files'][source],'path':clip['path'],
        'path_sha256':clip['sha256'],'start':info['start'],'end':info['end'],'band':band})
    if report['conflicts']:
        if full_preview:
            from .video_sequences import record_rejected_preview
            record_rejected_preview(service,job,clip)
        name=job['parameters']['direct_characters'][index]['name']
        raise ValueError(f'{name} mira hacia la cámara mientras permanece de perfil en el original. '
                         'Se conservan las tomas anteriores.')


async def render_side(service,job,p,info,prepared,names,frames,side,index,label,full_scene,*,lock_profile=False):
    from .direct_character import cpu,graph
    layout=info['cast_layout'];width,height=info['size']
    split,margin=layout['split'],layout['margin']
    source,_=direct_audio.prepared_input(p,prepared)
    actor=info['actors'][index]
    bounds=[0,split+margin] if side==0 else [split-margin,width]
    if full_scene:
        video=names['video']
    else:
        masked=await cpu(service,job,'marked-input',dict(source=source,source_sha256=prepared['files'][source],
            band=bounds,width=width,height=height,frames=frames),label=label+'-source')
        path=Path(masked['video'])
        sha=masked['files'][str(path)]
        receipt=service.root/'jobs'/job['id']/(label+'-upload.json')
        if receipt.exists():
            uploaded=json.loads(receipt.read_text(encoding='utf-8'))
            if uploaded['sha256']!=sha:raise ValueError('Cambió el vídeo aislado de un personaje.')
            video=uploaded['name']
        else:
            with path.open('rb') as stream:
                upload=await service.post(service.settings['comfy_url'],'/upload/image',files={'image':(sha+'.mp4',stream)},
                    data={'overwrite':'false','subfolder':f'gen_studio/{job["id"]}/cast'})
            video='/'.join(filter(None,[upload.get('subfolder'),upload['name']]))
            save(receipt,{'name':video,'sha256':sha})
    indices=list(dict.fromkeys([actor['appearance_picture'],actor['face_picture']]))
    refs=[names['references'][i] for i in indices]
    face=indices.index(actor['face_picture'])+1
    appearance=actor['appearance']
    prompt=(f'In <Video 1>, replace the isolated performer with the person in <Picture {face}> throughout. '
            'Only that person is present in the visible part of the frame. Identity, hairstyle, face and '
            'clothing come from the reference. Recreate them even during profiles and turns away. '
            'Keep the original framing, scene lighting, foreground occlusion, gaze, and precise speaking '
            'and listening movements. Preserve the destination crop and position. '
            f'Clothing and accessories from <Picture 1>: {appearance["clothing"]} {appearance["accessories"]}. '
            f'Face and hair: {appearance["face"]} {appearance["hair_details"]}.')
    if full_scene:
        position='left' if side==0 else 'right'
        prompt=(f'Use <Video 1> as the moving scene. Edit ONLY the {position} performer '
                f'({actor["confirmed_target"]}). Keep the scene partner, set and source camera in place. '
                'Match which cheek, ear or back of the head is visible at each instant. '
                'When the face is hidden, show the hair and back of head. Follow the source mouth and '
                'eyeline toward the scene partner, including direction changes. The reference supplies '
                'identity and wardrobe only. Never stage the photograph or copy its pose or background. '
                f'Use <Picture {face}> for identity and <Picture 1> for the visible wardrobe. '
                f'{appearance["clothing"]} {appearance["hair_details"]}')
    if info.get('feedback'):prompt+=' '+info['state'].get('plan',{}).get('special_notes','')
    directions=[{'frame':s['frame'],'head':s['head_gaze_face']} for s in actor.get('source_actions',[])]
    if lock_profile:
        directions=sustained_profile(actor.get('source_actions',[])) or directions
    if directions:prompt+=' Source action at matching frames: '+json.dumps(directions)
    native=graph({**p,'prompt':prompt},video,refs,job['id'])
    native=nsfw_adapters.apply_optional_adapters(native,p)
    native['28']['inputs']['filename_prefix']=f'gen_studio/{job["id"]}/{label}'
    clip=await phase(service,job,'direct-'+label,native,'28','.mp4')
    return clip


async def render(service,job,p,info,prepared,names,frames):
    from .direct_character import cpu
    layout=info['cast_layout'];width,height=info['size']
    split,margin=layout['split'],layout['margin']
    sides=[]
    for side,index in enumerate(layout['order']):
        clip=await render_side(service,job,p,info,prepared,names,frames,side,index,
                               f'cast-side-{index}',job.get('direct_marked_cast_policy')=='scene-cast-5')
        if not clip:return None
        sides.append({'path':clip['path'],'sha256':clip['sha256']})
    joined=await cpu(service,job,'join-cast',dict(sides=sides,width=width,height=height,frames=frames,
                   split=split,margin=margin,**({"scene_source":prepared["video"],"scene_hash":prepared["files"][prepared["video"]]}
                   if job.get("direct_marked_cast_policy") in ("protected-cast-4","scene-cast-5") else {})))
    return {'path':joined['video'],'sha256':joined['files'][joined['video']]}

def joint_acting_candidate(review):
    from .h3_marked_target import cast_failures
    rows=review.get('actors',[])
    return ({r['actor'] for r in rows}=={1,2} and len(rows)==2 and
            bool(cast_failures(review,2)) and
            all(r['checks'].get('identity',{}).get('verdict')=='pass' and
                r['checks'].get('marked_identity',{}).get('verdict') in ('pass','uncertain') and
                r['checks'].get('integration',{}).get('verdict')=='pass' for r in rows))


def timed_cast_graph(job,p,info,names,anchors):
    from .direct_character import graph
    from .h3_face_fidelity import apply
    prompt=('Recreate the original shot in <Video 1>. Use the timed pictures for BOTH performers, '
            'their identity, clothing, scale, gaze and expression at each indicated time. '
            'Follow the source action between those times. Keep both people in their original places '
            'at their original sizes. Preserve the camera distance and background throughout. ')
    for side,index in enumerate(info['cast_layout']['order']):
        card=p['direct_characters'][index]
        prompt+=f"The performer on the {('left','right')[side]} is {card['name']} from <Picture {info['actors'][index]['face_picture']+1}>. "
    native=apply(graph({**p,'prompt':prompt},names['video'],names['references'],job['id']),anchors)
    native=nsfw_adapters.apply_optional_adapters(native,p)
    native['28']['inputs']['filename_prefix']=f'gen_studio/{job["id"]}/cast-timed-acting'
    return native


def cast_framing_matches(review):
    """Check the visible faces' scale and placement separately for both cast members."""
    seen=set()
    for row in review.get('measurements',{}).values():
        for index,item in enumerate(row):
            if item is None:continue
            if abs(item['height_error'])>.15 or item['center_error']>.25:return False
            seen.add(index)
    return seen=={0,1}


def joint_issues(review):
    issues=[]
    for number in (1,2):
        row=next((r for r in review.get('actors',[]) if r['actor']==number),{})
        if row.get('checks',{}).get('marked_identity',{}).get('verdict')!='pass':
            issues.append(f'identidad sin confirmar en la persona marcada {number}')
    if not cast_framing_matches(review):
        issues.append('escala o posición facial sin confirmar')
    return issues


def mouth_only_notice(review, failures):
    """Keep a mouth discrepancy for review after both marked subjects and framing are confirmed."""
    return (bool(failures) and all(reasons==['mouth_action'] for reasons in failures.values())
            and not joint_issues(review) and all(
                row['checks'].get(key,{}).get('verdict')=='pass'
                for row in review['actors'] for key in ('identity','head_motion','integration','motion')))


def retry_actor(review, order):
    """Use the joint shot only if it retained the target's head movement and the partner's identity."""
    rows={row['actor']-1:row['checks'] for row in review.get('actors',[])}
    if len(order)!=2 or set(rows)!=set(order):return None
    for index,partner in (order,order[::-1]):
        checks=rows[index]
        other=rows[partner]
        if (checks.get('head_motion',{}).get('verdict')=='pass'
                and any(checks.get(key,{}).get('verdict')=='fail' for key in ('identity','marked_identity'))
                and all(other.get(key,{}).get('verdict')=='pass' for key in ('identity','marked_identity','head_motion'))):
            return index
    return None


def mouth_repair_actor(review,order):
    """Repair a missed expression only when it is the sole outstanding cast failure."""
    from .h3_marked_target import cast_failures
    failures=cast_failures(review,2)
    if len(failures)!=1 or sorted(order)!=[0,1]:
        return None
    number,reasons=next(iter(failures.items()))
    rows={row['actor']:row['checks'] for row in review['actors']}
    target=rows[number]
    if (reasons==['mouth_action'] and target.get('head_motion',{}).get('verdict')=='pass'
            and target.get('marked_identity',{}).get('verdict')=='pass'):
        return number-1
    return None


def add_motion_reference(native, source):
    """Supply aligned original frames as a second, motion-only visual guide."""
    encoder,= (n for n in native.values() if n['class_type']=='MiniMaxH3ReferenceToVideo')
    inputs=encoder['inputs']
    if 'ref_videos.ref_video_1' in inputs:
        raise ValueError('La segunda guía de movimiento ya está ocupada.')
    native['cast-source-motion']={'class_type':'LoadVideo','inputs':{'file':source}}
    native['cast-source-frames']={'class_type':'GetVideoComponents','inputs':{'video':['cast-source-motion',0]}}
    inputs['ref_videos.ref_video_1']=['cast-source-frames',0]


def expression_frames(review,index,count):
    """Confirm a donor for this actor and sample observations plus the intervals between them."""
    row=next((r for r in review.get('actors',[]) if r['actor']==index+1),None)
    if not row:return []
    checks=row['checks']
    if any(checks.get(k,{}).get('verdict')!='pass' for k in
           ('identity','marked_identity','head_motion','mouth_action','integration')):return []
    observed=sorted({f for f in checks['mouth_action'].get('frames',[]) if 0<=f<count})
    if len(observed)<2:return []
    # Review usually samples four instants; add points inside the two latest gaps
    # to hold a change of expression between the observed instants.
    midpoints=[(a+b+1)//2 for a,b in zip(observed,observed[1:]) if b-a>1]
    extra=max(0,6-len(observed))
    return sorted(set(observed+midpoints[-extra:])) if extra else observed


async def expression_guides(service,job,p,info,names,index,donor,donor_review,frames):
    from .direct_character import cpu
    from .h3_face_fidelity import upload
    count=min(p.get('sequence_keep',p['frames']),p['frames'])
    positions=expression_frames(donor_review,index,count)
    if not positions:return []
    layout=info['cast_layout']
    partner=1-index
    side=layout['order'].index(partner)
    # Keep the expression donor on the target's side; rebuild the partner in
    # isolation so the donor's rejected partner does not enter the guide.
    partner_clip=await render_side(service,job,p,info,info['prepared'],names,frames,side,partner,
                                   f'cast-side-expression-partner-{partner}',False,lock_profile=True)
    if not partner_clip:return None
    await verify_partner_profile(service,job,info,partner,partner_clip,'cast-expression-partner-profile')
    prepared=info['prepared'];source=prepared['video'];width,height=info['size']
    joined=await cpu(service,job,'cast-retry-composite',{'isolated':partner_clip['path'],
        'isolated_sha256':partner_clip['sha256'],'joint':donor['video'],'joint_sha256':donor['files'][donor['video']],
        'source':source,'source_sha256':prepared['files'][source], 'frames':count,'width':width,'height':height,
        'side':side,'split':layout['split'],'margin':layout['margin']},label='cast-expression-guide-scene')
    path=joined['video']
    extracted=await cpu(service,job,'expression-stills',{'source':path,'sha256':joined['files'][path],
                        'indices':positions},label='cast-expression-stills')
    return [{'frame':frame,'name':await upload(service,job,Path(image))}
            for frame,image in zip(positions,extracted['images'],strict=True)]


async def repair_actor(service,job,p,info,names,rendered,review,index,*,expression_anchors=()):
    from .direct_character import graph
    from .head import digest
    base=Path(rendered['path'])
    if digest(base)!=rendered['sha256']:
        raise ValueError('Cambió la toma que conserva el movimiento original.')
    mouth_only=mouth_repair_actor(review,info['cast_layout']['order'])==index
    label=f'cast-side-mouth-{index}' if mouth_only else f'cast-side-repair-{index}'
    video=names['video']
    if not expression_anchors:
        with base.open('rb') as stream:
            upload=await service.post(service.settings['comfy_url'],'/upload/image',
                files={'image':(rendered['sha256']+'.mp4',stream,'video/mp4')},
                data={'overwrite':'false','subfolder':f'gen_studio/{job["id"]}/cast-repair'})
        video='/'.join(filter(None,[upload.get('subfolder'),upload['name']]))
    actor=info['actors'][index]
    refs=list(dict.fromkeys([actor['appearance_picture'],actor['face_picture']]))
    profile_refs=actor.get('profile_pictures',[]) if job.get('direct_joint_cast')=='profile-context-2' and not mouth_only else []
    for number in profile_refs:
        if number-1 not in refs:refs.append(number-1)
    face=refs.index(actor['face_picture'])+1
    position='left' if info['cast_layout']['order'][0]==index else 'right'
    from .h3_integral_review import ask
    from pydantic import BaseModel,Field
    class ShotDirection(BaseModel):
        direction: str=Field(min_length=30,max_length=1800)
    partner=1-index
    rows={row['actor']-1:row for row in review['actors']}
    def observed(number,key):
        return rows[number]['checks'].get(key,{})
    motion=mouth_only or any(observed(partner,key).get('verdict')=='fail' for key in ('mouth_action','head_motion'))
    card=p['direct_characters'][index]
    data={'target':card['name'],'side':position,'source_subject':actor['confirmed_target'],
          'reference_details':actor['appearance'],'identity_picture':face,
          'partner':p['direct_characters'][partner]['name'],
          'partner_appearance':info['actors'][partner]['appearance'],
          'source_performance':{key:observed(index,key).get('source_observation') for key in ('head_motion','mouth_action')},
          'appearance_correction':rows[index].get('correction',''),
          'partner_motion':{k:observed(partner,k).get('source_observation') for k in ('mouth_action','head_motion')},
          'original_motion_guide':motion,'expression_only':mouth_only}
    rules=('Write a concrete English H3 shot direction. Describe the target identity, hair and visible clothing '
           'from the assigned picture, which original features must disappear, and the visible source action at '
           'opening, middle and end. Use screen coordinates. Respect the original crop and lighting. '
           'Never stage the reference portrait. Describe the other character and retain their generated identity. '
           'Video 1 is the scene being edited. Only when original_motion_guide is true, Video 2 supplies the '
           'exact original head and lip movement for both performers, never their appearance. Specify the '
           'partner mouth timing when it differs from Video 1. Image references depict only the target.')
    if mouth_only:
        rules+=(' Both identities are already established in Video 1. Keep the entire composition, scale, '
                'identity, wardrobe and the partner fixed. Describe only the selected performer lip and cheek '
                'expression over time from Video 2, including smile onset, visible teeth and mouth closure.')
    if expression_anchors:
        rules=('Write a concise English shot direction from the supplied source observations: '
               'specify both performers, the target mouth closure, visible teeth and timing, and the partner gaze. '
               'There is only one video: the original scene provides motion. Frame-aligned images provide '
               'composition and both replacement identities. Do not mention numbered photos or a second video.')
    direction=await ask(service,job,label+'-shot-direction',ShotDirection,rules,data,[])
    prompt=(f'Replace the {position} performer of <Video 1> with {card["name"]} from <Picture {face}>. '
            'Apply identity, hair and visible wardrobe in place. '+direction.direction)
    if mouth_only:
        prompt=(f'Keep both performers in <Video 1> in place. Preserve the {position} performer '
                f'as {card["name"]} from <Picture {face}>. Adjust only their mouth and cheeks. '
                'Keep both heads oriented as in the source and preserve the other performer. '+direction.direction)
    if profile_refs:
        pictures=', '.join(f'<Picture {refs.index(number-1)+1}>' for number in profile_refs)
        prompt+=f' Use {pictures} for the target in side view from the opening frame. Recreate the reference headwear, hair, ears, collar and visible accessories at the source scale throughout. Keep the expression and head angle from the moving scene.'
    if motion:
        prompt+=' Match <Video 2> frame by frame for mouth openings and head motion only. Keep the partner identity from <Video 1>.'
    if expression_anchors:
        from .h3_face_fidelity import apply
        prompt=('Recreate <Video 1> with the people pictured in the timed guides. '
                'Keep the original camera, timing, head direction and lighting. '+direction.direction+' '
                +' '.join(f'{card["name"]} uses <Picture {a["face_picture"]+1}>.'
                          for card,a in zip(p['direct_characters'],info['actors'])))
        native=apply(graph({**p,'prompt':prompt},video,names['references'],job['id']),expression_anchors)
    else:
        native=graph({**p,'prompt':prompt},video,[names['references'][i] for i in refs],job['id'])
        if motion:add_motion_reference(native,names['video'])
    native=nsfw_adapters.apply_optional_adapters(native,p)
    native['28']['inputs']['filename_prefix']=f'gen_studio/{job["id"]}/{label}'
    return await phase(service,job,'direct-'+label,native,'28','.mp4')
