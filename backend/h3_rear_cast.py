"""A joint scene guide when one confirmed performer has no measurable face."""
import json
from pathlib import Path
import shutil
from uuid import uuid4

from .head import digest
from .h3_close_cast import scene_edit
from .h3_cast_wardrobe import image_region
from .video_sequences import save

MODEL_SHA = '2647878d5dfa5098f2f8649825738a9345572bae2d4350a2468587ece47dd318'
CURRENT='marked-sam2-8'
PROFILE_REVIEW={'marked-sam2-7',CURRENT}
PROFILE_GUIDED={'marked-sam2-6',*PROFILE_REVIEW}
TEXTURED={'marked-sam2-5',*PROFILE_GUIDED}
PROJECTED={'marked-sam2-4',*TEXTURED}
VIEW_ONLY={'marked-sam2-3',*PROJECTED}
BLIND_REVIEW={'marked-sam2-2',*VIEW_ONLY}
SUPPORTED={'marked-sam2-1',*BLIND_REVIEW}


def untracked_actor(info, marks):
    scan=list(info['scan'].values())
    if (len(marks)!=2 or not scan or info['start'] or
            any(not 0<=m['frame']<info['end'] for m in marks) or
            any(s['face_count']!=1 or len(s['boxes'])!=2 for s in scan)):
        return None
    for i in (0,1):
        if all(s['boxes'][i] is None and s['boxes'][1-i] and
               s['boxes'][1-i][3]-s['boxes'][1-i][1]>=.20*info['size'][1] for s in scan):
            return i
    return None


async def prepare_track(service,job,info,marks):
    from . import h3_face_fidelity as face
    actor=untracked_actor(info,marks)
    if actor is None:return None
    prepared=info['prepared']; video=prepared['video']
    service.store.update(job['id'],stage='SAM 2.1 · Siguiendo la silueta desde la marca confirmada')
    binding={
        'mode':'sam-marked-person','runtime':service.settings['h3_marked_person_runtime'],
        'model_sha256':MODEL_SHA,'path':video,'path_sha256':prepared['files'][video],
        'size':list(info['size']),'count':info['end'],'indices':info['indices'],
        'actor':actor,'marks':marks,'peer_faces':{k:s['boxes'][1-actor] for k,s in info['scan'].items()}}
    if job.get('direct_body_tracking') in VIEW_ONLY:
        from .job_cancel import thread_checkpoint
        await thread_checkpoint(service,job['id'],reuse_tracking,service,job,binding)
    return await face.cpu(service,job,'cast-marked-silhouette',binding)


def tracking_key(binding):
    # Temporary paths and detector opening estimates do not enter the SAM predictor.
    return {**{k:v for k,v in binding.items() if k not in ('path','marks')},
            'marks':[{k:m[k] for k in ('frame','point')} for m in binding['marks']]}


def reuse_tracking(service,job,binding):
    from .guided_composition import local_path
    p=job['parameters']; folder=service.root/'jobs'/job['id']
    name='cast-marked-silhouette.json'; parent=p.get('sequence_parent_id')
    if not parent or (folder/name).exists():return
    with service.store.connect() as db:
        rows=db.execute("""SELECT id FROM jobs WHERE id<>? AND json_extract(payload,'$.sequence_parent_id')=?
            AND json_extract(payload,'$.sequence_index')=? AND json_extract(state,'$.direct_body_tracking') IS NOT NULL
            ORDER BY rowid DESC LIMIT 4""",(job['id'],parent,p['sequence_index'])).fetchall()
    for (prior,) in rows:
        previous=service.root/'jobs'/prior
        try:
            stored=json.loads((previous/name).read_text('utf8'))
            if tracking_key(stored['binding'])!=tracking_key(binding):continue
            report=stored['report']
            if report['frames_tracked']!=binding['count'] or report['actor']!=binding['actor']:continue
            if set(report['samples'])!={str(i) for i in binding['indices']}:continue
            files={}
            for frame,sample in report['samples'].items():
                path=local_path(previous,sample['mask']['path'])
                if digest(path)!=sample['mask']['sha256']:break
                files[frame]=path
            else:
                copies=folder/('tracked-masks-'+str(uuid4()));copies.mkdir()
                for frame,path in files.items():
                    target=copies/(frame+'.png');shutil.copyfile(path,target)
                    mask=report['samples'][frame]['mask']
                    if digest(target)!=mask['sha256']:raise ValueError('Cambió la máscara copiada.')
                    mask['path']=str(target)
                save(folder/name,{'binding':binding,'report':report,'source_attempt':prior});return
        except (ValueError,OSError,KeyError,TypeError):
            continue


