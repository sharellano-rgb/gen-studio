"""Native H3 with fixed source audio; the original audio is muxed once at the end."""
from copy import deepcopy

PERFORMANCE=(' Transfer the source performers\' facial and vocal performance to the corresponding reference characters. '
    'Match changing mouth opening, jaw motion, rounded and wide vowels, brief closures, eyebrows and eyes '
    'at the source moments. Adapt articulation naturally to each character\'s anatomy while preserving identity '
    'and head size. Follow actual singing, speaking and pauses in the source video and soundtrack. '
    'Keep body choreography, object contact and camera timing together with facial acting.')


def fixed_audio_graph(graph,audio):
    if not audio:raise ValueError('La interpretación vocal requiere audio fuente preparado.')
    g=deepcopy(graph);g.pop('2',None)
    for node in g.values():
        for key,value in node['inputs'].items():
            if value==['2',0]:node['inputs'][key]=['1',0]
    g['8']['inputs']['prompt']=g['8']['inputs']['prompt'].replace('No soundtrack.','')+PERFORMANCE
    def n(kind,**inputs):return {'class_type':kind,'inputs':inputs}
    g['40']=n('LoadAudio',audio=audio)
    g['41']=n('VAEEncodeAudio',audio=['40',0],vae=['5',0])
    g['42']=n('SolidMask',value=0.0,width=1,height=1)
    g['43']=n('SetLatentNoiseMask',samples=['41',0],mask=['42',0])
    g['44']=n('LTXVConcatAVLatent',video_latent=['8',1],audio_latent=['43',0])
    g['13']['inputs']['latent_image']=['44',0]
    return g
