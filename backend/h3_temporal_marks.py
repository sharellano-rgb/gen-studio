"""Relocate a confirmed cast when an opening frame cannot establish their positions."""
from typing import Literal

from pydantic import Field, model_validator

from . import h3_integral_review as integral
from .head import digest
from .job_cancel import thread_checkpoint
from .visual_targets import frame_path


class VisibleSubject(integral.Strict):
    state: Literal['visible','absent','uncertain']
    bbox: list[int] | None

    @model_validator(mode='after')
    def normalized_box(self):
        if self.state=='visible':
            if not self.bbox or len(self.bbox)!=4:
                raise ValueError('A visible subject needs a body box.')
            l,t,r,b=self.bbox
            if not (0<=l<r<=1000 and 0<=t<b<=1000):
                raise ValueError('Invalid normalized subject box.')
        elif self.bbox is not None:
            raise ValueError('Only a visible subject has a box.')
        return self


class CastAtFrame(integral.Strict):
    subjects: list[VisibleSubject]=Field(min_length=2,max_length=2)


class SoloAtFrame(integral.Strict):
    subjects: list[VisibleSubject]=Field(min_length=1,max_length=1)


def face_slots(detections,subjects,size):
    w,h=size
    candidates=[]
    for subject in subjects:
        hits=[]
        if subject.state=='visible':
            l,t,r,b=subject.bbox
            for i,(x,y,right,bottom) in enumerate(detections):
                cx,cy=(x+right)*500/w,(y+bottom)*500/h
                if l<=cx<=r and t<=cy<=t+.75*(b-t):
                    hits.append(i)
        candidates.append(hits)
    return [detections[hits[0]] if len(hits)==1 and sum(hits[0] in v for v in candidates)==1 else None
            for hits in candidates]


async def locate(service,job,info,cards,proofs):
    from .h3_face_fidelity import path_for
    anchors=[]
    labels=[]
    mark_frames=[]
    for c in cards:
        proof=next(v for v in proofs if v['character_id']==c['id'] and v['target']==c['target'])
        path,source_sha=await thread_checkpoint(service,job['id'],frame_path,service,proof['video_id'],proof['frame'])
        if source_sha!=proof['source_sha256'] or digest(path)!=proof['frame_sha256']:
            raise ValueError('El fotograma confirmado cambió. Vuelve a marcar a la persona.')
        anchors.append(path)
        mark_frames.append(proof['frame']-job['parameters'].get('sequence_start_frame',0))
        labels.append({'source_subject':c['target'],'point_xy_0_1000':[round(1000*proof['x']),round(1000*proof['y'])]})
    for actor in info['actors']:
        actor['absent_frames']=[]
    from .h3_swap_policy import applies
    record_bodies = applies(job)
    solo = record_bodies and len(cards)==1
    schema = SoloAtFrame if solo else CastAtFrame
    rules = ('Image 1 is the original confirmation frame. The point identifies ONE source performer. '
             'Locate only that individual in image 2. Give their visible body box in normalized image 2 coordinates. '
             'Absent means out of frame or fully behind an opaque object. A visible torso counts even if the face '
             'is outside the crop. Use uncertain when correspondence is ambiguous. Never borrow a bystander.'
             if solo else
             'Images 1 and 2 are original marked-time frames. The given point in each identifies ONE source '
             'person. Locate those two individuals in IMAGE 3 only, in the same order. Give a tight full '
             'visible body box in normalized image 3 coordinates. Absent means entirely outside the shot '
             'or fully behind an opaque object. A partially visible body is visible even if its face is hidden. '
             'Use uncertain for ambiguous correspondence. Never place a person behind closed doors by inference.')
    for frame in info['indices']:
        row=info['scan'][str(frame)]
        result=await integral.ask(service,job,f'cast-visible-{frame}',schema,rules,
            {'marked_subjects':labels},[*anchors,path_for(service,row['image'])])
        if any(frame==mark and subject.state!='visible' for mark,subject in zip(mark_frames,result.subjects,strict=True)):
            raise ValueError('La localización visual contradice el fotograma confirmado. Revisa la marca de esa persona.')
        row['boxes']=face_slots(row['detections'],result.subjects,info['size'])
        for actor,subject in zip(info['actors'],result.subjects,strict=True):
            if record_bodies and subject.state == 'visible':
                actor.setdefault('visible_body_boxes', {})[str(frame)] = subject.bbox
            if subject.state=='absent':
                actor['absent_frames'].append(frame)
            elif subject.state=='visible' and 'located_frame' not in actor:
                actor.update(bbox=subject.bbox,located_frame=frame)
    for actor in info['actors']:
        if len(info['indices'])>=4 and actor['absent_frames']==info['indices']:
            actor['absent_throughout']=True
        elif 'located_frame' not in actor:
            raise ValueError(f"No se consiguió ubicar a {actor['target']} en las muestras del tramo. Revisa la asignación de este intervalo.")
    info['temporal_cast']=True


async def review_absence(service,job,info,actor,index,frames,label):
    from .h3_face_fidelity import path_for
    if not frames or any(f['frame'] not in actor['absent_frames'] for f in frames):
        raise ValueError('No hay evidencia de ausencia para los fotogramas seleccionados.')
    paths=[path_for(service,image) for frame in frames
           for image in (info['scan'][str(frame['frame'])]['image'],frame)]
    checked=await integral.ask(service,job,f'{label}-absence-{index}',integral.Check,
        'Compare original and generated full images in successive pairs. Verify that the same people '
        'are present in both at each time, with reference substitutions permitted. The source target '
        'was observed absent. Fail if an extra person appears, return uncertain if association is '
        'unclear, and pass only for visibly preserved absence across every pair.',
        {'source_subject':actor['target'],'frames':[item['frame'] for item in frames]},paths)
    checks={k:{'verdict':'not_visible','evidence':'Persona fuera de cuadro en las muestras.'}
            for k in integral.DIMENSIONS}
    checks['confirmed_absence']=checked.model_dump()
    return {'actor':index+1,'checks':checks,'correction':'' if checked.verdict=='pass' else 'Preserve the source absence.'}