def actor_region(info,index):
    track=info['body_track']
    if index==track['actor']:
        boxes=[s['box'] for s in track['samples'].values()]
    else:
        w,h=info['size'];boxes=[]
        for s in info['scan'].values():
            x,y,r,b=s['boxes'][index];d=b-y
            boxes.append([max(0,int(x-.40*d)),max(0,int(y-.45*d)),min(w,int(r+.40*d)),min(h,int(b+1.60*d))])
    return [min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes)]


async def inspect_appearance(service,job,info,frames,label):
    from . import h3_face_fidelity as face, h3_integral_review as integral
    track=info['body_track']; index=track['actor']; actor=info['actors'][index]
    refs=info['prepared']['references']
    indices=list(dict.fromkeys([actor['appearance_picture'],*(p-1 for p in actor.get('profile_pictures',[]))]))
    paths=[Path(refs[i]) for i in indices];regions=[None]*len(paths)
    for still in frames:
        key=str(still['frame']);box=track['samples'][key]['box']
        for image in (info['scan'][key]['image'],still):
            path=face.path_for(service,image)
            paths.append(path);regions.append(image_region(path,box,info['size']))
    result=await integral.ask(service,job,label+'-silhouette-appearance',integral.Check,
        'First come the assigned reference pictures. Then each pair is the same tracked SOURCE person '
        'and the generated RESULT at that instant. Only evaluate the visible person in these crops. '
        'First identify what is actually visible in the SOURCE. Match that orientation, scale and occlusion. '
        'Identify concrete changes from SOURCE to RESULT in hair shape/texture, visible shoulder coverage, '
        'neckline and material that agree with the references. Compare all provided times. Fail if the '
        'source appearance remains where visibly different, the wrong person changed, a hidden face was '
        'invented or the source garment remains. If there is too little evidence return uncertain. '
        'A hidden face cannot prove facial identity. Describe the decisive visible evidence in Spanish.',
        {'reference_count':len(indices),'frames':[f['frame'] for f in frames]},paths,pixel_regions=regions)
    return result.model_dump()


