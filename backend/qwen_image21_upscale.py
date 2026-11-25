"""Elusarca Detail Enhancer, using SimpliGen's image-initialized upscale recipe."""
import math
from pathlib import Path
from .head import digest

VERSION = 'qwen-image-21-detail-upscale-v1'
LORA = 'elusarcas-qwen2-1-detailer-v1.safetensors'
LORA_SHA256 = 'c1298f51eb090473f924314475952710a2f0322f1df65c0290f3e14997244f96'
LORA_PATH = Path('D:/AI/apps/SimpliGen/profile/engine/models/loras') / LORA
MODEL_URL = 'https://huggingface.co/reverentelusarca/elusarcas-qwen-2.1-detail-enhancer-lora'
BRIEF = ('enhance this image. Enhance this image to high resolution with rich fine details. '
         'Sharpen textures and surfaces, add natural microdetails, and improve clarity and definition '
         'across the image while preserving the original composition, lighting, atmosphere, and visual style.')
RECIPE = {'version': VERSION, 'lora': LORA, 'lora_sha256': LORA_SHA256,
          'repository': MODEL_URL, 'revision': 'aedadc6b65a15e6b261c0ec61a4587c3e7119514',
          'template_sha256': '13941c9d9a09adca4bb9cef685f79dfd633c8aac59e4755c4cf8b7c07d3b22e7',
          'sampler': 'euler', 'scheduler': 'simple', 'reference_resolution': 1024,
          'latent': 'Lanczos scale to 2 or 4 MP, multiples of 32, then VAEEncode',
          'cache': ['auto', 'default'], 'brief': BRIEF}

def verify_lora():
    if not LORA_PATH.is_file() or digest(LORA_PATH) != LORA_SHA256:
        raise ValueError('Falta el LoRA Detail Enhancer verificado en D. Revisa su instalación.')

def dimensions(w, h, quality):
    scale = math.sqrt((4 if quality == 'high' else 2) * 1024 * 1024 / (w * h))
    size = tuple(round(v * scale / 32) * 32 for v in (w, h))
    if any(v < 64 or v > 4096 for v in size):
        raise ValueError('Esta proporción no es compatible con el upscale. Usa una imagen menos alargada.')
    if size[0] < w or size[1] < h or size == (w, h):
        raise ValueError('La imagen ya alcanza ese tamaño. Elige 4 MP o una imagen más pequeña; no se reducirá tu original.')
    return size

def graph(p, ident, names, model, clip, vae):
    def n(kind, **inputs): return {'class_type': kind, 'inputs': inputs}
    if len(names) != 1: raise ValueError('Upscale requiere una sola imagen de origen.')
    return {
        'model': n('UNETLoader', unet_name=model, weight_dtype='default'),
        'clip': n('CLIPLoader', clip_name=clip, type='qwen_image', device='default'),
        'vae': n('VAELoader', vae_name=vae),
        'detail_lora': n('LoraLoaderModelOnly', model=['model', 0], lora_name=LORA, strength_model=p['upscale_strength']),
        'cache': n('QwenImage21Cache', model=['detail_lora', 0], device='auto', dtype='default'),
        'reference_1': n('LoadImage', image=names[0]),
        'enlarge': n('ImageScaleToTotalPixels', image=['reference_1', 0], upscale_method='lanczos',
                     megapixels=4.0 if p['quality'] == 'high' else 2.0, resolution_steps=32),
        'brief': n('StringConcatenate', string_a=BRIEF, string_b=p['prompt'], delimiter=' '),
        'encode': n('TextEncodeQwenImage21', clip=['clip', 0], vae=['vae', 0], prompt=['brief', 0],
                    negative_prompt=p['negative_prompt'], resolution=1024, **{'images.image_1': ['enlarge', 0]}),
        'latent': n('VAEEncode', pixels=['enlarge', 0], vae=['vae', 0]),
        'sample': n('KSampler', model=['cache', 0], positive=['encode', 0], negative=['encode', 1],
                    latent_image=['latent', 0], seed=p['seed'], steps=p['steps'], cfg=p['cfg'],
                    sampler_name='euler', scheduler='simple', denoise=p['upscale_denoise']),
        'decode': n('VAEDecode', samples=['sample', 0], vae=['vae', 0]),
        'save': n('SaveImage', images=['decode', 0], filename_prefix=f'gen_studio/{ident}/qwen_image21/upscale'),
    }
