"""Read-only registry for local Qwen language models.

Catalog probes never load/unload a model, acquire a GPU lease, or change a job.
Resolution checks readiness at one point in time; callers still need the existing
EngineCoordinator before inference. Preferences are isolated by authenticated workspace.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException, Query

DEFAULT_MODEL_ID = 'qwen3.8:27b'
AGGRESSIVE_MODEL_ID = 'qwen38-27b-hauhau-aggressive:latest'
_EVIDENCE = json.loads(Path(__file__).with_name('qwen_models_evidence.json').read_text(encoding='utf-8'))
_MODELS = {item['id']: item for item in _EVIDENCE['models']}
_DECLARED = {'text': 'completion', 'vision': 'vision', 'tools': 'tools', 'structured_json': 'completion'}
_CAPABILITIES = frozenset(next(iter(_MODELS.values()))['verified_capabilities'])


class ModelResolutionError(ValueError):
    def __init__(self, code, message, status_code=409):
        super().__init__(message)
        self.code, self.status_code = code, status_code


def default_model_id():
    """Compatibility default; independent of settings and any historical job."""
    return DEFAULT_MODEL_ID


def model_definition(model_id=None):
    ident = default_model_id() if model_id is None else model_id
    if not isinstance(ident, str) or ident not in _MODELS:
        raise ModelResolutionError('unknown_model', 'El modelo Qwen solicitado no está registrado.', 404)
    return deepcopy(_MODELS[ident])


def runtime_endpoint(settings):
    base = settings.get('ollama_url')
    if not isinstance(base, str):
        raise ModelResolutionError('runtime_not_configured', 'Falta la URL de Ollama.', 503)
    try:
        url = urlsplit(base)
        url.port
    except ValueError as error:
        raise ModelResolutionError('invalid_runtime_endpoint', 'La URL configurada de Ollama no es válida.', 503) from error
    if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ModelResolutionError('invalid_runtime_endpoint', 'La URL configurada de Ollama no es válida.', 503)
    return base.rstrip('/')


def _model_list(data, *, require_digest=False):
    values = data.get('models')
    if not isinstance(values, list):
        raise ValueError('Invalid model list')
    result = {}
    for item in values:
        if not isinstance(item, dict):
            raise ValueError('Invalid model item')
        name = item.get('name') or item.get('model')
        if not isinstance(name, str) or name in result:
            raise ValueError('Invalid or duplicate model identity')
        if require_digest and not isinstance(item.get('digest'), str):
            raise ValueError('Missing digest')
        result[name] = item
    return result


async def _read(client, base, path, body=None):
    try:
        if body is None:
            response = await client.get(base + path, timeout=5)
        else:
            # Ollama /api/show uses POST but only reads model metadata.
            response = await client.post(base + path, json=body, timeout=5)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError('Expected object')
        if path == '/api/tags':
            _model_list(data, require_digest=True)
        elif path == '/api/ps':
            _model_list(data)
        elif path == '/api/version':
            if not isinstance(data.get('version'), str) or not data['version']:
                raise ValueError('Missing runtime version')
        elif path == '/api/show':
            caps = data.get('capabilities')
            if not isinstance(caps, list) or any(not isinstance(x, str) for x in caps):
                raise ValueError('Missing capabilities')
        return {'ok': True, 'data': data}
    except httpx.HTTPStatusError as error:
        return {'ok': False, 'error': {'code': 'http_error', 'status_code': error.response.status_code}}
    except httpx.HTTPError:
        return {'ok': False, 'error': {'code': 'runtime_unreachable'}}
    except (ValueError, TypeError):
        return {'ok': False, 'error': {'code': 'invalid_runtime_response'}}


async def catalog(client, settings):
    """Return public metadata, bounded read-only runtime status, and verified evidence."""
    base = runtime_endpoint(settings)
    paths = ('/api/version', '/api/tags', '/api/ps')
    values = await asyncio.gather(*(_read(client, base, path) for path in paths))
    probes = dict(zip(paths, values))
    version = probes['/api/version'].get('data', {}).get('version')
    installed = _model_list(probes['/api/tags']['data']) if probes['/api/tags']['ok'] else {}
    loaded = _model_list(probes['/api/ps']['data']) if probes['/api/ps']['ok'] else {}
    ids = [ident for ident in _MODELS if ident in installed]
    shows = await asyncio.gather(*(_read(client, base, '/api/show', {'model': ident}) for ident in ids))
    shows = dict(zip(ids, shows))
    models = []
    for ident in _MODELS:
        item = model_definition(ident)
        evidence = item.pop('verified_capabilities')
        show = shows.get(ident, {'ok': False})
        declared = show.get('data', {}).get('capabilities', [])
        observed_digest = installed.get(ident, {}).get('digest')
        reason = None
        if not all(p['ok'] for p in probes.values()):
            reason = 'runtime_unavailable'
        elif ident not in installed:
            reason = 'model_missing'
        elif observed_digest != item['evidence']['model_digest']:
            reason = 'model_changed'
        elif version != item['evidence']['runtime_version']:
            reason = 'runtime_changed'
        elif not show['ok']:
            reason = 'model_metadata_unavailable'
        elif not all(_DECLARED[c] in declared for c in _DECLARED if evidence.get(c)):
            reason = 'capabilities_changed'
        elif ident in loaded and loaded[ident].get('digest') != observed_digest:
            reason = 'loaded_model_changed'
        verified = {cap: bool(value and reason is None and (_DECLARED.get(cap) is None or _DECLARED[cap] in declared))
                    for cap, value in evidence.items()}
        active = loaded.get(ident)
        # Do not expose paths, prompts, or unknown models loaded by another application.
        load_state = None if not probes['/api/ps']['ok'] else bool(active)
        numeric = {key: active.get(key) for key in ('size', 'size_vram', 'context_length')
                   if active and type(active.get(key)) is int and active[key] >= 0}
        item.update(runtime='ollama', endpoint=base, installed=None if not probes['/api/tags']['ok'] else ident in installed,
                    available=reason is None, unavailable_reason=reason, loaded=load_state, load=numeric,
                    declared_capabilities=declared, verified_capabilities=verified,
                    observed_digest=observed_digest, evidence_matches=reason is None,
                    metadata_error=show.get('error'))
        item['evidence']['tested_capabilities'] = evidence
        models.append(item)
    ok = sum(p['ok'] for p in probes.values())
    return {'default_model_id': default_model_id(), 'selection_enabled': True,
            'checked_at': datetime.now(timezone.utc).isoformat(),
            'runtime': {'name': 'ollama', 'endpoint': base, 'version': version,
                        'status': 'online' if ok == len(paths) else ('offline' if ok == 0 else 'degraded'),
                        'errors': {path: p['error'] for path, p in probes.items() if not p['ok']}},
            'models': models}


async def resolve_model(client, settings, model_id=None, *, required_capabilities=('text',), context_tokens=None):
    """Validate an explicit choice without fallback, selection, loading, or GPU reservation."""
    definition = model_definition(model_id)
    if not isinstance(required_capabilities, (tuple, list)) or any(not isinstance(cap, str) or cap not in _CAPABILITIES for cap in required_capabilities):
        raise ModelResolutionError('unknown_capability', 'La capacidad solicitada no está registrada.', 422)
    if context_tokens is None:
        context_tokens = definition['context']['default_tokens']
    if type(context_tokens) is not int or context_tokens < 1:
        raise ModelResolutionError('invalid_context', 'El contexto debe ser un entero positivo.', 422)
    if context_tokens > definition['context']['validated_setting_tokens']:
        raise ModelResolutionError('context_unverified', 'Ese contexto aún no se ha comprobado para este modelo.', 422)
    result = await catalog(client, settings)
    item = next(m for m in result['models'] if m['id'] == definition['id'])
    if not item['available']:
        reason = item['unavailable_reason']
        raise ModelResolutionError(reason, 'El modelo solicitado no está disponible con la configuración verificada.',
                                   503 if reason in ('runtime_unavailable', 'model_metadata_unavailable') else 409)
    if any(not item['verified_capabilities'][cap] for cap in required_capabilities):
        raise ModelResolutionError('capability_unverified', 'Una capacidad solicitada aún no está verificada.', 422)
    return {**item, 'requested_context_tokens': context_tokens}


def register(app, settings):
    def service():
        return app.state.jobs.current

    def failure(error):
        return HTTPException(error.status_code, detail={'code': error.code, 'message': str(error)})

    from .qwen_preferences import register as register_preferences
    register_preferences(app)

    @app.get('/api/qwen/models')
    async def list_models():
        current = service()
        try:
            result = await catalog(current.client, current.settings)
            result['selection'] = current.store.qwen_preference()
            return result
        except ModelResolutionError as error:
            raise failure(error) from error

    @app.get('/api/qwen/models/{model_id}/resolve')
    async def resolve(model_id: str, capability: list[str] = Query(default=['text']),
                      context_tokens: int | None = Query(default=None, ge=1)):
        current = service()
        try:
            return await resolve_model(current.client, current.settings, model_id,
                                       required_capabilities=capability, context_tokens=context_tokens)
        except ModelResolutionError as error:
            raise failure(error) from error

    @app.get('/api/qwen/models/{model_id}')
    async def describe(model_id: str):
        try:
            model_definition(model_id)
            result = await list_models()
            return next(m for m in result['models'] if m['id'] == model_id)
        except ModelResolutionError as error:
            raise failure(error) from error
