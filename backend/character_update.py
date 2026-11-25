"""Staged 20261015 adapter. Not enabled in production pending dependency approval."""
import hashlib
import math
from pathlib import Path
from . import character_lora

VENDOR = Path(__file__).resolve().parents[1] / 'workflows/vendor/char-swap-20261015/Char_Swap-20261015.json'
SHA256 = 'f5c24c580c3d26f060c0b02baedbdd785adbb9a97f8533fdec6a38a548e4d8cb'
BASE = 'Minimax-h3_Singularity_ref2va_v1.3_int8.safetensors'
TURBO = 'minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors'
CHARACTER = character_lora.V1_FILENAME
ENCODER = 'qwen3vl_32b_minimax_h3_int8_convrot.comfy.safetensors'
REALISM = 'h3-realism-people-t2v-i2v-r2v(r34l1sm).safetensors'
PREDICTOR = 'minimax_h3_t2va_veda_8nfe_600step_preview_fp8.safetensors'
OFFICIAL_REALISM = 'h3-realism-people-t2v-i2v-r2v.safetensors'
OFFICIAL_REALISM_SHA256 = 'acc529601d2da117fb81179e76c56e488a3beab1171659d305f04fa3655b787e'
NATIVE_ENCODER = 'qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors'


def validate_duration(duration, frames, start_seconds=0):
    """Validate the requested 24 fps cut before preparing or uploading media."""
    if isinstance(frames, bool) or not isinstance(frames, int) or frames < 5 or (frames - 5) % 17:
        raise ValueError('La duración H3 debe tener 17n + 5 fotogramas.')
    if not math.isfinite(duration) or duration <= 0 or not math.isfinite(start_seconds) or start_seconds < 0:
        raise ValueError('Duración o inicio de vídeo inválidos.')
    if start_seconds + frames / 24 > duration + 1e-6:
        raise ValueError('El segmento solicitado excede la duración del vídeo.')
    return {'frames': frames, 'fps': 24, 'start_seconds': start_seconds, 'seconds': frames / 24}


def resolve_model(catalog, node_type, field, filename):
    """Resolve a declared filename, accepting only a unique subfolder match."""
    choices = catalog.get(node_type, {}).get('input', {}).get('required', {}).get(field, [[]])[0]
    if not isinstance(choices, list):
        raise ValueError(f'Catálogo de modelos inválido: {node_type}.{field}')
    if filename in choices:
        return filename
    matches = [name for name in choices if name.replace('\\', '/').rsplit('/', 1)[-1] == filename]
    if len(matches) != 1:
        raise ValueError(f'Modelo ausente o ambiguo: {filename}')
    return matches[0]


def native_candidate(catalog, video_name, reference_name, *, duration, frames,
                     prompt, width, height, start_seconds=0, seed=43, character_version='v1'):
    """Explicit isolated adaptation; does not change the author or app defaults.

    The caller must normalize/cut the input to the validated segment at 24 fps.
    This helper does not submit or upload anything.
    """
    validate_duration(duration, frames, start_seconds)
    model = VENDOR.parents[3] / 'models/loras' / OFFICIAL_REALISM
    if hashlib.sha256(model.read_bytes()).hexdigest() != OFFICIAL_REALISM_SHA256:
        raise ValueError('El SHA-256 del LoRA oficial de realismo no coincide.')
    character_lora.verify(character_version, VENDOR.parents[3] / 'models/loras')
    g = graph(video_name, reference_name, prompt=prompt, width=width, height=height,
              frames=frames, seed=seed, encoder=NATIVE_ENCODER, realism=OFFICIAL_REALISM,
              attention_mode='native', resize_method='lanczos', character_version=character_version)
    for node in g.values():
        fields = {'UNETLoader': 'unet_name', 'CLIPLoader': 'clip_name',
                  'VAELoader': 'vae_name', 'LoraLoaderModelOnly': 'lora_name'}
        field = fields.get(node['class_type'])
        if field:
            node['inputs'][field] = resolve_model(catalog, node['class_type'], field, node['inputs'][field])
    return g


