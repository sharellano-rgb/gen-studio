"""Pinned, per-job Sol-Attn attention policy for explicitly enabled video methods.

Only new jobs receive a profile. Immutable request payloads and accepted ComfyUI
graphs remain unchanged; internal children inherit their parent's snapshot.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import PurePosixPath

VERSION = 'video-acceleration-v1'
POLICY = 'sol-attn-conservative-v1'
NODE = 'BlockSparseAttention'
METHODS = frozenset({'h3_direct', 'h3_alisson', 'h3_ninja', 'bunny', 'tara', 'motion_layers'})
SOL_INPUTS = {
    'selection': 'sol-attn', 'selection.tau': 1.0,
    'start_percent': 0.2, 'end_percent': 1.0, 'dense_blocks': '0, 1',
    'min_tokens': 12288, 'extra_tokens': 256,
    'sink_conditioning': 'exact_kv_and_rows', 'verbose': True,
}
MODELS = frozenset({
    'fasth3_omniref_8step_pruned_int8_convrot.safetensors',
    'minimax-h3_singularity_ref2va_v1.3_int8.safetensors',
    'minimax_h3_ref2va_pruned_int8_convrot.safetensors',
    'minimax_h3_hybrid_fl2va_ref2va_b25-49-int8.safetensors',
})
MODEL_CHAIN = frozenset({
    'LoraLoaderModelOnly', 'MiniMaxH3MemoryEfficientSageAttentionPatch',
    'PathchSageAttentionKJ', 'MiniMaxChunkFeedForward', 'ModelPatchTorchSettings',
    'MiniMaxH3SigmaShift', 'ModelAttentionBackend', 'GenStudioTaraH3Temporal',
})


def method(payload):
    engine, operation = payload.get('engine'), payload.get('operation')
    if engine == 'h3_direct' and operation in (None, 'long_character'):
        return 'h3_direct'
    if engine == 'h3_alisson':
        return 'h3_alisson'
    if engine == 'h3_ninja' and operation in (
            'ninja_prepare', 'ninja_render', 'ninja_shots_plan', 'ninja_shots_prepare', 'ninja_shots_render'):
        return 'h3_ninja'
    if engine == 'viggle_motion' and operation == 'motion_layers':
        return 'motion_layers'
    if engine == 'viggle_motion' and operation == 'motion_bunny':
        return 'tara' if payload.get('transfer_method') == 'tara' else 'bunny'
    return None


def configuration(settings):
    """Enable only methods with a completed benchmark, never an implicit all."""
    config = (settings or {}).get('video_acceleration') or {}
    if not isinstance(config, dict) or type(config.get('enabled', False)) is not bool:
        raise ValueError('Configuración de aceleración de vídeo no válida.')
    allowed = config.get('allowed_methods', [])
    if not isinstance(allowed, list) or any(not isinstance(value, str) or value not in METHODS for value in allowed):
        raise ValueError('Método de aceleración de vídeo no válido.')
    return {'enabled': config.get('enabled', False), 'allowed_methods': sorted(set(allowed))}


def native(reason):
    return {'version': VERSION, 'mode': 'native', 'reason': reason}


def public_status(config):
    """Expose configured admission policy, not private jobs or speed claims."""
    checked = configuration({'video_acceleration': config})
    allowed = checked['allowed_methods']
    return {
        'name': 'Sol-Attn', 'version': VERSION, 'policy': POLICY, **checked,
        'new_jobs_only': True, 'dense_fallback_allowed': True,
        'exclusions': {'vsa_combination': True, 'tara': 'tara' not in allowed},
        'excluded_methods': sorted(METHODS - set(allowed)),
    }


def validate_profile(profile):
    if not isinstance(profile, dict) or profile.get('version') != VERSION:
        raise ValueError('Perfil de aceleración de vídeo no compatible.')
    if profile.get('mode') == 'native':
        return deepcopy(profile)
    expected = {'version': VERSION, 'mode': 'sol', 'policy': POLICY, 'inputs': SOL_INPUTS}
    if profile != expected:
        raise ValueError('El perfil Sol-Attn no coincide con la receta verificada.')
    return deepcopy(profile)


def parent_id(payload):
    # Only implementation children, not user-created variants or reused videos.
    if payload.get('long_parent_id') and type(payload.get('long_segment_index')) is int:
        return payload['long_parent_id']
    return payload.get('sequence_parent_id') or payload.get('ninja_sequence_owner') or payload.get('hybrid_parent_id')


def creation_profile(db, payload, config):
    """Called inside the Store insertion transaction, after idempotence lookup."""
    parent = parent_id(payload)
    if parent:
        row = db.execute('SELECT state FROM jobs WHERE id=?', (parent,)).fetchone()
        if not row:
            # Never attach a new policy to an unknown/foreign parent.
            return native('parent-unavailable')
        previous = json.loads(row[0]).get('video_acceleration')
        return validate_profile(previous) if previous else native('legacy-parent')
    selected = method(payload)
    if selected is None:
        return None
    if payload.get('direct_preset')=='fasth3_omni':
        if payload.get('omni_experiment')=='sol':
            return {'version': VERSION, 'mode': 'sol', 'policy': POLICY, 'inputs': deepcopy(SOL_INPUTS)}
        return native('fasth3-omni-pilot')
    if payload.get('direct_vsa'):
        return native('vsa-selected')
    if not config['enabled']:
        return native('disabled')
    if selected not in config['allowed_methods']:
        return native('method-not-enabled')
    return {'version': VERSION, 'mode': 'sol', 'policy': POLICY, 'inputs': deepcopy(SOL_INPUTS)}


def graph_hash(graph):
    return hashlib.sha256(json.dumps(graph, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def _link(value):
    return isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and type(value[1]) is int


def _sampling_roots(graph):
    roots = []
    for node in graph.values():
        kind, inputs = node.get('class_type'), node.get('inputs', {})
        if kind in ('KSampler', 'KSamplerAdvanced'):
            roots.append(inputs.get('model'))
        elif kind == 'SamplerCustomAdvanced':
            guide = inputs.get('guider')
            if not _link(guide):
                raise ValueError('Sol-Attn no reconoce el guider del sampler.')
            guider = graph.get(guide[0], {})
            if guider.get('class_type') not in ('BasicGuider', 'CFGGuider'):
                raise ValueError('Sol-Attn requiere un guider H3 compatible.')
            roots.append(guider.get('inputs', {}).get('model'))
    return roots


def _chain(graph, root):
    seen, chain = set(), []
    current = root
    while _link(current) and current[1] == 0:
        key = current[0]
        if key in seen:
            raise ValueError('La cadena de modelo Sol-Attn contiene un ciclo.')
        seen.add(key)
        node = graph.get(key, {})
        kind, inputs = node.get('class_type'), node.get('inputs', {})
        chain.append(key)
        if kind == 'UNETLoader':
            filename = PurePosixPath(str(inputs.get('unet_name', '')).replace('\\', '/')).name.lower()
            return chain if filename in MODELS else None
        if kind not in MODEL_CHAIN | {NODE}:
            return None
        current = inputs.get('model')
    return None


def apply(graph, state):
    """Return a graph copy and receipt. Never mutate inputs or accepted graphs.

    SOL sits after existing dense-backend/model patches. For TARA it goes
    immediately before the temporal node, which preserves temporal alignment.
    """
    profile = state.get('video_acceleration')
    result = deepcopy(graph)
    if profile is None or validate_profile(profile)['mode'] == 'native':
        return result, None
    if method(state.get('parameters') or {}) is None:
        return result, None
    if (state.get('parameters') or {}).get('direct_vsa') or any(
            'vsa' in node.get('class_type', '').lower() for node in graph.values()):
        return result, {'policy': POLICY, 'mode': 'native', 'reason': 'vsa-selected', 'graph_sha256': graph_hash(result)}
    # A mixed/image/preparation graph must never acquire an H3 model patch.
    roots = _sampling_roots(graph)
    if not roots:
        return result, None
    chains = [_chain(graph, root) for root in roots]
    if any(chain is None for chain in chains):
        if any(node.get('class_type') == 'UNETLoader' and
               PurePosixPath(str(node.get('inputs', {}).get('unet_name', '')).replace('\\', '/')).name.lower() in MODELS
               for node in graph.values()):
            raise ValueError('La cadena del modelo H3 contiene un parche no validado para SOL.')
        return result, None
    additions = {}
    for root, chain in zip(roots, chains):
        existing = [key for key in chain if graph[key]['class_type'] == NODE]
        if existing:
            if len(existing) != 1 or {k: v for k, v in graph[existing[0]]['inputs'].items() if k != 'model'} != SOL_INPUTS:
                raise ValueError('El grafo ya contiene otro perfil de atención dispersa.')
            additions[existing[0]] = existing[0]
            continue
        temporal = [key for key in chain if graph[key]['class_type'] == 'GenStudioTaraH3Temporal']
        if len(temporal) > 1:
            raise ValueError('Sol-Attn no admite múltiples parches temporales en una cadena.')
        target = graph[temporal[0]]['inputs']['model'] if temporal else root
        patch_key = 'genstudio-sol-' + target[0]
        if patch_key in additions:
            continue
        if patch_key in graph:
            raise ValueError('El identificador del nodo Sol-Attn ya está ocupado.')
        additions[patch_key] = patch_key
        if temporal:
            result[temporal[0]]['inputs']['model'] = [patch_key, 0]
        else:
            for node in result.values():
                if node.get('inputs', {}).get('model') == target:
                    node['inputs']['model'] = [patch_key, 0]
        result[patch_key] = {'class_type': NODE, 'inputs': {'model': deepcopy(target), **deepcopy(SOL_INPUTS)}}
    return result, {'policy': POLICY, 'mode': 'sol', 'execution': 'requested', 'dense_fallback_allowed': True,
                    'nodes': sorted(additions), 'graph_sha256': graph_hash(result)}


def prepare_phase(service, state, name, graph):
    """Durably tie the chosen policy to each submitted phase, before POST."""
    result, receipt = apply(graph, state)
    records = deepcopy(state.get('video_acceleration_phases') or {})
    prior = records.get(name)
    if prior is not None and prior != receipt:
        raise ValueError('El grafo de aceleración cambió; se conserva el envío anterior.')
    if receipt is not None:
        if prior is None:
            records[name] = receipt
            service.store.update(state['id'], video_acceleration_phases=records)
    return result
