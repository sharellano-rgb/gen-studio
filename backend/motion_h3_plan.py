"""Exact H3 windows with bounded context and an immutable CPU retiming budget.

The model always renders 107 frames. Each window reserves 18 frames so any
manual advance in the supported range can be applied within that window,
without changing the output intervals or generating replacement windows.
Context is input-only; it is never duplicated in the assembled result.
"""

NATIVE_FRAMES = 107
MAX_FRAMES = 720
CONTEXT_FRAMES = 12
TIMING_BUDGET = 18
MAX_SEGMENTS = 12


def plan_segments(frames, cuts=()):
    """Return frame-exact output intervals and source windows for one take.

    Cuts refer to frames relative to the already selected source trim. They
    reset the context and bound every reference window to its own shot. The
    caller clones that shot's final source frame to reach NATIVE_FRAMES when
    source_frames is shorter, rather than conditioning across a scene cut.
    """
    if type(frames) is not int or not 5 <= frames <= MAX_FRAMES:
        raise ValueError('Motion H3 admite entre 5 fotogramas y 30 segundos a 24 fps.')
    if not isinstance(cuts, (list, tuple)):
        raise ValueError('Los cortes de Motion H3 requieren una lista de fotogramas enteros.')
    if any(type(cut) is not int or not 0 < cut < frames for cut in cuts):
        raise ValueError('Los cortes deben ser fotogramas enteros dentro de la toma.')
    if len(set(cuts)) != len(cuts):
        raise ValueError('Los cortes no pueden repetirse.')

    boundaries = [0, *sorted(cuts), frames]
    result = []
    for shot, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
        output_start = start
        while output_start < end:
            context = min(CONTEXT_FRAMES, output_start - start)
            input_start = output_start - context
            keep = min(NATIVE_FRAMES - context - TIMING_BUDGET, end - output_start)
            result.append({
                'index': len(result),
                'output_start': output_start,
                'input_start': input_start,
                'context': context,
                'keep': keep,
                'source_frames': min(NATIVE_FRAMES, end - input_start),
                'shot': shot,
            })
            if len(result) > MAX_SEGMENTS:
                raise ValueError('La toma requiere más de doce ventanas H3 debido a los cortes. Usa un tramo con menos cortes.')
            output_start += keep
    return result
