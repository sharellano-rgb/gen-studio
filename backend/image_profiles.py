"""Pinned, optional BASE + 8-step distillation recipe for MOSS edits."""
import json
import os
import threading
from pathlib import Path

from .head import digest

PROFILE = 'mystic_base8'
VERSION = 'moss-image-edit-mystic-base8-v1'
BASE = 'moss_image_base_int8_convrot.safetensors'
DISTILL = 'moss_image_base_8_steps_dmd.safetensors'
MYSTIC = 'MysticXXX-Moss-V1.safetensors'
# Embedded modelspec.trigger_word in the verified Mystic safetensors metadata.
MYSTIC_TRIGGER = 'i measured it'
REFERENCE_MAX_EDGE = 1024
def models_root(config_path=None):
    """Use the configured model store without relying on Windows profile junctions."""
    config_path = Path(config_path) if config_path is not None else Path(__file__).with_name('config.json')
    settings = json.loads(config_path.read_text(encoding='utf-8')) if config_path.is_file() else {}
    configured = settings.get('image_models_root')
    if configured is not None:
        if not isinstance(configured, str) or not configured.strip() or not Path(configured).is_absolute():
            raise ValueError('image_models_root debe ser una ruta absoluta a la carpeta de modelos.')
        return Path(configured)
    return Path(os.environ.get('APPDATA', Path.home()/'AppData'/'Roaming'))/'simpligen'/'engine'/'models'


MODELS_ROOT = models_root()
WEIGHTS = {
    'base': {'folder': 'diffusion_models', 'name': BASE, 'bytes': 5413698958,
        'sha256': 'f601c1e1161915fb3fea003da0c1782e374dd960fbb3978bc0e4e3fa1f2d8e6f'},
    'distill': {'folder': 'loras', 'name': DISTILL, 'bytes': 1189176800,
        'sha256': 'f0e6cd07f222dfaea798afe3b796e2c2995cadfd44ad56e02ca616f0467aec28'},
    'mystic': {'folder': 'loras', 'name': MYSTIC, 'bytes': 145157240,
        'sha256': 'e53282721e0217804a16be0f3d761138509d0a73614bf907745a8fd23cd35e00'},
}
REQUEST_FIELDS = ('edit_profile', 'mystic_strength', 'distill_strength', 'edit_sampler', 'edit_scheduler')
_verified = {}
_verify_lock = threading.Lock()


def selected(parameters):
    return parameters.get('edit_profile', 'standard') == PROFILE


def validate_selection(mode, profile, lora_enabled):
    if profile == PROFILE and lora_enabled:
        raise ValueError('La receta BASE con Mystic ya incluye su LoRA. Desactiva el LoRA de estilo anterior.')


def pins():
    return {name: dict(value) for name, value in WEIGHTS.items()}


def _identity(path):
    stat = path.stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def verify_weights():
    """Hash every newly observed file; reuse only an unchanged filesystem identity."""
    with _verify_lock:
        for name, value in WEIGHTS.items():
            path = (MODELS_ROOT/value['folder']/value['name']).resolve()
            key = str(path)
            try:
                before = _identity(path)
                expected = (value['bytes'], value['sha256'])
                if not path.is_file() or before[2] != value['bytes']:
                    raise ValueError('weight size')
                if _verified.get(key) == (before, expected):
                    continue
                if digest(path) != value['sha256'] or _identity(path) != before:
                    raise ValueError('weight SHA-256')
                _verified[key] = (before, expected)
            except (OSError, ValueError) as error:
                _verified.pop(key, None)
                raise ValueError('No se pudo verificar el SHA-256 del modelo '+name+' de la receta BASE con Mystic. Revisa su instalación.') from error


def bind(parameters):
    if not selected(parameters):
        from .image_realism import bind as bind_realism
        return bind_realism(parameters)
    verify_weights()
    from .image_realism import bind as bind_realism
    return bind_realism({**parameters, 'edit_profile_version': VERSION, 'edit_profile_weights': pins(),
        'mystic_trigger': MYSTIC_TRIGGER, 'edit_reference_max_edge': REFERENCE_MAX_EDGE})


def verify_binding(parameters, verify_files=True):
    if not selected(parameters):
        from .image_realism import verify_binding as verify_realism
        verify_realism(parameters,verify_files)
        return
    if (parameters.get('edit_profile_version') != VERSION or
            parameters.get('edit_profile_weights') != pins() or
            parameters.get('mystic_trigger') != MYSTIC_TRIGGER or
            parameters.get('edit_reference_max_edge') != REFERENCE_MAX_EDGE):
        raise ValueError('La receta BASE con Mystic no coincide con los modelos verificados de este trabajo.')
    if verify_files:
        verify_weights()
    from .image_realism import verify_binding as verify_realism
    verify_realism(parameters,verify_files)


def binding(parameters):
    if not selected(parameters):
        from .image_realism import binding as realism_binding
        return realism_binding(parameters)
    from .image_realism import binding as realism_binding
    return {**{key: parameters[key] for key in (*REQUEST_FIELDS, 'edit_profile_version',
        'edit_profile_weights', 'mystic_trigger', 'edit_reference_max_edge')},**realism_binding(parameters)}
