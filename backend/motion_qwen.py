"""Qwen Image 2.1 editing from a protected copy of the installed preset."""
import json

from .head import digest
from .workflows import ROOT

VENDOR = ROOT/'workflows/vendor/qwen-image-21/image_qwen_image_21_edit.template.json'
VENDOR_SHA = '4f8c95512c57408746a2c1c3e227cf58abcc54b8f2c77182801c1226c1534492'
MODELS = {'unet': 'qwen_image_2.1_int8_convrot.safetensors',
          'clip': 'qwen3vl_8b_int8_convrot.safetensors', 'vae': 'qwen_image_2.1_vae_bf16.safetensors'}
STEPS = 20
PROFILE_STEPS = {'legacy': 20, 'pose_v2': 25}


def environment_prompt(label='Image 3'):
    return (
        'REPLACE the complete source environment, both foreground and background, with the destination environment in '+label+'. '
        'Remove the original location and scenery completely; do not merely recolor or restyle the source location. '
        +label+' supplies ONLY environment, scene objects, materials, lighting and background visual style. '
        'Reconstruct this destination around the source pose and camera perspective. '
        'Ignore any people or characters visible in '+label+'; never copy their identity, pose, clothing, scale or placement. '
        'Do not copy the camera viewpoint or framing from '+label+', and do not paste that whole reference image into the output. ')


def three_image_prompt(p, labels=('Image 1','Image 2','Image 3')):
    source,identity,environment=labels
    text=(
        'Create ONE complete replacement scene. '+source+' is ONLY a geometry guide for the performer pose and camera. '
        'REPLACE the single source performer completely with the character from '+identity+'. '
        'The original performer must disappear. Show exactly ONE actor: the target character, not the original person plus an added character. '
        'Use '+identity+' for identity, face, anatomy, hairstyle, clothing, colors and accessories only. '
        'Place the replacement in the exact same image-space bounding box as the source performer: '
        'same head-center, shoulder line, torso center, hand positions and foot positions. '
        'Keep the same camera-relative body height, body width and footprint within the frame. Do not zoom, crop, enlarge, recenter, move the character forward or add a small character in front. '
        'Copy the visible head orientation and gaze, torso lean, shoulder rotation, elbow and wrist angles, knee bends and ankle positions from '+source+'. '
        'Do not copy the stance, gestures, head direction or framing from '+identity+'. '
        +environment_prompt(environment)+
        'The source geometry defines pose and contact points, not the appearance or presence of its original scenery. '
        'Replace contacted source props with the requested destination objects at corresponding contact points. '
        'Render the target character naturally integrated into the new environment, never as a pasted cutout. '
        'Preserve the output dimensions and camera perspective of '+source+'. '
        'Requested destination details: '+p['direction']+'\n')
    if p.get('anchor_pose_notes'):
        text+='Geometry notes for the performer in '+source+' only: '+p['anchor_pose_notes']+'\n'
    text+=(
        'Final frame requirements: one replacement character, source performer pose and scale, fully replaced destination environment. '
        'No original performer, no second actor, no retained source location, no inset image, no collage or character sheet.')
    return text


def pose_prompt(p, reference, environment=None):
    if environment:return three_image_prompt(p,('<image1>','<image2>','<image3>'))
    text=('Edit <image1> as the base canvas for ONE still frame. The source image has priority for '
          'pose, body scale, position, perspective and object contact. Copy the source performers\' '
          'head orientation and gaze, torso lean, shoulder rotation, elbow and wrist positions, '
          'knee bends and ankle locations. Keep their same camera-relative height and body footprint. '
          'Do not enlarge, recenter or turn a performer toward the camera. ')
    if reference:
        text+=('Use <image2> ONLY for the target identity, anatomy, clothing, materials and visual style. '
               'Replace the requested performers completely with the target characters, in the exact '
               'source poses. Do not copy the reference stance, arm gestures, camera distance or framing. ')
    text+=('Keep hands or paws at their source contact points and feet on their source support surfaces. '
           'Keep the complete source composition and perspective; do not paste a frontal character cutout. ')
    text+=('Reinterpret the entire source scene consistently in the requested target visual style. '
           if p.get('scene_mode')=='restyle' else 'Preserve the unedited source environment. ')
    text+='Appearance and style request: '+p['direction']+'\n'
    if p.get('anchor_pose_notes'):
        text+='Pose notes for this still frame only: '+p['anchor_pose_notes']+'\n'
    text+=('Any video or motion instructions above apply only to the visible pose in <image1> here. '
           'Make one complete edited image, not a collage or character sheet. Source pose and scale '
           'take priority over the appearance reference pose.')
    return text


