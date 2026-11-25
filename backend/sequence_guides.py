"""Attach a native image guide to every sampling pass without replacing media roles."""
from copy import deepcopy

def apply(graph,image_name,*,guide_frames=1):
    if type(guide_frames) is not int or guide_frames not in (1,5):raise ValueError('La guía requiere uno o cinco fotogramas reales.')
    g=deepcopy(graph);encoders=[key for key,n in g.items() if n['class_type']=='MiniMaxH3ReferenceToVideo']
    if not encoders:return g,[] # tracking-only phase
    if len(encoders)!=1:raise ValueError('La continuidad requiere un condicionamiento H3 único.')
    encoder=g[encoders[0]];vae=encoder['inputs']['vae'];image=['sequence-image',0]
    if guide_frames==5:
        g['sequence-video']={'class_type':'LoadVideo','inputs':{'file':image_name}}
        g['sequence-components']={'class_type':'GetVideoComponents','inputs':{'video':['sequence-video',0]}}
        image=['sequence-components',0]
    else:
        g['sequence-image']={'class_type':'LoadImage','inputs':{'image':image_name}}
    # Ninja conditions a moving crop. Apply the same reviewed mask/crop transform
    # to a batch of the previous full-canvas image, then use its first crop.
    if guide_frames==5 and 'crop' in g and g['crop']['class_type'] in ('MVEx_SubjectCrop','MVEx_SubjectCropAdvanced'):
        g['sequence-roi']={'class_type':'GenStudioFielGuideToROI','inputs':{
            'images':image,'bboxes':['crop',2],
            'source_images':g['crop']['inputs']['original_images'],'roi_images':['crop',0],
            'expected_context':5}}
        image=['sequence-roi',0]
    elif 'crop' in g and g['crop']['class_type']=='MVEx_SubjectCrop':
        count=g['source-exact']['inputs']['expected']
        g['sequence-repeat']={'class_type':'RepeatImageBatch','inputs':{'image':image,'amount':count}}
        crop=deepcopy(g['crop']);crop['inputs']['original_images']=['sequence-repeat',0]
        g['sequence-crop']=crop
        g['sequence-first']={'class_type':'ImageFromBatch','inputs':{'image':['sequence-crop',0],'batch_index':0,'length':1}}
        image=['sequence-first',0]
    changes=[]
    for key,node in list(graph.items()):
        if node['class_type']!='SamplerCustomAdvanced':continue
        original=graph.get(node['inputs']['guider'][0]);latent=node['inputs']['latent_image']
        if not original or original['class_type'] not in ('BasicGuider','CFGGuider'):raise ValueError('El sampler no ofrece una guía de continuidad compatible.')
        field='conditioning' if original['class_type']=='BasicGuider' else 'positive'
        condition=original['inputs'][field];guide='sequence-guide-'+key;guider='sequence-guider-'+key
        g[guide]={'class_type':'MiniMaxH3AddGuide','inputs':{'positive':condition,'latent':latent,'vae':vae,'image':image,'frame_idx':0}}
        g[guider]=deepcopy(original);g[guider]['inputs'][field]=[guide,0]
        if original['class_type']=='CFGGuider' and original['inputs'].get('negative')==condition:g[guider]['inputs']['negative']=[guide,0]
        g[key]['inputs']['guider']=[guider,0];changes.append(key)
    if not changes:raise ValueError('Este workflow no tiene un sampler compatible con la continuidad por fotograma.')
    prompt=encoder['inputs'].get('prompt')
    note='\nTemporal continuity: the first source frame and image guide are the previous window endpoint. Match it at frame zero, then immediately follow the new source motion. This hidden overlap frame is trimmed from the final video. Do not restart the action or freeze the pose.'
    if guide_frames==5:
        note='\nTemporal continuity: the first five source frames overlap the previous window. The guide clip contains the corresponding five previous visible output frames in chronological order. Follow their evolving pose and contacts, then continue the new source motion. These five context frames are trimmed from the final video. Do not repeat one endpoint, restart the action, or hold the opening pose.'
    if isinstance(prompt,str):encoder['inputs']['prompt']+=note
    elif isinstance(prompt,list) and g.get(prompt[0],{}).get('class_type') in ('SECoursesH3GenjutsuPrompt','GenStudioSECoursesPosePrompt'):
        g[prompt[0]]['inputs']['prompt']+=note
    return g,changes
