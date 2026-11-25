"""Exact frame intervals for bounded H3 jobs; preserve the original workflow."""
SHORT_FRAMES=(73,90,107,124)
MAX_FRAMES=360
SCENE_CUT_MAX_FRAMES=60*24
CONTINUOUS_CHUNK_VERSION='continuous-5s-v1'
DIRECT_7S_VERSION='h3-direct-continuous-7s-v1'
DIRECT_7S_FRAMES=7*24
CHUNK_SIZES={CONTINUOUS_CHUNK_VERSION:120, DIRECT_7S_VERSION:DIRECT_7S_FRAMES}


def continuous_chunk_size(parameters):
    size=parameters.get('direct_chunk_frames')
    version=parameters.get('direct_chunk_version')
    if size is None and version is None:return None
    if (type(size) is not int or size!=CHUNK_SIZES.get(version)
            or parameters.get('engine')!='h3_direct' or parameters.get('direct_scene_cuts')
            or type(parameters.get('frames')) is not int or not (120 if version==DIRECT_7S_VERSION else 240)<parameters['frames']<=(2880 if version==DIRECT_7S_VERSION else 1440)):
        raise ValueError('La configuración de tramos automáticos no es válida.')
    return size


def plan_segments(frames,cuts=(),scene_cuts=False,validate_only=False,chunk_frames=None):
    if chunk_frames is not None:
        if type(chunk_frames) is not int or chunk_frames not in CHUNK_SIZES.values() or scene_cuts:
            raise ValueError('Los tramos automáticos requieren una duración de ventana compatible.')
        limit=2880 if chunk_frames==DIRECT_7S_FRAMES else 1440
        if type(frames) is not int or not 5<=frames<=limit:
            raise ValueError(f'Los tramos automáticos admiten hasta {limit//24} segundos a 24 fps.')
        if validate_only:return []
        boundaries=[0]+sorted({int(c) for c in cuts if 0<int(c)<frames})+[frames]
        result=[]
        for start,end in zip(boundaries,boundaries[1:]):
            cursor=start
            while cursor<end:
                keep=min(chunk_frames,end-cursor)
                # Avoid an unchanged 1–4-frame tail within a continuous shot.
                if 0<end-cursor-keep<5:keep-=5-(end-cursor-keep)
                result.append(dict(index=len(result),start=cursor,keep=keep,
                    render_frames=max(5,keep+(5-keep)%17),passthrough=keep<5,
                    ends_at_cut=cursor+keep==end and end<frames))
                cursor+=keep
        return result
    if scene_cuts:
        if type(frames) is not int or not 3 <= frames <= SCENE_CUT_MAX_FRAMES:
            raise ValueError('El modo por cortes admite hasta 60 segundos a 24 fps.')
        if validate_only:return []
        boundaries=[0]+sorted({int(c) for c in cuts if 0<int(c)<frames})+[frames]
        result=[]
        for start,end in zip(boundaries,boundaries[1:]):
            keep=end-start
            if keep>345:
                raise ValueError(f'El plano {start/24:.2f}-{end/24:.2f} s supera 14.38 s entre los cortes que pudo reconocer el detector. Puede haber cortes no reconocidos; no se ha iniciado la generación.')
            render=max(5,keep+(5-keep)%17)
            result.append({'index':len(result),'start':start,'keep':keep,'render_frames':render,
                'passthrough':False,'ends_at_cut':end<frames})
        return result
    if type(frames) is not int or not 3<=frames<=MAX_FRAMES:
        raise ValueError('Usa entre 3 fotogramas y 15 segundos a 24 fps.')
    boundaries=[0]+sorted({int(c) for c in cuts if 0<int(c)<frames})+[frames]
    result=[]
    for start,end in zip(boundaries,boundaries[1:]):
        while start<end:
            keep=min(107,end-start)
            result.append({'index':len(result),'start':start,'keep':keep,
                'render_frames':next(n for n in SHORT_FRAMES if n>=keep),
                'passthrough':keep<3,'ends_at_cut':start+keep==end and end<frames})
            start+=keep
    assert sum(s['keep'] for s in result)==frames
    return result


def reviewed_segments(frames,cuts,passthrough_indices=()):
    if type(frames) is not int or not 3<=frames<=SCENE_CUT_MAX_FRAMES:raise ValueError('La revisión admite hasta 60 segundos.')
    if not isinstance(cuts,list) or any(type(c) is not int or not 0<c<frames for c in cuts) or cuts!=sorted(set(cuts)):raise ValueError('Los límites de planos no son válidos.')
    bounds=[0,*cuts,frames];excluded=list(passthrough_indices)
    if any(type(i) is not int or not 0<=i<len(bounds)-1 for i in excluded) or len(set(excluded))!=len(excluded):raise ValueError('La selección de planos originales no es válida.')
    result=[]
    for i,(start,end) in enumerate(zip(bounds,bounds[1:])):
        keep=end-start;passthrough=i in excluded
        if keep>345 and not passthrough:raise ValueError(f'El plano {i+1} supera 14.38 s continuos. Recorta la toma o conserva ese plano original.')
        result.append(dict(index=i,start=start,keep=keep,render_frames=keep if passthrough else max(5,keep+(5-keep)%17),passthrough=passthrough,ends_at_cut=end<frames))
    return result
