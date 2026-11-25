"""Reuse the native Object Swap recipe while preserving the input rendering style."""
RECIPE='object-swap-style-v1'

def prompt():
    from .character_bindings import compile_prompt
    return compile_prompt(['the main transformed character'],1,edit_policy={
        'version':1,'scope':'character','clothing':'reference','background':'source'})+'\n'+(
        'Use the reference facial proportions, eyes, hair or fur, distinctive anatomy, skin or fur color, '
        'and the exact colors of each garment and accessory from the FIRST to the LAST frame. '
        'Replace the simplified identity already present in the video completely. '
        'Do not gradually blend back into that input identity, outfit or color palette. '
        'The reference identity and wardrobe remain the same during head turns, profiles, bending and occlusion; '
        'keep the same identity when the face becomes visible again. '
        'Follow the video head orientation, gaze, expression changes, body rotation and contact with objects at the original times. '
        'Do not turn the head toward the camera to show the reference face. '
        'Keep the video rendering style, environment palette and exposure stable. '
        'Do not recolor the environment to match the reference background or introduce a photorealistic style into an illustrated video. '
        'The reference supplies identity and clothing only, never its pose, background or sheet layout.')

def adapt(graph,parameters):
    recipe=parameters.get('hybrid_identity_recipe')
    if recipe is None:return graph
    if recipe!=RECIPE or not parameters.get('hybrid_parent_id') or parameters.get('direct_preset')!='hands' or parameters.get('direct_character_version')!='v1.5':
        raise ValueError('Receta de identidad Motion Transfer no válida.')
    # This is the native full-reference Object Swap graph; realism changes the
    # rendering domain and therefore is deliberately absent from this variant.
    from .character_update import OFFICIAL_REALISM
    realism=graph.get('4',{})
    if realism.get('class_type')!='LoraLoaderModelOnly' or realism['inputs']['lora_name'].replace('\\','/').split('/')[-1]!=OFFICIAL_REALISM:
        raise ValueError('No se reconoce la adaptación de estilo de Object Swap.')
    replacement=realism['inputs']['model']
    for node in graph.values():
        for key,value in node['inputs'].items():
            if value==['4',0]:node['inputs'][key]=replacement.copy()
    del graph['4']
    encoder=graph['19']['inputs'];prefix='r34l1sm , ultra realistic video. '
    if not encoder['prompt'].startswith(prefix):raise ValueError('No se reconoce el prefijo de estilo del workflow.')
    encoder['prompt']=encoder['prompt'][len(prefix):]
    encoder['ref_image_size']='max'
    return graph
