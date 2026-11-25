"""Opt-in opening-frame localization for H3 Direct over the existing private cable."""
import asyncio
import hashlib
import json

import httpx

from .h3_intel_planner import cable_client

PROTOCOL = 'genstudio-h3-location-v1'
PORT = 19444


def connection(profile):
    return cable_client(profile['ryzen_cert'], profile['ryzen_key'], profile['intel_cert'], port=PORT)


async def ready(client, profile):
    response = await client.get('/ready', timeout=4)
    response.raise_for_status()
    if response.json() != {'protocol': PROTOCOL, 'model': profile['model'], 'ready': True}:
        raise ValueError('El servicio de Intel no confirmó el modelo y el protocolo de visión.')


async def available(profile):
    try:
        async with connection(profile) as client:
            await ready(client, profile)
        return True
    except (ValueError, KeyError, TypeError, httpx.HTTPError, OSError):
        return False


async def locate(service, schema, rules, inputs, image, limit):
    profile = service.settings['h3_intel_planner']
    owner = service.coordinator or service
    if not hasattr(owner, 'h3_intel_slot'):
        owner.h3_intel_slot = asyncio.Lock()
    contract = hashlib.sha256(json.dumps([rules, schema.model_json_schema(), limit],
                                         sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    body = {'contract': contract, 'inputs': inputs, 'image': image}
    request_hash = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    try:
        async with owner.h3_intel_slot, connection(profile) as client:
            await ready(client, profile)
            reply = await client.post('/locate', json=body)
            reply.raise_for_status()
            data = reply.json()
            if data['request_hash'] != request_hash or data['model'] != profile['model']:
                raise ValueError('La respuesta de visión de Intel corresponde a otra solicitud o modelo.')
            return schema.model_validate(data['location'], strict=True)
    except (OSError, httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        detail = f' (HTTP {status})' if status else ''
        raise ValueError(f'Intel no completó la localización visual{detail}. Comprueba el puente visual y Qwen en la Intel antes de reintentar el tramo.') from exc