def graph(video_name, reference_name, *, prompt, width, height, frames, seed=43,
          encoder=ENCODER, realism=REALISM, predictor=PREDICTOR,
          attention_mode='author', resize_method='nvidia_rtx_vsr', character_version='v1'):
    """API adaptation: uploaded inputs and app output replace absolute VHS paths.

    Callers must declare any alternative encoder/weights explicitly. No silent
    bypass of missing Veda, realism or attention nodes is allowed.
    """
    if hashlib.sha256(VENDOR.read_bytes()).hexdigest() != SHA256:
        raise ValueError('La copia del workflow actualizado cambió.')
    if isinstance(frames, bool) or not isinstance(frames, int) or frames < 5 or (frames - 5) % 17:
        raise ValueError('La duración H3 debe tener 17n + 5 fotogramas.')
    if width <= 0 or height <= 0 or width % 32 or height % 32:
        raise ValueError('La resolución debe ser positiva y múltiplo de 32.')
    if attention_mode not in ('author', 'native'):
        raise ValueError('Modo de atención desconocido.')
    references = [reference_name] if isinstance(reference_name, str) else list(reference_name)
    if not 1 <= len(references) <= 9 or not all(isinstance(r, str) and r for r in references):
        raise ValueError('Selecciona entre 1 y 9 referencias.')
    def n(kind, **inputs):
        return {'class_type': kind, 'inputs': inputs}
    g = {'1': n('UNETLoader', unet_name=BASE, weight_dtype='default')}
    previous = '1'
    for key, name in [('2', TURBO), ('3', character_lora.filename(character_version)), ('4', realism)]:
        g[key] = n('LoraLoaderModelOnly', model=[previous, 0], lora_name=name, strength_model=1)
        previous = key
    patches = [
        ('5', 'MiniMaxH3MemoryEfficientSageAttentionPatch', {}),
        ('6', 'PathchSageAttentionKJ', {'sage_attention': 'auto', 'allow_compile': False}),
        ('7', 'MiniMaxChunkFeedForward', {'chunks': 2, 'seq_threshold': 4096}),
        ('8', 'ModelPatchTorchSettings', {'enable_fp16_accumulation': True}),
        ('9', 'VedaSparseAttention', {'predictor': predictor, 'generated_sparsity': '32',
            'reference_sparsity': '32', 'full_attention_layers': '', 'full_attention_steps': '', 'verbose': False}),
        ('10', 'MiniMaxH3SigmaShift', {'shift_video': 12, 'shift_audio': 3}),
    ]
    for key, kind, inputs in patches:
        if attention_mode == 'native' and key in ('5', '6', '9'):
            continue
        g[key] = n(kind, model=[previous, 0], **inputs)
        previous = key
    g.update({
        '11': n('CLIPLoader', clip_name=encoder, type='minimax', device='default'),
        '12': n('VAELoader', vae_name='minimax_h3_video_vae_fp16.safetensors'),
        '13': n('VAELoader', vae_name='minimax_h3_audio_vae_fp32.safetensors'),
        '14': n('LoadVideo', file=video_name),
        '15': n('GetVideoComponents', video=['14', 0]),
        '16': n('LoadImage', image=references[0]),
    })
    for key, image, proportion in [('17', ['15', 0], 'pad'), ('18', ['16', 0], 'crop')]:
        g[key] = n('ImageResizeKJv2', image=image, width=width, height=height,
            upscale_method=resize_method, keep_proportion=proportion,
            pad_color='0, 0, 0', crop_position='center', divisible_by=2, device='cpu')
    g.update({
        '19': n('MiniMaxH3ReferenceToVideo', clip=['11', 0], vae=['12', 0], audio_vae=['13', 0],
            prompt='r34l1sm , ultra realistic video. ' + prompt, width=width, height=height,
            length=frames, ref_image_size='match',
            **{'ref_images.ref_image_0': ['18', 0], 'ref_videos.ref_video_0': ['17', 0]}),
        '20': n('BasicGuider', model=['10', 0], conditioning=['19', 0]),
        '21': n('BasicScheduler', model=['10', 0], scheduler='beta', steps=8, denoise=1),
        '22': n('KSamplerSelect', sampler_name='euler'),
        '23': n('RandomNoise', noise_seed=seed),
        '24': n('SamplerCustomAdvanced', noise=['23', 0], guider=['20', 0], sampler=['22', 0],
            sigmas=['21', 0], latent_image=['19', 1]),
        '25': n('VAEDecode', samples=['24', 1], vae=['12', 0]),
        '26': n('VAEDecodeAudio', samples=['24', 1], vae=['13', 0]),
        '27': n('CreateVideo', images=['25', 0], audio=['26', 0], fps=24),
        '28': n('SaveVideo', video=['27', 0], filename_prefix='gen_studio/char-swap-20261015/trial',
            format='mp4', codec='h264'),
    })
    for i, name in enumerate(references[1:], 1):
        image_key, resize_key = f'reference_{i}', f'reference_resize_{i}'
        g[image_key] = n('LoadImage', image=name)
        g[resize_key] = n('ImageResizeKJv2', **{**g['18']['inputs'], 'image': [image_key, 0]})
        g['19']['inputs'][f'ref_images.ref_image_{i}'] = [resize_key, 0]
    return g
