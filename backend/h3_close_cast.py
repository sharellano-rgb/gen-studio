"""Whole-frame, source-conditioned identity guides for a confirmed two-person close shot."""
import json
from pathlib import Path

from .head import digest
from .viggle_remake.shot_anchor_graphs import klein

STAGED = 'joint-closeup-2'


def eligible(job,info):
    if (job.get('direct_close_cast_guide') not in ('joint-closeup-1',STAGED) or not info.get('confirmed_cast')
            or not info.get('integral') or not info.get('cast_layout') or info['start']!=0):
        return False
    opening=info['scan'].get('0',{})
    boxes=opening.get('boxes',[])
    return (len(info['actors'])==len(boxes)==2 and opening.get('face_count')==2
            and all(box and box[3]-box[1]>=.22*info['size'][1] for box in boxes))


def close_graph(job,info,frame,names,roles,label,seed,skip,corrections=()):
    width,height=info['edit_size']
    sample=info['scan'][str(frame)]
    text=['Edit image 1, a single live-action close two-shot. Keep this exact camera crop, background, '
          'head sizes, shoulders, head angles, gaze and expressions at this instant. Replace both people '
          'together, once each. Image 1 alone defines composition and pose.']
    for actor,box,refs in zip(info['actors'],sample['boxes'],roles,strict=True):
        x=round(100*(box[0]+box[2])/(2*info['size'][0]))
        labels=', '.join(f'image {n} ({view})' for n,view in refs)
        text.append(f'The performer centered at {x}% of frame width takes the identity, hair, headwear '
                    f'and visible neckline from {labels}. Match the source expression and lighting there.')
        if actor.get('upper_outfit'):
            text.append(f'Redress this performer within the existing crop: {actor["upper_outfit"]["upper_garment"]}. '
                        'Replace all visible source garment fabric up to the shoulders. '
                        'Use the neckline and coverage of the replacement under the existing hair and occlusions.')
    text.extend(corrections)
    text.append('Return one photograph at the same moment, with the same crop at every edge. '
                'Keep each face distinct and the space between them intact.')
    return scene_edit(job,label,names,width,height,' '.join(text),seed,skip)


def scene_edit(job,label,names,width,height,prompt,seed,skip):
    graph=klein({'prompt':prompt,'seed':seed},job['id']+'/'+label,names,width,height)
    graph['scale0']={'class_type':'ImageScale','inputs':{'image':['input0',0],'width':width,'height':height,
                    'upscale_method':'lanczos','crop':'disabled'}}
    for i in range(1,len(names)):
        graph[f'scale{i}']['inputs']['megapixels']=.32
    graph['sigmas']['inputs']['steps']=6
    graph['edit-sigmas']={'class_type':'SplitSigmas','inputs':{'sigmas':['sigmas',0],'step':skip}}
    graph['sample']['inputs'].update(sigmas=['edit-sigmas',1],latent_image=['enc0',0])
    del graph['latent']
    return graph


