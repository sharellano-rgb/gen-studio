"""Editable dialogue metadata. No provider calls while saving a script."""
import hashlib
import json
import re
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .repository import Problem, encode


class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)


class Voice(Strict):
    mode: Literal['design','clone']='design'
    description: str = Field(default='Voz adulta, clara y conversacional, tono natural. Personaje ficticio.',max_length=700)
    reference_id: UUID | None = None
    reference_start_ms: int = Field(default=0,ge=0,le=120000,strict=True)
    reference_end_ms: int = Field(default=5000,gt=0,le=120000,strict=True)
    transcript: str = Field(default='',max_length=1600)
    authorized: bool = Field(default=False,strict=True)
    permission_note: str = Field(default='',max_length=1000)

    @model_validator(mode='after')
    def consistent(self):
        if self.mode=='design' and len(self.description)<5:
            raise ValueError('Describe la voz diseñada con al menos cinco caracteres.')
        if self.mode=='clone':
            if not self.reference_id or not 3000<=self.reference_end_ms-self.reference_start_ms<=25000 or len(self.transcript)<5:
                raise ValueError('La clonación requiere un clip de 3–25 segundos y su transcripción.')
            # An unapproved draft is allowed; execution separately requires permission.
            if self.authorized and len(self.permission_note)<10:
                raise ValueError('Registra la procedencia y alcance del permiso de voz.')
        return self


class Line(Strict):
    id: UUID
    shot_id: UUID
    character_id: UUID | None = None
    kind: Literal['speech','original','silence']='speech'
    start_ms: int = Field(ge=0,le=120000,strict=True)
    end_ms: int = Field(gt=0,le=120000,strict=True)
    text: str = Field(default='',max_length=650)
    acting: str = Field(default='',max_length=2000)
    language: Literal['Spanish','English']='Spanish'
    seed: int = Field(default=127,ge=0,lt=2**32,strict=True)
    pause_before_ms: int = Field(default=0,ge=0,le=5000,strict=True)
    pause_after_ms: int = Field(default=0,ge=0,le=5000,strict=True)
    voice: Voice = Field(default_factory=Voice)

    @model_validator(mode='after')
    def consistent(self):
        if self.end_ms<=self.start_ms:
            raise ValueError('El final de la frase debe ser posterior al inicio.')
        if self.kind=='speech' and (not self.character_id or not self.text):
            raise ValueError('Asigna un personaje y escribe el diálogo.')
        if self.kind!='speech' and (self.pause_before_ms or self.pause_after_ms):
            raise ValueError('Las pausas añadidas solo corresponden a voz sintetizada.')
        if self.pause_before_ms+self.pause_after_ms>=self.end_ms-self.start_ms:
            raise ValueError('Las pausas ocupan todo el intervalo de la frase.')
        return self


class RequiredPhrase(Strict):
    character_id: UUID
    text: str = Field(min_length=1,max_length=160)
    reference_id: UUID | None = None
    start_ms: int = Field(default=0,ge=0,strict=True)
    end_ms: int = Field(default=0,ge=0,strict=True)
    use_clip_audio: bool = Field(default=False,strict=True,exclude_if=lambda v:not v)

    @model_validator(mode='after')
    def valid(self):
        if not phrase_words(self.text):
            raise ValueError('Escribe las palabras que debe pronunciar el personaje.')
        if self.reference_id and not 250<=self.end_ms-self.start_ms<=10000:
            raise ValueError('Selecciona de 0,25 a 10 segundos del vídeo donde se pronuncia la frase.')
        if not self.reference_id and (self.start_ms or self.end_ms or self.use_clip_audio):
            raise ValueError('Selecciona primero el vídeo de referencia.')
        return self


def phrase_words(text):
    return re.findall(r'\w+',text.casefold())


def phrase_line(rule,line):
    if line.get('kind','speech')!='speech' or line.get('character_id')!=rule['character_id']:
        return False
    words,required=phrase_words(line['text']),phrase_words(rule['text'])
    return any(words[i:i+len(required)]==required for i in range(len(words)))


def split_phrase(text,phrase):
    tokens=list(re.finditer(r'\w+',text))
    words=[t.group().casefold() for t in tokens]
    required=phrase_words(phrase)
    for i in range(len(words)):
        if words[i:i+len(required)]==required:
            return text[:tokens[i].start()].strip(),text[tokens[i+len(required)-1].end():].strip()
    raise ValueError('El diálogo ya no contiene la frase obligatoria.')


class Script(Strict):
    version: Literal[1]=1
    required_phrase: RequiredPhrase | None = Field(default=None,exclude_if=lambda v:v is None)
    lines: list[Line]=Field(default_factory=list,max_length=240)

    @model_validator(mode='after')
    def unique(self):
        if len({line.id for line in self.lines})!=len(self.lines):
            raise ValueError('Las frases requieren identificadores distintos.')
        return self


def validate_script(script, source, shots, cast):
    characters={c['id']:c for c in (cast or {}).get('characters',[])}
    if rule:=script.required_phrase:
        character=characters.get(str(rule.character_id))
        if not character:
            raise Problem(422,'PHRASE_CHARACTER','Selecciona un personaje del reparto para la frase obligatoria.')
        if rule.reference_id and not any(r['id']==str(rule.reference_id) and r['role']=='acting' for r in character['references']):
            raise Problem(422,'PHRASE_REFERENCE','Selecciona un clip de actuación de ese mismo personaje.')
    shot_map={str(s.id):s for s in shots}
    for line in script.lines:
        shot=shot_map.get(str(line.shot_id))
        if not source or not shot or line.start_ms<shot.start_ms or line.end_ms>shot.end_ms:
            raise Problem(409,'SCRIPT_INTERVAL','Revisa las frases: una pertenece a otra fuente o supera su plano.')
        character=characters.get(str(line.character_id))
        if line.character_id and not character:
            raise Problem(409,'SCRIPT_CHARACTER','Una frase usa un personaje que ya no está en el reparto.')
        if line.kind=='speech' and line.voice.mode=='clone':
            reference=next((r for r in character['references'] if r['id']==str(line.voice.reference_id) and r['role']=='voice'),None)
            if not reference:
                raise Problem(422,'VOICE_REFERENCE','Selecciona una referencia de voz de la ficha del personaje.')
    return script.model_dump(mode='json')


def line_binding(project,line_id):
    line=next((v for v in project.get('script',{}).get('lines',[]) if v['id']==str(line_id)),None)
    if not line: raise Problem(404,'LINE_NOT_FOUND','Frase no encontrada en el guion guardado.')
    character=next((c for c in project.get('cast',{}).get('characters',[]) if c['id']==line.get('character_id')),None)
    reference=None
    if line['kind']=='speech' and line['voice']['mode']=='clone':
        reference=next((r for r in (character or {}).get('references',[]) if r['id']==line['voice']['reference_id']),None)
        if not reference or reference['role']!='voice':raise Problem(409,'VOICE_REFERENCE','Cambió la referencia de voz.')
    value=json.loads(encode(dict(line=line,source=project['source'],reference=reference,version='parody-voice-1')))
    rule=project.get('script',{}).get('required_phrase')
    if rule and rule.get('use_clip_audio') and phrase_line(rule,line):
        ref=next((r for r in character['references'] if r['id']==rule['reference_id'] and r['role']=='acting'),None)
        if not ref:raise Problem(409,'PHRASE_REFERENCE','Cambió el clip de la frase.')
        value['phrase_audio']={**rule,'asset_id':ref['asset_id'],'sha256':ref['sha256']}
    return value,hashlib.sha256(encode(value).encode()).hexdigest()
