"""Explicit Character Swap selection; historical jobs retain v1."""
import hashlib
from pathlib import Path
V1_FILENAME='h3_character_swap_pro4500_1000.safetensors'
V15_FILENAME='h3_character_swap_v1.5.safetensors'
V15_SHA256='8f2c3c3eba161df8d0c6099598b4730f641f5ce01e04cd16d2427cff8a35218f'
V15_SIZE=155110792
V15_REVISION='0f409f0c2a18f123f3985041ec52982c813f5a9c'
MODEL_ROOT=Path(__file__).resolve().parents[1]/'models/loras'

def filename(version='v1'):
    if version not in ('v1','v1.5'):
        raise ValueError('Version de Character Swap desconocida.')
    return V15_FILENAME if version=='v1.5' else V1_FILENAME

def hands_strength(version='v1'):
    filename(version)
    return 1.0 if version=='v1.5' else 1.35

def verify(version='v1',model_root=None):
    name=filename(version)
    if version=='v1':return None
    path=Path(model_root or MODEL_ROOT)/name
    if not path.is_file():raise ValueError('Falta Character Swap v1.5: '+name)
    if path.stat().st_size!=V15_SIZE:raise ValueError('El tamano de Character Swap v1.5 no coincide con la version verificada.')
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(8*1024*1024),b''):digest.update(chunk)
    if digest.hexdigest()!=V15_SHA256:raise ValueError('El SHA-256 de Character Swap v1.5 no coincide.')
    return path
