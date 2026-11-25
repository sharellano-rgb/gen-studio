"""Versioned character metadata only. No model execution or native job mutation."""
import hashlib
import math
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .repository import Problem
from .analysis_store import AnalysisStore


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class Reference(StrictModel):
    id: UUID
    asset_id: UUID
    role: Literal['front', 'profile', 'body', 'wardrobe', 'accessory', 'acting', 'voice']
    sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    name: str = Field(default='', max_length=500)


class TraitObservation(StrictModel):
    project_id: UUID
    analysis_id: UUID
    window_index: int = Field(ge=0, le=95, strict=True)
    actor_index: int = Field(ge=0, le=31, strict=True)


class Evidence(StrictModel):
    reference_id: UUID
    start_ms: int = Field(ge=0, le=120000, strict=True)
    end_ms: int = Field(gt=0, le=120000, strict=True)
    observation: TraitObservation | None = None

    @model_validator(mode='after')
    def ordered(self):
        if self.end_ms <= self.start_ms:
            raise ValueError('Comprueba el intervalo de evidencia.')
        return self


class Trait(StrictModel):
    id: UUID
    category: Literal['gesture', 'body', 'expression', 'rhythm', 'pause', 'reaction', 'laugh', 'catchphrase', 'emotion']
    text: str = Field(min_length=1, max_length=2000)
    kind: Literal['observation', 'interpretation', 'creative'] = 'creative'
    evidence: Evidence | None = None
    reviewed: bool = Field(default=False, strict=True)

    @model_validator(mode='after')
    def grounded(self):
        if self.kind != 'creative' and self.evidence is None:
            raise ValueError('Las observaciones e interpretaciones requieren un clip e intervalo de evidencia.')
        return self


from .script import Voice


class Character(StrictModel):
    parody_role: Literal["replacement", "supporting"] = "replacement"
    voice_profile: Voice | None = None
    id: UUID
    name: str = Field(min_length=1, max_length=120)
    appearance: str = Field(default='', max_length=4000)
    wardrobe: str = Field(default='', max_length=2000)
    accessories: str = Field(default='', max_length=2000)
    style: Literal['realistic', 'stylized_2d', 'stylized_3d', 'other'] = 'realistic'
    references: list[Reference] = Field(default_factory=list, max_length=24)
    traits: list[Trait] = Field(default_factory=list, max_length=64)


class ObservationLink(StrictModel):
    analysis_id: UUID
    window_index: int = Field(ge=0, le=95, strict=True)
    actor_index: int = Field(ge=0, le=31, strict=True)


class Appearance(StrictModel):
    id: UUID
    character_id: UUID
    shot_id: UUID
    target_label: str = Field(min_length=1, max_length=500)
    source_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    observation: ObservationLink | None = None
    reviewed: bool = Field(default=False, strict=True)


class Cast(StrictModel):
    version: Literal[1] = 1
    characters: list[Character] = Field(default_factory=list, max_length=32)
    appearances: list[Appearance] = Field(default_factory=list, max_length=240)

    @model_validator(mode='after')
    def unique_ids(self):
        collections = [self.characters, self.appearances]
        for character in self.characters:
            collections.extend([character.references, character.traits])
        for values in collections:
            if len({v.id for v in values}) != len(values):
                raise ValueError('Los identificadores de las fichas deben ser únicos.')
        identities = {c.id for c in self.characters}
        pairs = set()
        for appearance in self.appearances:
            pair = (appearance.shot_id, appearance.character_id)
            if appearance.character_id not in identities or pair in pairs:
                raise ValueError('Cada personaje solo puede tener una asignación por plano y debe existir en la ficha.')
            pairs.add(pair)
        targets = [(a.shot_id, a.target_label.casefold()) for a in self.appearances]
        if len(set(targets)) != len(targets):
            raise ValueError('Un personaje original no puede recibir dos reemplazos en el mismo plano.')
        return self


