"""Owner-only, durable switch for each guest’s rolling three-hour video admission."""
import json
import os
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .generation_limits import clear_waiting

INTERVAL_SECONDS = 10800


class LimitRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = Field(strict=True)


class SonnyGenerationControl:
    def __init__(self, registry, workspace_id='sonny'):
        self.workspace_id = workspace_id
        self.registry = registry
        self.path = Path(registry.services['owner'].root) / 'admin' / (workspace_id + '-generation-limit.json')
        self.service = registry.services.get(workspace_id)
        if self.service is None:
            return
        enabled = self.service.settings.get('generation_interval_seconds', 0) > 0
        if self.path.exists():
            try:
                value = json.loads(self.path.read_text(encoding='utf-8'))
                if (not isinstance(value, dict) or set(value) != {'version', 'enabled'}
                        or type(value['version']) is not int or value['version'] != 1
                        or type(value['enabled']) is not bool):
                    raise ValueError('Formato inválido.')
                enabled = value['enabled']
            except (OSError, ValueError, TypeError) as exc:
                # Do not silently remove a restriction if its saved state is damaged.
                raise ValueError('No se pudo leer el control de generación del espacio.') from exc
        self.service.settings['generation_limit_managed'] = True
        self._apply(enabled)

    def _apply(self, enabled):
        self.service.settings['generation_interval_seconds'] = INTERVAL_SECONDS if enabled else 0
        if not enabled:
            clear_waiting(self.service)
        self.service.wake.set()
        self.registry.coordinator.changed.set()

    def state(self):
        if self.service is None:
            raise HTTPException(404, 'El espacio no está configurado.')
        enabled = self.service.settings['generation_interval_seconds'] > 0
        return {'enabled': enabled, 'interval_seconds': INTERVAL_SECONDS,
                'effective_interval_seconds': INTERVAL_SECONDS if enabled else 0}

    def set_enabled(self, enabled):
        self.state()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + '.' + uuid4().hex + '.tmp')
        try:
            with temporary.open('x', encoding='utf-8', newline='\n') as stream:
                json.dump({'version': 1, 'enabled': enabled}, stream)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        # Commit the file before changing the live policy. A failed save leaves it intact.
        self.service.settings['generation_limit_managed'] = True
        self._apply(enabled)
        return self.state()


def register(app):
    from .workspaces import current_workspace

    def control(workspace_id='sonny'):
        if current_workspace.get() != 'owner':
            raise HTTPException(403, 'Sólo el propietario puede cambiar este control.')
        manager = app.state.jobs.generation_controls.get(workspace_id)
        if manager is None:
            raise HTTPException(404, 'El espacio no está configurado.')
        return manager

    @app.get('/api/admin/sonny-generation-limit')
    async def get_limit():
        return control().state()

    @app.get('/api/admin/workspaces/{workspace_id}/generation-limit')
    async def get_workspace_limit(workspace_id: str):
        return control(workspace_id).state()

    @app.put('/api/admin/workspaces/{workspace_id}/generation-limit')
    async def set_workspace_limit(workspace_id: str, body: LimitRequest):
        manager = control(workspace_id)
        try:
            return manager.set_enabled(body.enabled)
        except OSError as exc:
            raise HTTPException(503, 'No se pudo guardar el control. Vuelve a intentarlo.') from exc

    @app.put('/api/admin/sonny-generation-limit')
    async def set_limit(body: LimitRequest):
        manager = control()
        try:
            return manager.set_enabled(body.enabled)
        except OSError as exc:
            raise HTTPException(503, 'No se pudo guardar el control. Vuelve a intentarlo.') from exc
