"""Approved full-reference hands recipe; legacy graphs remain unchanged."""
def render_frames(frames):
    if type(frames) is not int or not 5 <= frames <= 360:
        raise ValueError('Usa entre 5 fotogramas y 15 segundos.')
    return frames + (5 - frames) % 17

def apply(graph, character_version='v1'):
    from .character_lora import hands_strength
    graph['3']['inputs']['strength_model'] = hands_strength(character_version)
    for key, node in graph.items():
        if key == '18' or key.startswith('reference_resize_'):
            node['inputs']['keep_proportion'] = 'pad'
    return graph
