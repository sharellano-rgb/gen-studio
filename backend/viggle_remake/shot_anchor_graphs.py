"""Recovered validated Klein graph; no dependency/model installation."""
RECIPE={'version':'klein-shot-1','model':'flux2Klein9bFp8_fp8.safetensors','clip':'qwen_3_8b_fp8mixed.safetensors','vae':'flux2-vae.safetensors','steps':4,'cfg':1,'sampler':'euler','scheduler':'Flux2Scheduler'}
def klein(p, ident, names, width, height):

    def n(kind, **inputs):
        return {'class_type': kind, 'inputs': inputs}
    text = p['prompt'].replace('<image1>', 'image 1').replace('<image2>', 'image 2').replace('<image3>', 'image 3')
    g = {'model': n('UNETLoader', unet_name='flux2Klein9bFp8_fp8.safetensors', weight_dtype='default'), 'clip': n('CLIPLoader', clip_name='qwen_3_8b_fp8mixed.safetensors', type='flux2', device='default'), 'vae': n('VAELoader', vae_name='flux2-vae.safetensors'), 'positive': n('CLIPTextEncode', clip=['clip', 0], text=text), 'negative': n('ConditioningZeroOut', conditioning=['positive', 0]), 'noise': n('RandomNoise', noise_seed=p['seed']), 'sampler': n('KSamplerSelect', sampler_name='euler'), 'sigmas': n('Flux2Scheduler', steps=4, width=width, height=height), 'latent': n('EmptyFlux2LatentImage', width=width, height=height, batch_size=1)}
    pos, neg = (['positive', 0], ['negative', 0])
    for i, name in enumerate(names):
        g[f'input{i}'] = n('LoadImage', image=name)
        g[f'scale{i}'] = n('ImageScaleToTotalPixels', image=[f'input{i}', 0], upscale_method='lanczos', megapixels=0.65, resolution_steps=16)
        g[f'enc{i}'] = n('VAEEncode', pixels=[f'scale{i}', 0], vae=['vae', 0])
        g[f'pos{i}'] = n('ReferenceLatent', conditioning=pos, latent=[f'enc{i}', 0])
        g[f'neg{i}'] = n('ReferenceLatent', conditioning=neg, latent=[f'enc{i}', 0])
        pos, neg = ([f'pos{i}', 0], [f'neg{i}', 0])
    g['guider'] = n('CFGGuider', model=['model', 0], positive=pos, negative=neg, cfg=1.0)
    g['sample'] = n('SamplerCustomAdvanced', noise=['noise', 0], guider=['guider', 0], sampler=['sampler', 0], sigmas=['sigmas', 0], latent_image=['latent', 0])
    g['decode'] = n('VAEDecode', samples=['sample', 0], vae=['vae', 0])
    g['save'] = n('SaveImage', images=['decode', 0], filename_prefix=f'gen_studio/{ident}/klein')
    return g
