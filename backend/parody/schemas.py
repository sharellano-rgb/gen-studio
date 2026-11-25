from uuid import UUID
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .characters import Cast
from .script import Script


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class CreateProject(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    source_asset_id: UUID | None = None
    guided: bool = False


GuideStep = Literal['source', 'cast', 'images', 'voices', 'acting', 'scene', 'characters', 'script', 'render', 'montage', 'done']


class GuideNavigation(StrictModel):
    expected_revision: int = Field(ge=1)
    previous: GuideStep
    step: GuideStep


class ProjectGuide(StrictModel):
    step: GuideStep = 'source'
    target: str = Field(default='', max_length=500)
    original_images: list[UUID] = Field(default_factory=list, max_length=24)


class UpdateProject(StrictModel):
    expected_revision: int = Field(ge=1, strict=True)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    archived: bool | None = Field(default=None, strict=True)

    @model_validator(mode='after')
    def nonempty(self):
        if self.name is None and self.archived is None:
            raise ValueError('Indica un nombre o el estado del archivo.')
        return self


class Shot(StrictModel):
    id: UUID
    label: str = Field(min_length=1, max_length=120)
    start_ms: int = Field(ge=0, strict=True)
    end_ms: int = Field(gt=0, le=120000, strict=True)

    @model_validator(mode='after')
    def ordered(self):
        if self.end_ms <= self.start_ms:
            raise ValueError('El final del plano debe ser posterior al inicio.')
        return self


class UpdateDraft(StrictModel):
    expected_revision: int = Field(ge=1, strict=True)
    source_asset_id: UUID | None
    notes: str = Field(default='', max_length=8000)
    shots: list[Shot] = Field(default_factory=list, max_length=240)
    cast: Cast | None = None
    script: Script | None = None
    guide: ProjectGuide | None = None

    @model_validator(mode='after')
    def consistent(self):
        if len({s.id for s in self.shots}) != len(self.shots):
            raise ValueError('Cada plano requiere un identificador distinto.')
        if self.shots and not self.source_asset_id:
            raise ValueError('Selecciona la fuente de los planos.')
        return self
