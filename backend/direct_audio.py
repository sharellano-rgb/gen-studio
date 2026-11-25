"""Exclude source audio from H3 while preserving a separate final mux source."""
from pathlib import Path


def apply(graph, parameters):
    if not parameters.get('direct_video_only'):
        return graph
    for node in graph.values():
        inputs = node['inputs']
        if node['class_type'] == 'MiniMaxH3ReferenceToVideo':
            for key in list(inputs):
                if key.startswith(('ref_audios.', 'ref_video_audios.')):
                    inputs.pop(key)
        if node['class_type'] == 'CreateVideo':
            inputs.pop('audio', None)
    for key in list(graph):
        if graph[key]['class_type'] == 'VAEDecodeAudio':
            del graph[key]
    validate(graph)
    return graph


def validate(graph):
    # The audio VAE is still required by H3's joint architecture. It is not
    # source-audio conditioning; reject actual audio inputs/edges instead.
    components = {key for key, node in graph.items() if node['class_type'] == 'GetVideoComponents'}
    for node in graph.values():
        if node['class_type'] in ('LoadAudio', 'VAEEncodeAudio', 'VAEDecodeAudio'):
            raise ValueError('El workflow conserva una ruta de audio; crea otra copia sin audio para H3.')
        for key, value in node['inputs'].items():
            if key.startswith(('ref_audios.', 'ref_video_audios.')) or (
                    isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and value[0] in components and value[1] == 1):
                raise ValueError('El workflow conserva una referencia de audio; no se enviará a H3.')


def prepared_input(parameters, prepared):
    enabled = bool(parameters.get('direct_video_only'))
    if bool(prepared.get('video_only')) != enabled:
        raise ValueError('La preparación guardada no corresponde a la opción de audio; crea otra copia.')
    if not enabled:
        return prepared['video'], prepared['audio']
    path = prepared.get('h3_video')
    if not path or prepared.get('h3_audio') is not False or path not in prepared['files'] or not Path(path).is_file():
        raise ValueError('Falta la copia de vídeo sin audio para H3.')
    return path, False
