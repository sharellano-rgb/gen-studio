"""Adaptadores NSFW opcionales por tomas. Desactivados por defecto; fusion temporal no permanente."""
import hashlib
import json
from pathlib import Path

ADAPTERS = (
    ('nsfw_h3_motion_booster', 'H3_Motion_BoosterV2.safetensors'),
    ('nsfw_penis_v2', 'PenisV2_minimax-h3_epoch60.safetensors'),
    ('nsfw_hm_pussy', 'HMPussyV2.safetensors'),
    ('nsfw_minimax_unlocked', 'Minimax_H3_Unlocked_V2.1.safetensors'),
)
MARKER = 'nsfw-adapter-0'
MODEL_ROOT = Path(__file__).resolve().parents[1] / 'models/loras'


def _strength(payload):
    raw = payload.get('nsfw_strength', 1.0)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError('nsfw_strength debe ser un numero entre 0 y 2.')
    return max(0.0, min(2.0, value))


def _enabled(payload):
    return [(k, f) for (k, f) in ADAPTERS if payload.get(k) is True]


def _unet(graph):
    ids = [i for i, n in graph.items() if n.get('class_type') == 'UNETLoader']
    if len(ids) != 1:
        raise ValueError('Se esperaba exactamente un UNETLoader para insertar los adaptadores NSFW.')
    return ids[0]


def _consumers(graph, target):
    hits = []
    for i, n in graph.items():
        for v in n.get('inputs', {}).values():
            if isinstance(v, (list, tuple)) and len(v) == 2 and v[0] == target[0] and v[1] == target[1]:
                hits.append(i)
    return hits


def apply_optional_adapters(graph, payload):
    """Inserta la cadena de adaptadores NSFW justo tras el UNET y re-apunta a sus lectores. Idempotente."""
    if MARKER in graph:
        return graph
    enabled = _enabled(payload)
    strength = _strength(payload)
    if not enabled or strength <= 0:
        return graph
    unet = _unet(graph)
    consumers = _consumers(graph, [unet, 0])
    prev = [unet, 0]
    for i, (_key, name) in enumerate(enabled):
        nid = 'nsfw-adapter-%d' % i
        graph[nid] = {
            'class_type': 'LoraLoaderModelOnly',
            'inputs': {'model': list(prev), 'lora_name': name, 'strength_model': strength},
        }
        prev = [nid, 0]
    for cid in consumers:
        inputs = graph[cid]['inputs']
        for k, v in list(inputs.items()):
            if isinstance(v, (list, tuple)) and len(v) == 2 and v[0] == unet and v[1] == 0:
                inputs[k] = list(prev)
    return graph


def applied(payload):
    strength = _strength(payload)
    return [{'key': key, 'lora': name, 'strength': strength}
            for key, name in ADAPTERS if payload.get(key) is True and strength > 0]


def verify(payload, model_root=None):
    """Falla si falta un adaptador solicitado o su tamano/SHA no coinciden; devuelve los registros verificados."""
    root = Path(model_root or MODEL_ROOT)
    table = {}
    manifest = root / 'phase2_adapters_manifest.json'
    if manifest.is_file():
        table = json.loads(manifest.read_text(encoding='utf-8'))
    strength = _strength(payload)
    records = []
    for key, name in ADAPTERS:
        if payload.get(key) is not True or strength <= 0:
            continue
        path = root / name
        if not path.is_file():
            raise ValueError('Falta el adaptador NSFW: ' + name)
        size = path.stat().st_size
        if not size:
            raise ValueError('El adaptador NSFW esta vacio: ' + name)
        expected = table.get(name, {}).get('bytes')
        if expected is not None and size != expected:
            raise ValueError('El tamano de %s no coincide con el manifieste.' % name)
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
                digest.update(chunk)
        sha = digest.hexdigest()
        expected_sha = table.get(name, {}).get('dst_sha256')
        if expected_sha and sha != expected_sha:
            raise ValueError('El SHA-256 de %s no coincide con el manifieste.' % name)
        records.append({'key': key, 'lora': name, 'strength': strength, 'sha256': sha, 'bytes': size})
    return records
