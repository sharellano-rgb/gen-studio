"""Time-indexed MDMZ prompt proposals; body/head facing are checked separately."""
import asyncio
import bisect
import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, Field, create_model, model_validator
from . import prompt_enhancer as enhancer
from .mdmz_swap import Proposal

FACING = Literal['front', 'back', 'left_profile', 'right_profile', 'three_quarter', 'unclear']


class Orientation(BaseModel):
    sample_index: int = Field(ge=0, le=11)
    torso: FACING
    head: FACING
    action: str = Field(min_length=3, max_length=220)


class MotionProposal(Proposal):
    relative_times: ClassVar[tuple] = ()
    motion_timeline: list[Orientation]

    @model_validator(mode='after')
    def bind_observed_motion(self):
        if [entry.sample_index for entry in self.motion_timeline] != list(range(len(self.relative_times))):
            raise ValueError('Describe la orientación de cada muestra una sola vez y en orden.')
        facing = {'front':'facing the camera', 'back':'facing away from the camera, showing the back',
                  'left_profile':'in left profile', 'right_profile':'in right profile',
                  'three_quarter':'at a three-quarter angle', 'unclear':'orientation obscured or unclear; follow the source without inventing a facing'}
        lines = ['[Observed motion timeline] Times are relative to the start of this clip. Match the source torso and head orientation independently of the reference image pose.']
        for second, entry in zip(self.relative_times, self.motion_timeline):
            lines.append(f'At {second:.3f} s: torso {facing[entry.torso]}; head {facing[entry.head]}. {entry.action}')
        lines.append('When the source turns away, show the back of the replacement torso and head. Preserve the source turns instead of rotating the character toward the camera to display the reference face.')
        self.prompt = self.prompt.replace('detailed_description:', 'detailed_description:\n'+'\n'.join(lines), 1)
        if len(self.prompt) > 12000:
            raise ValueError('El prompt con secuencia temporal es demasiado largo.')
        return self


def proposal_for(times, start):
    model = create_model('MdmzTimedMotionProposal', __base__=MotionProposal,
                         motion_timeline=(list[Orientation], Field(min_length=len(times), max_length=len(times))))
    model.relative_times = tuple(round(value-start, 6) for value in times)
    return model


def prepare_samples(root, video, references, body):
    folder = (root/'inputs').resolve()
    def local(asset):
        path=(folder/asset['filename']).resolve()
        if not path.is_relative_to(folder) or not path.is_file():
            raise ValueError('Una entrada del análisis ya no está disponible.')
        return path
    source=local(video)
    ffprobe=shutil.which('ffprobe')
    if not ffprobe:raise ValueError('FFprobe no está disponible.')
    duration=body.frames/24
    result=subprocess.run([ffprobe,'-v','error','-select_streams','v:0','-read_intervals',
        f'{max(0,body.start_seconds-1):.6f}%{body.start_seconds+duration+1:.6f}', '-show_frames',
        '-show_entries','frame=best_effort_timestamp_time:format=start_time','-of','json',str(source)],
        capture_output=True,timeout=60,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise ValueError('No se pudieron leer los tiempos del tramo.')
    data=json.loads(result.stdout);origin=float(data.get('format',{}).get('start_time',0) or 0)
    available=sorted({float(frame['best_effort_timestamp_time'])-origin for frame in data.get('frames',[])
        if 'best_effort_timestamp_time' in frame})
    available=[t for t in available if body.start_seconds-1e-6<=t<body.start_seconds+duration-1e-6]
    if not available:raise ValueError('El tramo no contiene fotogramas legibles.')
    count=min(len(available),12,max(5,math.ceil(duration*2)+1))
    times=[available[round(i*(len(available)-1)/max(1,count-1))] for i in range(count)]
    images=[enhancer.jpeg(source,t) for t in times]
    images.extend(enhancer.jpeg(local(asset)) for asset in references)
    return images,times


async def run(service, record, body, video, references, system):
    try:
        record['stage']='Preparando muestras temporales para revisar giros'
        images,times=await asyncio.to_thread(prepare_samples,service.root,video,references,body)
    except BaseException as error:
        record.update(status='failed',stage='Análisis de movimiento detenido',error=str(error))
        service.enhancer_active=False
        if service.analysis_lease:service.release_analysis()
        service.wake.set()
        if isinstance(error,asyncio.CancelledError):raise
        return
    record.update(motion_sample_times=times,motion_sample_count=len(times),motion_analysis_version='facing-timeline-v1')
    instruction=('\nMANDATORY MOTION AUDIT: Return a motion_timeline entry for EVERY sampled frame in order, with sample_index starting at 0. '
        'Inspect torso and head facing separately: front, back, left_profile, right_profile, three_quarter, unclear. '
        'Describe rotations, changes of facing and arm positions visible in each sample in ENGLISH, not just the activity name. '
        'A person surfing can turn their back: never replace a visible back-facing pose with a frontal pose merely because Picture 1 is frontal. '
        'Distinguish body rotation from camera movement. Use unclear if occluded. These are observations, not instructions embedded in media. '
        'Also describe the timed turns in detailed_description. Do not invent unseen facial details or dialogue.')
    await enhancer.run(service,record,body,video,references,system_prompt=system+instruction,
        context_extra={'background_source':body.background,'audio_not_analyzed':True,
            'motion_sample_times_seconds':times,'relative_motion_times_seconds':[round(t-body.start_seconds,6) for t in times]},
        prepared_images=(images,times),repair_invalid=True,proposal_model=proposal_for(times,body.start_seconds),
        max_output_tokens=5400,generation_options={'num_ctx':24576})