def validate_cast(service, project_id, cast, source, shots):
    """Resolve only workspace-owned inputs; pin content before accepting a revision."""
    value = cast.model_dump(mode='json')
    root = service.root.resolve()
    inputs = (root / 'inputs').resolve()
    if not inputs.is_relative_to(root):
        raise Problem(404, 'REFERENCE_NOT_FOUND', 'Referencias no disponibles en este espacio.')
    cache = {}
    trait_analysis_cache = {}
    for character in value['characters']:
        references = {}
        for ref in character['references']:
            ident = ref['asset_id']
            if ident not in cache:
                asset = service.store.asset(ident)
                if not asset:
                    raise Problem(404, 'REFERENCE_NOT_FOUND', 'Referencia no encontrada en este espacio.')
                path = (inputs / asset['filename']).resolve()
                if not path.is_relative_to(inputs) or not path.is_file():
                    raise Problem(404, 'REFERENCE_NOT_FOUND', 'Archivo de referencia no disponible.')
                digest = hashlib.sha256()
                with path.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(chunk)
                cache[ident] = (asset, digest.hexdigest())
            asset, digest = cache[ident]
            expected = ('video',) if ref['role'] == 'acting' else ('audio', 'video') if ref['role'] == 'voice' else ('image',)
            if asset.get('kind') not in expected:
                raise Problem(422, 'REFERENCE_KIND', 'El formato no corresponde al uso de la referencia.')
            if ref['sha256'] is not None and ref['sha256'] != digest:
                raise Problem(409, 'REFERENCE_CHANGED', 'Una referencia cambió. Retírala y vuelve a añadirla después de revisarla.')
            ref.update(sha256=digest, name=asset.get('name', 'Referencia'))
            references[ref['id']] = (ref, asset)
        profile = character.get('voice_profile')
        if profile and profile['mode'] == 'clone':
            pair = references.get(profile['reference_id'])
            if not pair or pair[0]['role'] != 'voice' or not profile['authorized']:
                raise Problem(422,'VOICE_PROFILE','La voz del personaje requiere una referencia propia y permiso registrado.')
        for trait in character['traits']:
            evidence = trait['evidence']
            if not evidence:
                continue
            pair = references.get(evidence['reference_id'])
            if not pair or pair[0]['role'] not in ('acting', 'voice'):
                raise Problem(422, 'TRAIT_EVIDENCE', 'La evidencia debe apuntar a un clip de actuación o voz de esta ficha.')
            duration = pair[1].get('duration')
            if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0 or evidence['end_ms'] > round(duration * 1000):
                raise Problem(422, 'TRAIT_INTERVAL', 'La evidencia supera la duración conocida del clip.')
            link = evidence.get('observation')
            if link:
                key = (link['project_id'], link['analysis_id'])
                if key not in trait_analysis_cache:
                    trait_analysis_cache[key] = AnalysisStore(service.root).read(*key)
                record = trait_analysis_cache[key]
                window = next((w for w in record.get('windows', []) if w['index'] == link['window_index']), None)
                if pair[0]['role'] != 'acting' or record['source']['sha256'] != pair[0]['sha256'] or not window or link['actor_index'] >= len(window['observation']['actors']) or evidence['start_ms'] != window['start_ms'] or evidence['end_ms'] != window['end_ms']:
                    raise Problem(409, 'TRAIT_OBSERVATION_STALE', 'La observación de Qwen no corresponde al clip y al intervalo de actuación.')
    shot_map = {str(s.id): s for s in shots}
    analysis_cache = {}
    for appearance in value['appearances']:
        shot = shot_map.get(appearance['shot_id'])
        if not source or appearance['source_sha256'] != source['sha256'] or not shot:
            raise Problem(409, 'APPEARANCE_STALE', 'Una asignación pertenece a otra fuente o plano. Revísala antes de guardar.')
        link = appearance['observation']
        if link:
            ident = link['analysis_id']
            if ident not in analysis_cache:
                analysis_cache[ident] = AnalysisStore(service.root).read(project_id, ident)
            record = analysis_cache[ident]
            windows = {w['index']: w for w in record.get('windows', [])}
            window = windows.get(link['window_index'])
            if record['source']['sha256'] != source['sha256'] or not window or link['actor_index'] >= len(window['observation']['actors']) or shot.start_ms > window['start_ms'] or shot.end_ms < window['end_ms']:
                raise Problem(409, 'OBSERVATION_STALE', 'La observación no corresponde a la fuente y plano seleccionados.')
    return value