def outfit_graph(job,info,frame,source,details,indices,label,seed,attempt):
    from .h3_cast_wardrobe import upper_body_region
    width,height=info['edit_size']
    sw,sh=info['size']
    prompt=['Edit the clothing in photograph 1. Preserve the people, heads, hair, poses, lighting and camera.']
    names=[source]
    regions=[]
    for index in indices:
        region=upper_body_region(info,index,frame)
        if region is None:raise ValueError('No se pudo localizar la prenda en la guía conjunta.')
        regions.append([round(x*width/sw) if i%2==0 else round(x*height/sh) for i,x in enumerate(region)])
        x0,_,x1,_=info['scan'][str(frame)]['boxes'][index]
        outfit=info['actors'][index]['upper_outfit']
        names.append(details[index])
        prompt.append(f'At {round(50*(x0+x1)/sw)}% of image width dress the performer exactly as in '
                      f'image {len(names)}: {outfit["upper_garment"]}.')
        if outfit['shoulders']=='both_bare':
            prompt.append('Remove the original sleeves and shoulder fabric completely. Show naturally lit bare '
                          'skin across the visible shoulder and upper arm. Keep the bodice down on the chest; '
                          'its neckline may fall below the crop. Preserve strands of hair over the shoulder.')
    graph=scene_edit(job,label,names,width,height,' '.join(prompt),seed,0 if attempt else 1)
    graph['sigmas']['inputs']['steps']=8
    graph['mask-base']={'class_type':'SolidMask','inputs':{'value':0.,'width':width,'height':height}}
    mask=['mask-base',0]
    for number,(x,y,r,b) in enumerate(regions):
        key=f'outfit-{number}'
        feather=min(16,(r-x)//4,(b-y)//4)
        graph[key]={'class_type':'SolidMask','inputs':{'value':1.,'width':r-x,'height':b-y}}
        graph[key+'-edge']={'class_type':'FeatherMask','inputs':{'mask':[key,0],
            'left':feather if x else 0,'right':feather if r<width else 0,
            'top':feather if y else 0,'bottom':feather if b<height else 0}}
        graph[key+'-mask']={'class_type':'MaskComposite','inputs':{'destination':mask,'source':[key+'-edge',0],
            'x':x,'y':y,'operation':'add'}}
        mask=[key+'-mask',0]
    graph['masked-scene']={'class_type':'SetLatentNoiseMask','inputs':{'samples':['enc0',0],'mask':mask}}
    graph['sample']['inputs']['latent_image']=['masked-scene',0]
    return graph


async def check_guide(service,job,info,frame,shot,label,*,outfits):
    from . import h3_face_fidelity as face, h3_cast_wardrobe as costume
    sample=info['scan'][str(frame)]
    geometry=await face.cpu(service,job,label+'-geometry',{'mode':'anchor','path':shot['path'],
        'path_sha256':shot['sha256'],'indices':[frame],'size':list(info['size']),
        'expected':sample['boxes'],'face_count':sample['face_count']})
    if not geometry['approved']:return 'encuadre',list(range(len(info['actors'])))
    still={'frame':frame,**shot}
    wrong=[]
    for index in range(len(info['actors'])):
        check=await face.integral.inspect_marked_identity(service,job,info,index,[still],label)
        if check['verdict']!='pass':wrong.append(index)
    if wrong:return 'identidad',wrong
    if outfits:
        for index in range(len(info['actors'])):
            check=await costume.inspect(service,job,info,index,[still],label)
            if check['verdict'] not in ('pass','not_visible'):wrong.append(index)
        if wrong:return 'vestuario',wrong
    return '',[]


async def joint_guide(service,job,info,*,retry):
    from . import h3_face_fidelity as face
    from .guided_head import phase
    from .video_sequences import save

    folder=service.root/'jobs'/job['id']
    receipt=folder/'joint-closeup-guides.json'
    approved=[]
    if receipt.exists():
        approved=json.loads(receipt.read_text(encoding='utf8'))
        for guide in approved:face.path_for(service,guide)
        if not retry or len(approved)>1:return approved
    first=approved[0]['frame'] if approved else None
    frames=[int(i) for i,s in info['scan'].items() if int(i)%17==0 and int(i)<info['end']
            and s['face_count']==2 and all(s['boxes']) and int(i)!=first]
    if not frames and approved:return approved
    paths=info['prepared']['references']
    staged=job.get('direct_close_cast_guide')==STAGED
    identity_info=({**info,'actors':[{k:v for k,v in a.items() if k!='upper_outfit'} for a in info['actors']]}
                   if staged else info)
    portraits=[];roles=[];details={}
    failures=[]
    for index,actor in enumerate(info['actors']):
        primary=actor['face_picture']
        slots=[(primary,'identity')]+[(n-1,'profile') for n in actor.get('profile_pictures',[]) if n-1!=primary]
        role=[]
        for n,view in slots:
            path=Path(paths[n]);sha=digest(path)
            crop=await face.cpu(service,job,'joint-portrait-'+sha[:16],{'mode':'cast-reference-portrait',
                'size':list(info['size']),'indices':[],'path':str(path),'path_sha256':sha})
            if crop['crops']:path=face.path_for(service,crop['crops'][0])
            elif view=='profile':continue
            portraits.append(await face.upload(service,job,path));role.append((len(portraits)+1,view))
        if actor.get('upper_outfit'):
            path=Path(paths[actor['appearance_picture']]);sha=digest(path)
            detail=await face.cpu(service,job,'joint-outfit-'+sha[:16],{'mode':'cast-reference-outfit',
                'size':list(info['size']),'indices':[],'path':str(path),'path_sha256':sha})
            if staged:
                garment=face.path_for(service,detail['crops'][0]) if detail['crops'] else path
                details[index]=await face.upload(service,job,garment)
            elif detail['crops']:
                portraits.append(await face.upload(service,job,face.path_for(service,detail['crops'][0])))
                role.append((len(portraits)+1,'upper garment detail'))
        roles.append(role)
    for frame in sorted(frames)[:2]:
        sample=info['scan'][str(frame)]
        source=await face.upload(service,job,face.path_for(service,sample['image']))
        skip=2
        corrections=[]
        for attempt in range(2):
            label=f'direct-face-anchor-joint-close-{frame}-{attempt}'
            seed=(job['parameters']['seed']+6281+frame+attempt*4568)%9007199254739992
            graph=close_graph(job,identity_info,frame,[source,*portraits],roles,label,seed,skip,corrections)
            service.store.update(job['id'],stage=f'H3 directo · Verificando la guía conjunta en el fotograma {frame}')
            shot=await phase(service,job,label,graph,'save','.png')
            if shot is None:return None
            if staged:
                issue,wrong=await check_guide(service,job,info,frame,shot,label,outfits=True)
                if issue=='vestuario':
                    # Always refine the same joint identity image, never a video of just one actor.
                    base=await face.upload(service,job,face.path_for(service,shot))
                    pending=wrong
                    for dress_attempt in range(2):
                        dress_label=f'{label}-outfit-{dress_attempt}'
                        graph=outfit_graph(job,info,frame,base,details,pending,dress_label,seed+71+dress_attempt,dress_attempt)
                        service.store.update(job['id'],stage=f'H3 directo · Ajustando el vestuario en la guía del fotograma {frame}')
                        shot=await phase(service,job,dress_label,graph,'save','.png')
                        if shot is None:return None
                        issue,wrong=await check_guide(service,job,info,frame,shot,dress_label,outfits=True)
                        if not issue:break
                if not issue:
                    approved.append({'frame':frame,**shot,'name':await face.upload(service,job,face.path_for(service,shot))})
                    approved.sort(key=lambda s:s['frame'])
                    save(receipt,approved)
                    return approved
                cards=job['parameters']['direct_characters']
                failures.append(f'fotograma {frame}: {issue} ({", ".join(cards[i]["name"] for i in wrong)})')
                skip=3 if issue=='encuadre' else 1
                continue
            geometry=await face.cpu(service,job,label+'-geometry',{'mode':'anchor','path':shot['path'],
                'path_sha256':shot['sha256'],'indices':[frame],'size':list(info['size']),
                'expected':sample['boxes'],'face_count':sample['face_count']})
            if not geometry['approved']:
                skip=3
                continue
            still={'frame':frame,**shot}
            verdicts=[await face.integral.inspect_marked_identity(service,job,info,index,[still],label) for index in range(2)]
            if all(v['verdict']=='pass' for v in verdicts):
                from . import h3_cast_wardrobe as costume
                corrections=[]
                for index,actor in enumerate(info['actors']):
                    if actor.get('upper_outfit'):
                        verdict=await costume.inspect(service,job,info,index,[still],label)
                        if verdict['verdict'] not in ('pass','not_visible'):
                            x0,_,x1,_=sample['boxes'][index]
                            position=round(50*(x0+x1)/info['size'][0])
                            corrections.append(f'At {position}% of frame width, correct the costume: '
                                               f'{actor["upper_outfit"]["upper_garment"]}. {verdict["evidence"]}')
                if corrections:
                    skip=1
                    continue
                approved.append({**still,'name':await face.upload(service,job,face.path_for(service,shot))})
                approved.sort(key=lambda s:s['frame'])
                save(receipt,approved)
                return approved
            skip=1
    if approved:return approved
    if failures:
        raise ValueError('Guía conjunta sin confirmar: '+'; '.join(dict.fromkeys(failures))+'. Se conservan las tomas anteriores.')
    raise ValueError('La guía conjunta no confirmó las dos identidades, el vestuario o el encuadre del original. '
                     'Se detuvo el tramo antes de generar vídeo.')