async def compare_tracked_view(service,job,info,frames,label):
    """The verifier sees only source/result crops. Reference sheets cannot be mistaken for output."""
    from . import h3_face_fidelity as face, h3_integral_review as integral
    from .h3_swap_policy import applies
    lighting = (' Account for the light and exposure of the current scene when comparing colour and sheen. '
                'Require a visible structural contradiction such as the strand pattern, hair fall or length '
                'to reject hair on the basis of brightness or highlight changes. Judge only visible areas.'
                if applies(job) else '')
    track=info['body_track'];actor=info['actors'][track['actor']]
    paths=[];regions=[]
    for f in frames:
        key=str(f['frame']);box=track['samples'][key]['box']
        for image in (info['scan'][key]['image'],f):
            path=face.path_for(service,image)
            paths.append(path);regions.append(image_region(path,box,info['size']))
    if job.get('direct_body_tracking') in PROFILE_REVIEW and actor.get('profile_pictures'):
        from typing import Literal
        class HairReview(integral.Strict):
            hair_matches_reference: Literal['yes','no','uncertain']
            pose_matches_source: Literal['yes','no','uncertain']
            evidence: str
        profile=Path(info['prepared']['references'][actor['profile_pictures'][0]-1])
        review=await integral.ask(service,job,label+'-profile-hair-review',HairReview,
            'Image 1 is the replacement hair target. Remaining images are chronological pairs: source pose, generated candidate. '
            'For hair compare each candidate ONLY to image 1 using visible color, strand texture, sheen and length. '
            'The source hairstyle is intended to change. For pose compare each candidate ONLY to its paired source, '
            'checking orientation, camera and occlusion. Ignore clothing here. If a required trait cannot be assessed use uncertain. '
            'Give visible evidence in Spanish for both comparisons.'+lighting,
            {'frames':[f['frame'] for f in frames]},[profile,*paths],pixel_regions=[None,*regions])
        checks=(review.hair_matches_reference,review.pose_matches_source)
        verdict='fail' if 'no' in checks else 'uncertain' if 'uncertain' in checks else 'pass'
        return {'verdict':verdict,'evidence':review.evidence}
    if job.get('direct_body_tracking') in TEXTURED:
        result=await integral.ask(service,job,label+'-source-result-hair',integral.Check,
            'Alternate pairs ORIGINAL and GENERATED. Check the assigned hair and the original orientation '
            'of the head only. Wardrobe is measured separately. Focus on visible length, texture, fall and color. '
            'The portrait styling adapts to the original pose and hair occlusion. A hair length extending beyond '
            'the edge of the image is unknown. Require positive agreement in visible traits at each supplied time. '
            'If the original hairstyle visibly differs from the requested hair and is retained, fail. Reject '
            'an invented face reveal or a new camera angle. If evidence is ambiguous return uncertain. '
            'Give concrete evidence in Spanish.'+lighting,{'paired_frames':[f['frame'] for f in frames],
            'replacement_hair':actor['appearance'].get('hair_details')},paths,pixel_regions=regions)
        return result.model_dump()
    result=await integral.ask(service,job,label+'-source-result-view',integral.Check,
        'The images alternate ORIGINAL, GENERATED at each listed frame. There are no reference '
        'pictures here. Compare only the person centered in each crop. In the generated samples '
        'look for the requested appearance specified separately in the text. Check the length, texture and '
        'fall of the hair, the visible shoulder and garment coverage, and the angle and visible extent of '
        'the head. Color alone is insufficient. Require positive visual agreement with the replacement '
        'and a visible change where the original differs. Respect areas outside the camera. '
        'A newly revealed face, source clothing left on a visibly different shoulder, or visibly wrong '
        'haircut is a failure. If insufficient evidence, return uncertain. Give short Spanish evidence.',
        {'paired_frames':[f['frame'] for f in frames],'replacement_hair':actor['appearance'].get('hair_details'),
         'replacement_outfit':actor['upper_outfit']},paths,pixel_regions=regions)
    return result.model_dump()


