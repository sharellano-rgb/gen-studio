"""Ordered, immutable source-to-reference assignments; no model or sampler changes."""
VERSION='viggle-characters-1'

def assignments(p):
    return [{'target':p['target'],'reference_id':p['reference_id']}, *p.get('additional_characters',[])]

def targets(p):
    return [c['target'] for c in assignments(p)]

def references(service,p,verify=True):
    from ..qwen_director import asset
    from .state import digest
    rows=assignments(p)
    hashes=[p.get('reference_sha256'),*p.get('additional_reference_sha256',[])]
    if p.get('additional_characters') and p.get('viggle_characters_version')!=VERSION:
        raise ValueError('Falta la versión de asignaciones múltiples de Viggle.')
    paths=[asset(service,c['reference_id'],'image') for c in rows]
    if verify and (len(hashes)!=len(paths) or any(not sha or digest(path)!=sha for path,sha in zip(paths,hashes))):
        raise ValueError('Cambió una referencia de los personajes; crea otra toma.')
    return paths