def graph(p, frame, reference, ident, environment=None):
    if p.get('environment_reference_id') and not environment:
        raise ValueError('Falta cargar la imagen de escenario vinculada a esta guía.')
    if environment and (p.get('scene_mode')!='restyle' or not reference or p.get('original_reference') or p.get('style_anchor_id')):
        raise ValueError('La imagen de escenario requiere escena completa y una referencia de personaje independiente.')
    if digest(VENDOR) != VENDOR_SHA:
        raise ValueError('Cambió la copia del workflow Qwen Image Edit; se conserva el original.')
    w, h = map(int, p['resolution'].split('x'))
    scope = ('Reconstruct all characters, materials and environment in the requested target style. '
             if p.get('scene_mode') == 'restyle' else
             'Edit the selected character and preserve the remaining source scene. ')
    text = (p['direction'] + '\n' + scope +
        'Image 1 is the source pose and composition guide: preserve the requested performers\' relative positions, '
        'camera perspective, exact arm and leg poses and object contact relationships. '
        'Create a single complete frame at the requested dimensions, not a collage or a character sheet. ')
    if reference:
        text += ('Image 2 defines the target identities, clothing, anatomy and visual style. '
                 'Preserve those identities while placing them in the Image 1 performance pose. '
                 'The reference pose does not replace the source pose. ')
    if environment:text=three_image_prompt(p)
    values = {**MODELS, 'width': w, 'height': h, 'seed': p['seed'], 'steps': STEPS, 'cfg': 3,
              'prompt': text, 'negative_prompt': '', 'ref_image_1': frame,
              'ref_image_2': reference or frame, 'ref_image_3': environment or frame, 'ref_image_4': frame}
    source = VENDOR.read_text(encoding='utf-8')
    for key, value in values.items():
        source = source.replace('"{{'+key+'}}"', json.dumps(value)).replace('{{'+key+'}}', json.dumps(value))
    if '{{' in source: raise ValueError('Faltan valores del workflow Qwen Image Edit.')
    g = json.loads(source)
    # The installed editing recipe stays intact; unused reference slots and the
    # empty optional user-LoRA loader are removed from this private graph only.
    del g['simpligen_lora_1']
    g['sg_encode']['inputs']['clip'] = ['sg_clip', 0]
    g['sg_apg']['inputs']['model'] = ['sg_unet', 0]
    for index in ((4,) if environment else (3, 4)) if reference else (2, 3, 4):
        del g[f'sgopt_ref_image_{index}__load']
        del g['sg_encode']['inputs'][f'images.image_{index}']
    g['save_anchor'] = g.pop('sg_save')
    g['save_anchor']['inputs']['filename_prefix'] = f'gen_studio/{ident}/motion-qwen-anchor'
    recipe=p.get('qwen_recipe','legacy')
    if recipe not in PROFILE_STEPS:raise ValueError('Versión Qwen desconocida.')
    if recipe=='pose_v2':
        g['sg_encode']['inputs'].update(prompt=pose_prompt(p,reference,environment),negative_prompt='',resolution=0)
        g['sg_sampler']['inputs'].update(steps=25,cfg=1,sampler_name='euler',scheduler='simple',latent_image=['sg_encode',2])
        g['sg_cache']['inputs']['model']=['sg_unet',0]
        for name in ('sg_latent','sg_apg','sg_fresca','sg_negative'):g.pop(name,None)
    elif p.get('anchor_pose_notes') and not environment:
        g['sg_encode']['inputs']['prompt']+='\nStill-frame pose notes: '+p['anchor_pose_notes']
    return g