def focused_edit(graph,box,size,source_size):
    """Give the editor a detailed view, then put only its tracked silhouette into the scene still."""
    w,h=size; sw,sh=source_size
    x,y,r,b=[v*limit/base for v,limit,base in zip(box,(w,h,w,h),(sw,sh,sw,sh))]
    pad=max(16,.10*max(r-x,b-y))
    x=max(0,int((x-pad)//16)*16);y=max(0,int((y-pad)//16)*16)
    r=min(w,(int((r+pad)//16)+1)*16);b=min(h,(int((b+pad)//16)+1)*16)
    crop={'x':x,'y':y,'width':r-x,'height':b-y}
    def node(kind,**inputs):return {'class_type':kind,'inputs':inputs}
    graph['scene-crop']=node('ImageCrop',image=['input0',0],**crop)
    factor=min(1.7,1008/max(r-x,b-y))
    ew,eh=[max(64,round(v*factor/16)*16) for v in (r-x,b-y)]
    graph['scale0']['inputs'].update(image=['scene-crop',0],width=ew,height=eh)
    graph['sigmas']['inputs'].update(width=ew,height=eh)
    graph['mask-image']=node('MaskToImage',mask=['tracked-person',0])
    def scale(image,width,height):
        return node('ImageScale',image=image,upscale_method='bilinear',width=width,height=height,crop='disabled')
    graph['mask-canvas']=scale(['mask-image',0],w,h)
    graph['canvas-mask']=node('ImageToMask',image=['mask-canvas',0],channel='red')
    graph['detail-mask']=node('CropMask',mask=['canvas-mask',0],**crop)
    graph['tracked-latent']['inputs']['mask']=['detail-mask',0]
    graph['edit-detail']=scale(['decode',0],r-x,b-y)
    graph['edited-scene']=node('ImageCompositeMasked',destination=['input0',0],source=['edit-detail',0],
                    mask=['detail-mask',0],x=x,y=y,resize_source=False)
    graph['save']['inputs']['images']=['edited-scene',0]
    return graph


def mask_edit(graph,mask_name,invert):
    graph['tracked-person']={'class_type':'LoadImageMask','inputs':{'image':mask_name,'channel':'red'}}
    mask=['tracked-person',0]
    if invert:
        graph['outside-person']={'class_type':'InvertMask','inputs':{'mask':mask}}
        mask=['outside-person',0]
    graph['tracked-latent']={'class_type':'SetLatentNoiseMask','inputs':{'samples':['enc0',0],'mask':mask}}
    graph['sample']['inputs']['latent_image']=['tracked-latent',0]
    return graph


def visible_style_prompt(actor,position,projection):
    prompt=(f'Edit the marked person at {position}% of image width in place. Preserve the original head angle '
            f'and close framing. Change the existing hair to {projection["hair"]}. '
            f'In this camera view, {projection["head"]}. Let strands fall across exactly the features that '
            'the source hair obscures. Keep the visible jaw outline and source distance. ')
    if projection['visible_torso']=='shoulder_neck_only' and actor['upper_outfit']['shoulders']=='both_bare':
        prompt+=('Replace the original shoulder garment with a naturally lit bare shoulder and neck. '
                 'The strapless garment starts below the visible shoulder, beyond the crop. ')
    else:
        prompt+='Match this visible replacement wardrobe: '+actor['upper_outfit']['upper_garment']+' '
    return prompt+'Preserve the other person and the background. This is the same moment in the same film.'


async def project_view(service,job,info,frame):
    from typing import Literal
    from pydantic import Field
    from . import h3_integral_review as integral, h3_face_fidelity as face
    class Projection(integral.Strict):
        hair: str=Field(min_length=1,max_length=150)
        head: str=Field(min_length=1,max_length=190)
        visible_torso: Literal['shoulder_neck_only','garment_body_visible','uncertain']
    track=info['body_track'];actor=info['actors'][track['actor']];key=str(frame)
    path=face.path_for(service,info['scan'][key]['image'])
    result=await integral.ask(service,job,f'rear-camera-appearance-{frame}',Projection,
        'The image is the ORIGINAL subject crop. The separate hair description is from the REPLACEMENT. '
        'Write concise English camera directions. For hair give just replacement length, color and texture, '
        'without its portrait styling, parting, direction, anatomy or background. For head describe only the '
        'actual ORIGINAL orientation and the features visible or obscured in this crop. For visible_torso '
        'choose shoulder_neck_only if the garment body/bodice is outside frame and only a neck, '
        'collar, shoulder or sleeve edge is visible. Otherwise mark the garment body visible or uncertain.',
        {'replacement_hair':actor['appearance'].get('hair_details',''),
         **({'texture_requirement':'Include wet or dry finish, gloss, and strand pattern when described in the reference. Preserve all visible texture cues.'}
            if job.get('direct_body_tracking') in TEXTURED else {})},[path],
        pixel_regions=[image_region(path,track['samples'][key]['box'],info['size'])])
    return result.model_dump()


async def reference_inputs(service,job,info,index):
    from . import h3_face_fidelity as face
    actor=info['actors'][index];refs=info['prepared']['references'];result=[]
    views=[(actor['face_picture'],'portrait')]
    views.extend((n-1,'portrait') for n in actor.get('profile_pictures',[]) if n-1!=actor['face_picture'])
    views.append((actor['appearance_picture'],'outfit'))
    for n,kind in views:
        path=Path(refs[n]);sha=digest(path)
        report=await face.cpu(service,job,f'rear-ref-{kind}-{sha[:16]}',{
            'mode':'cast-reference-'+kind,'indices':[],'size':list(info['size']),'path':str(path),'path_sha256':sha})
        if report['crops']:path=face.path_for(service,report['crops'][0])
        result.append(await face.upload(service,job,path))
    return result


async def guides(service,job,info,*,retry):
    from . import h3_face_fidelity as face, h3_integral_review as integral, h3_cast_wardrobe as costume
    from .guided_head import phase
    track=info['body_track']; hidden=track['actor'];peer=1-hidden
    projected=job.get('direct_body_tracking') in PROJECTED
    view_only=job.get('direct_body_tracking') in VIEW_ONLY
    focused=job.get('direct_body_tracking') in BLIND_REVIEW
    folder=service.root/'jobs'/job['id'];receipt=folder/'rear-cast-guides.json'
    accepted=json.loads(receipt.read_text('utf8')) if receipt.exists() else []
    for guide in accepted:face.path_for(service,guide)
    if accepted and (not retry or len(accepted)>1):return accepted
    used={a['frame'] for a in accepted}
    frames=sorted(int(i) for i in track['samples'] if int(i)%17==0 and int(i) not in used)
    if not frames and accepted:return accepted
    refs={i:await reference_inputs(service,job,info,i) for i in ((peer,) if view_only else (peer,hidden))}
    reason='No hay fotograma alineado para la guía.'
    for frame in frames[:2 if job.get('direct_body_tracking') in PROFILE_GUIDED else 1]:
        k=str(frame);sample=info['scan'][k];row=track['samples'][k]
        projection=await project_view(service,job,info,frame) if projected else None
        source=await face.upload(service,job,face.path_for(service,sample['image']))
        mask=await face.upload(service,job,face.path_for(service,row['mask']))
        for attempt in range(2):
            current=source
            for i in (peer,hidden):
                actor=info['actors'][i];box=row['box'] if i==hidden else sample['boxes'][i]
                position=round(50*(box[0]+box[2])/info['size'][0])
                label=f'rear-guide-{frame}-{attempt}-{i}'
                pose=('Preserve the exact source visibility and direction of this head. Replace the visible hair, '
                      'neck and shoulders, including their outline. Where the camera sees the back of the head, '
                      'keep it facing away throughout. Preserve foreground hands and overlapping objects.' if i==hidden
                      else 'Replace the visible facial structure, hairstyle, headwear and outfit. Preserve expression, gaze and neck angle.')
                prompt=(f'Edit photograph 1. Replace only the performer at {position}% across the full photograph, '
                        f'in the exact same camera position, scale, pose and lighting. {pose} '
                        f'The following pictures are appearance references for this one person. The last picture supplies '
                        f'the garment: {actor["upper_outfit"]["upper_garment"]}. Hair: {actor["appearance"].get("hair_details","")}. '
                        'Respect the actual camera crop. Retain the other performer and the scene. Output one live-action photograph.')
                if focused:
                    action=next((m['head_gaze_face'] for m in actor.get('source_actions',[]) if m['frame']==frame),'')
                    if i==hidden:
                        prompt=(f'Edit the foreground performer in this close detail. Keep image 1 exactly framed. '
                                f'Restyle the hair from root to tip: {actor["appearance"].get("hair_details","")}. '
                                f'Remove the original hairstyle and garment. Match the last reference garment: '
                                f'{actor["upper_outfit"]["upper_garment"]}. ')
                        if actor['upper_outfit']['shoulders']=='both_bare':
                            prompt+=('Show natural bare skin on the visible shoulder and upper back. The fabric ends '
                                     'below the shoulders, and may fall entirely below this camera crop. ')
                    prompt+=' Preserve this source performance at this instant: '+action
                seed=(job['parameters']['seed']+10849+17*frame+347*attempt+i)%9007199254739992
                refs_here=refs.get(i,[])
                hidden_view=view_only and i==hidden
                if hidden_view:
                    prompt=(f'Edit the marked performer at {position}% across the source still in place. Preserve '
                            'the exact original head angle, scale, crop and occlusion. Apply this replacement hairstyle: '
                            f'{actor["appearance"].get("hair_details","")}. Keep strands over the facial regions '
                            'covered in the source. Keep the original visible edge of the jaw. Keep hidden eyes, ears '
                            'and lips out of view. The source pose and camera take precedence over the hairstyle. '
                            f'Redress only visible fabric as {actor["upper_outfit"]["upper_garment"]}. ')
                    if actor['upper_outfit']['shoulders']=='both_bare':
                        prompt+='Remove the source shoulder fabric. Show the naturally lit bare shoulder and upper back. The bodice can stay entirely below frame. '
                    prompt+='Preserve the other person and background. At this instant: '+action
                    if projection:prompt=visible_style_prompt(actor,position,projection)
                use_profile=hidden_view and job.get('direct_body_tracking') in PROFILE_GUIDED and actor.get('profile_pictures')
                if use_profile:
                    profile=Path(info['prepared']['references'][actor['profile_pictures'][0]-1])
                    refs_here=[await face.upload(service,job,profile)]
                    prompt+=' Picture 2 supplies hair texture, color and finish only. Match the strands and sheen across the entire visible hair. Keep the head orientation and visibility from picture 1.'
                detailed_profile=use_profile and job.get('direct_body_tracking')==CURRENT
                if detailed_profile:
                    prompt=('Image 1 is a cropped film still. Restyle the foreground subject using the profile in image 2. '
                            'Match the direction from roots to ends, fineness, density, natural pigment and texture of the individual hair strands in image 2. '
                            'Render photographic hair with subtle highlights from the scene lighting. Keep the head angle, facial visibility, silhouette and camera of image 1. '
                            'Hair may be rearranged within the silhouette. Preserve visible skin detail. ')
                    prompt+=(
                        'Show the visible neck and shoulder as naturally lit bare skin.' if projection['visible_torso']=='shoulder_neck_only' and actor['upper_outfit']['shoulders']=='both_bare'
                        else 'Match the replacement wardrobe within the original crop: '+actor['upper_outfit']['upper_garment'])
                graph=scene_edit(job,label,[current,*refs_here],*info['edit_size'],prompt,seed,1 if hidden_view else attempt)
                graph['sigmas']['inputs']['steps']=7 if hidden_view else 8
                if detailed_profile:
                    graph['scale1']['inputs']['megapixels']=1.0
                    graph['edit-sigmas']['inputs']['step']=2 if attempt==0 else 0
                mask_edit(graph,mask,i==peer)
                if focused and i==hidden and (not view_only or attempt or use_profile):focused_edit(graph,box,info['edit_size'],info['size'])
                service.store.update(job['id'],stage=f'H3 directo · Preparando la guía de ambos personajes, fotograma {frame}')
                shot=await phase(service,job,label,graph,'save','.png')
                if shot is None:return None
                current=await face.upload(service,job,face.path_for(service,shot))
            still={'frame':frame,**shot}
            geometry=await face.cpu(service,job,label+'-geometry',{'mode':'anchor','path':shot['path'],
                'path_sha256':shot['sha256'],'indices':[frame],'size':list(info['size']),
                'expected':[sample['boxes'][peer]],'face_count':1})
            if not geometry['approved']:
                reason='Cambió la escala, la posición o el número de caras visibles.';continue
            match=await integral.inspect_marked_identity(service,job,info,peer,[still],label)
            verify=compare_tracked_view if focused else inspect_appearance
            appearance=await verify(service,job,info,[still],label)
            wardrobe=await costume.inspect(service,job,info,hidden,[still],label)
            if match['verdict']=='pass' and appearance['verdict']=='pass' and wardrobe['verdict'] in ('pass','not_visible'):
                guide={**still,'name':current,'retain_source_view':True}
                if job.get('direct_body_tracking') in TEXTURED and projection:
                    guide['visible_direction']=visible_style_prompt(info['actors'][hidden],
                        round(50*(row['box'][0]+row['box'][2])/info['size'][0]),projection)
                accepted.append(guide)
                save(receipt,accepted);return accepted
            reason=' '.join(v['evidence'] for v in (match,appearance,wardrobe) if v['verdict'] not in ('pass','not_visible'))
    if accepted:return accepted
    raise ValueError('La guía de las personas marcadas requiere revisión. '+reason)
