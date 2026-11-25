import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / 'workflows' / 'minimax-h3.api.json'


def load_workflow():
    graph = json.loads(TEMPLATE.read_text(encoding='utf-8-sig'))
    if not graph or not isinstance(graph, dict) or 'nodes' in graph:
        raise ValueError('Se requiere un workflow en formato API.')
    for node_id, node in graph.items():
        if not isinstance(node, dict) or not isinstance(node.get('class_type'), str) or not isinstance(node.get('inputs'), dict):
            raise ValueError(f'Nodo API inválido: {node_id}')
    return graph


def describe_workflow():
    graph = load_workflow()
    manifest = json.loads((ROOT / 'workflows' / 'manifest.json').read_text(encoding='utf-8'))
    digest = hashlib.sha256(TEMPLATE.read_bytes()).hexdigest()
    return {
        'id': 'minimax-h3', 'name': 'MiniMax H3 · Texto a vídeo',
        'node_count': len(graph), 'sha256': digest,
        'integrity_ok': digest == manifest['sha256'].lower(),
        'evidence': manifest['evidence'],
        'width': graph['h3_conditioning']['inputs']['width'],
        'height': graph['h3_conditioning']['inputs']['height'],
        'seconds': graph['h3_duration']['inputs']['value'],
        'fps': graph['h3_create_video']['inputs']['fps'],
        'steps': graph['h3_sigmas']['inputs']['steps'],
        'nodes': [{'id': key, 'type': node['class_type'], 'title': node.get('_meta', {}).get('title', key)} for key, node in graph.items()],
    }


async def preflight(client, comfy_url):
    return await check_graph(client, comfy_url, load_workflow(), describe_workflow()['integrity_ok'])


async def check_graph(client, comfy_url, graph, integrity=True, skip_inputs=None):
    response = await client.get(comfy_url + '/object_info')
    response.raise_for_status()
    catalog = response.json()
    errors = []
    for node_id, node in graph.items():
        spec = catalog.get(node['class_type'])
        if not spec:
            errors.append(f"{node_id}: falta {node['class_type']}")
            continue
        sections = spec.get('input', {})
        required = sections.get('required', {})
        fields = {**required, **sections.get('optional', {})}
        for name in required:
            if name not in node['inputs']:
                field=required[name]
                dynamic=field[0]=='COMFY_AUTOGROW_V3'
                options=field[1] if len(field)>1 and isinstance(field[1],dict) else {}
                template=options.get('template',{})
                minimum=template.get('min',1)
                children=sum(k.startswith(name+'.') for k in node['inputs'])
                if not dynamic or children<minimum:
                    errors.append(f'{node_id}: falta la entrada {name}')
        for name, value in node['inputs'].items():
            if skip_inputs and (node_id, name) in skip_inputs:
                continue
            field = fields.get(name)
            image_upload = bool(field and len(field) > 1 and isinstance(field[1], dict) and field[1].get('image_upload'))
            # ComfyUI declares model filenames and other choices as lists.
            if field and not image_upload and isinstance(field[0], list) and not isinstance(value, list) and value not in field[0]:
                errors.append(f'{node_id}: opción o modelo no disponible: {name}={value}')
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and isinstance(value[1], int):
                source = graph.get(value[0])
                outputs = catalog.get(source['class_type'], {}).get('output', []) if source else []
                if not source or not 0 <= value[1] < len(outputs):
                    errors.append(f'{node_id}: enlace inválido en {name}')
    if not integrity:
        errors.append('La copia del workflow no coincide con el SHA256 auditado.')
    return {'ok': not errors, 'errors': errors, 'node_count': len(graph),
            'scope': 'Compatibilidad estática de nodos, opciones y enlaces; no ejecuta el workflow.'}


CHARACTER_TEMPLATE = ROOT / 'workflows/character-replacement.api.json'


def character_graph():
    return json.loads(CHARACTER_TEMPLATE.read_text(encoding='utf-8-sig'))


def character_description():
    graph = character_graph()
    manifest = json.loads((ROOT / 'workflows/character-replacement.manifest.json').read_text(encoding='utf-8-sig'))
    digest = hashlib.sha256(CHARACTER_TEMPLATE.read_bytes()).hexdigest()
    return {'id': 'character-replacement', 'name': 'Character Replacement · MiniMax H3',
            'node_count': len(graph), 'sha256': digest, 'integrity_ok': digest == manifest['sha256'],
            'evidence': manifest['evidence'], 'defaults': {
                'prompt': graph['8']['inputs']['prompt'], 'subject': graph['18']['inputs']['text'],
                'resolution': '960x544', 'frames': graph['8']['inputs']['length'],
                'steps': graph['10']['inputs']['steps'], 'seed': graph['12']['inputs']['noise_seed'],
                'lora_strength': graph['2']['inputs']['strength_model']},
            'nodes': [{'id': key, 'type': node['class_type'], 'title': node.get('_meta', {}).get('title', node['class_type'])} for key, node in graph.items()]}


def prepare_character(payload, video_path, reference_name, job_id, audio=False, detail_reference_name=None):
    if not character_description()['integrity_ok']:
        raise ValueError('La copia base de Character Replacement fue modificada.')
    graph = character_graph()
    width, height = map(int, payload['resolution'].split('x'))
    graph['6']['inputs']['image'] = reference_name
    graph['7']['inputs'].update(video=str(video_path), custom_width=width, custom_height=height,
                                frame_load_cap=payload['frames'], force_rate=24, start_time=0, format='None')
    graph['8']['inputs'].update(prompt=payload['prompt'], width=width, height=height, length=payload['frames'])
    graph['8']['inputs']['ref_image_size'] = payload.get('ref_image_size', 'match')
    if payload.get('guide_face_reference'):
        if not detail_reference_name:
            raise ValueError('Falta el primer plano cargado para guiar la apariencia en H3.')
        graph['31'] = {'class_type': 'LoadImage', 'inputs': {'image': detail_reference_name}}
        graph['8']['inputs']['ref_images.ref_image_1'] = ['31', 0]
    graph['10']['inputs']['steps'] = payload['steps']
    graph['12']['inputs']['noise_seed'] = payload['seed']
    graph['2']['inputs']['strength_model'] = payload['lora_strength']
    graph['18']['inputs']['text'] = payload['subject']
    graph['21']['inputs']['grow_spatial'] = payload.get('mask_grow_spatial', 16)
    graph['16']['inputs']['filename_prefix'] = f'gen_studio/{job_id}/character'
    if payload.get('preserve_face'):
        graph['32'] = {'class_type': 'MaskToImage', 'inputs': {'mask': ['20', 0]}}
        graph['33'] = {'class_type': 'CreateVideo', 'inputs': {'images': ['32', 0], 'fps': 24}}
        graph['34'] = {'class_type': 'SaveVideo', 'inputs': {'video': ['33', 0],
                       'filename_prefix': f'gen_studio/{job_id}/subject-mask', 'format': 'mp4', 'codec': 'h264'}}
    if audio:
        graph['15']['inputs']['audio'] = ['7', 2]
    return graph
