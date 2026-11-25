"""Task-local model identity; preference changes never retarget accepted work."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
import inspect
import json

from .qwen_models import AGGRESSIVE_MODEL_ID, default_model_id, model_definition, resolve_model, ModelResolutionError

FIELD = 'qwen_model_binding'
MTP_PROFILE = 'aggressive-native-mtp4-v1'
_active = ContextVar('qwen_model_binding', default=None)
_workspace = ContextVar('qwen_model_workspace', default=None)


def make_binding(model_id=None, revision=0):
    item = model_definition(model_id)
    value = {'version': 1, 'model_id': item['id'], 'model_digest': item['evidence']['model_digest'],
             'runtime_version': item['evidence']['runtime_version'], 'selection_revision': revision}
    if item['id'] == AGGRESSIVE_MODEL_ID and item['mtp'].get('gen_studio_new_work_enabled') is True:
        value.update(version=2, inference_profile=MTP_PROFILE)
    return value


def checked_binding(value):
    if not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] not in (1, 2):
        raise ValueError('La asignación de Qwen no es válida.')
    expected = make_binding(value.get('model_id'))
    if any(value.get(k) != expected[k] for k in ('model_id', 'model_digest', 'runtime_version')):
        raise ValueError('Cambió la identidad de Qwen; conserva el modelo asignado a esta toma.')
    if type(value.get('selection_revision')) is not int or value['selection_revision'] < 0:
        raise ValueError('La revisión de Qwen no es válida.')
    if value['version'] == 2:
        if value['model_id'] != AGGRESSIVE_MODEL_ID or value.get('inference_profile') != MTP_PROFILE:
            raise ValueError('El perfil de inferencia Qwen no es válido.')
    elif 'inference_profile' in value:
        raise ValueError('La asignación histórica no admite un perfil nuevo.')
    return deepcopy(value)


def current_binding():
    return deepcopy(_active.get())


def current_model():
    return (_active.get() or {}).get('model_id', default_model_id())


def cache_identity():
    value = _active.get()
    # Legacy cache hashes remain byte-for-byte unchanged. New caches include
    # the immutable model/runtime, but not the preference's changing revision.
    if not value:
        return {}
    keys = ('version', 'model_id', 'model_digest', 'runtime_version')
    if value['version'] == 2:
        keys += ('inference_profile',)
    return {FIELD: {k: value[k] for k in keys}}


def cache_suffix():
    return json.dumps(cache_identity(), sort_keys=True) if _active.get() else ''


def selected_binding(service):
    preference = service.store.qwen_preference()
    return make_binding(preference['model_id'], preference['revision'])


def record_binding():
    value = current_binding()
    return {FIELD: value} if value else {}


@contextmanager
def scope(record, service=None):
    # Absence means the historical original, never today's preference.
    value = record.get(FIELD)
    token = _active.set(checked_binding(value) if value is not None else None)
    owner = getattr(getattr(service, 'store', None), 'path', None)
    owner_token = _workspace.set(str(owner) if owner is not None else None)
    try:
        yield
    finally:
        _active.reset(token)
        _workspace.reset(owner_token)


def bound(function):
    """Bind a service + job/analysis-record entry point, including thread calls."""
    if inspect.iscoroutinefunction(function):
        @wraps(function)
        async def asynchronous(service, record, *args, **kwargs):
            with scope(record, service):
                return await function(service, record, *args, **kwargs)
        return asynchronous
    @wraps(function)
    def synchronous(service, record, *args, **kwargs):
        with scope(record, service):
            return function(service, record, *args, **kwargs)
    return synchronous


async def chat(service, *, json, timeout):
    """Only transport gateway for language Qwen; callers retain their GPU lease."""
    from .tessera_runtime import release_idle
    owner = _workspace.get()
    if owner is not None and owner != str(service.store.path):
        raise ValueError('El análisis Qwen pertenece a otro espacio.')
    await release_idle(service)
    payload = deepcopy(json)
    model = current_model()
    payload['model'] = model
    binding = _active.get()
    mtp_profile = bool(binding and binding['version'] == 2)
    required = ['text', 'mtp'] if mtp_profile else ['text']
    if any(message.get('images') for message in payload.get('messages', [])):
        required.append('vision')
    resolved = await resolve_model(service.client, service.settings, model,
        required_capabilities=required, context_tokens=payload.get('options', {}).get('num_ctx'))
    if binding and resolved['observed_digest'] != binding['model_digest']:
        raise ModelResolutionError('binding_changed', 'El modelo ya no coincide con el asignado a esta toma.')
    if mtp_profile:
        if resolved['mtp'].get('gen_studio_new_work_enabled') is not True:
            raise ModelResolutionError('inference_profile_disabled', 'El perfil MTP de esta toma está suspendido; no se cambia su configuración automáticamente.')
        if payload.get('think') is not False:
            raise ModelResolutionError('inference_profile_options_unverified', 'Este perfil MTP requiere razonamiento desactivado, como en las pruebas verificadas.', 422)
    # Old Aggressive work remains draft=0; new versioned work uses draft=4.
    # Original and unbound historical work retain their existing options.
    if model == AGGRESSIVE_MODEL_ID:
        payload.setdefault('options', {})['draft_num_predict'] = 4 if mtp_profile else 0
    return await service.client.post(service.settings['ollama_url'] + '/api/chat', json=payload, timeout=timeout)
