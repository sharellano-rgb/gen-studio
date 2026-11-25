"""Versioned identity-first conditioning for new Pika Object Swap takes."""
from copy import deepcopy
from typing import Literal
from pydantic import Field, model_validator

FIELD='pika_swap_direction'
VERSION='masked-identity-1'

def enabled(p):
    value=p.get(FIELD)
    if value is not None and value!=VERSION:raise ValueError('Versión de indicaciones Pika desconocida.')
    return value==VERSION and p.get('engine')=='pika_ref2va' and p.get('mode')=='swap'

RULES='''OBJECT SWAP IDENTITY AND SCENE CONTRACT:
The explicit user request and relevant later USER corrections define the replacement. Source observations describe INPUT evidence, not the identity to render. The supplied swap_task.original_user_request is an instruction, not an image caption. Inherited feedback keeps its original source interval: opening-foot, pose and time-specific corrections apply only there. Explicit whole-take decisions to retain the scene/audio and general reference-identity preferences continue across subsequent windows unless the user limited them to a time or shot. Current explicit corrections take precedence; do not repeat a scope question already resolved for the whole take.
Return effective_request: a concise ENGLISH description of the resolved final replacement, including reference identity, retained original scene and relevant corrections. Return source_subject: a short visual description identifying the selected original performer. identity_notes must concretely describe the assigned appearance reference, distinguishing its face, anatomy, clothing and accessories from the source even if both wear similar clothing. Return replacement_changes: concrete ENGLISH visual differences to APPLY, comparing the source and reference observations: original face versus reference face, original hands/body versus reference anatomy, original garment details/accessories versus the reference ones. Do not merely repeat the shared category (e.g. both are people or both wear black). Name visible reference-specific features that must appear in the output and source-specific details to replace; do not invent differences when uncertain. Replace the entire selected source head, face, body and outfit from the first through the last visible frame, including turns and occlusions. The reference image supplies appearance, never its pose or backdrop.
Return preserved_scene: a positive concise ENGLISH description of the actual source environment, camera and lighting, excluding the selected actor's appearance. Describe the desired visible scene directly, including background people and props. Qwen analyzes the ORIGINAL frames; the rendering adapter will invert only the source performer using a SAM mask to suppress source identity. This processing does not crop the scene. Do not describe inverted colors as final appearance or include masking instructions in your output fields. Keep background people, scenery, non-character props and camera motion. Match the replacement's size, position, contacts and perspective to the moving source; remove the original character's appearance, not its performance. Reconstruct the replacement's contact shading and any cast shadow on ORIGINAL receiving surfaces under ORIGINAL lighting. Do not retain source-shaped shadows or copy reference lighting; do not invent hard shadows where uncertain.
SCOPE CHECK: Object Swap retains the source scene and restores the original audio. If the unresolved explicit request changes the whole environment, set scope_conflict=scene_change (or scene_and_audio), ready=false and ask a short Spanish question explaining that a different environment belongs to Motion Transfer, or ask to confirm retaining this scene. Do not silently omit the requested change or suggest recoloring the stage as a substitute. If the user explicitly requests new speech, screams, music or other generated/replaced audio, set scope_conflict=audio_change (or scene_and_audio), ready=false and explain that this method retains original audio. A mention of sounds already present is CONTEXT, not an instruction to add sound. Do not block an ordinary replacement request. When a later user answer confirms retaining the original scene/audio, that answer supersedes the earlier conflicting request: set scope_conflict=none and continue if otherwise clear. Never automatically change methods. Never promise unsupported audio additions or postprocessing.
pose_notes and motion_notes describe source-driven local posture, head orientation, contacts, trajectory, camera-relative scale and timing. Do not substitute the reference stance, mirror anatomical sides or label a changing trajectory as a stationary rotation. Actual local frames govern uncertain sampled captions. special_notes must agree with effective_request and preserved_scene. Whole-video evidence provides chronology only; do not copy raw observation paragraphs into render directions or replay the opening at each boundary. Keep fields concise, in English, without native media tags. reply_es must describe the replacement and retained scene so the user can review it. When suggesting Motion Transfer, suggest it ONLY for the environment; do not suggest that it adds requested audio. Never promise pixel-exact motion, background or identity.'''

def prior_feedback(service,job):
    """Carry explicit earlier answers within this sequence, including across cuts."""
    p=job['parameters'];owner=p.get('sequence_parent_id');index=p.get('sequence_index',0)
    if not enabled(p) or not owner or not index:return []
    from ..video_sequences import child_id
    from .. import qwen_director_store as records
    result=[]
    parent=service.store.job(owner) or owner
    for i in range(index):
        previous=service.store.job(child_id(parent,i))
        if not previous or previous['status']!='completed':continue
        pp=previous['parameters']
        if (pp.get('sequence_parent_id')!=owner or pp.get('sequence_index')!=i
                or pp.get('reference_sha256')!=p.get('reference_sha256') or not enabled(pp)):continue
        state=records.read(service.store,previous['id']) or {}
        corrections=[m for m in state.get('messages',[]) if m['role']=='user']
        if corrections and state.get('plan',{}).get('ready'):
            result.append({'source_interval':state['context']['source'],'user_messages':corrections[-8:],
                'correction_summary':state['plan']['special_notes']})
    return result

def resolved_inputs(inputs):
    """Do not present a stale complete JSON answer for Qwen to copy verbatim."""
    inputs=deepcopy(inputs)
    old=inputs.pop('previous_plan',None)
    if old:
        inputs['previous_visual_guidance']={k:old[k] for k in ('pose_notes','motion_notes','identity_notes') if k in old}
    # The global evidence already has its own named field. Avoid duplicating it
    # ahead of the actual user response in the finite text context.
    if 'whole_video_context' in inputs:inputs['visual_observations'].pop('global_context',None)
    inputs['current_user_direction']={'latest_message':inputs.pop('latest_user_message'),
        'prior_explicit_user_messages':[m['content'] for m in inputs['conversation'] if m['role']=='user'],
        'earlier_window_answers':inputs['inherited_feedback'],
        'instruction':'Resolve the current goal from these USER corrections. They supersede conflicting parts of the original form and stale assistant replies. Evaluate scope again; never repeat an already answered question. Keep original settings only where they have not been corrected.'}
    return inputs

def plan_schema():
    from .director import plan_schema as base_schema
    class SwapPlan(base_schema()):
        effective_request:str=Field(max_length=1000,description='ENGLISH resolved replacement goal, source scene retention and relevant corrections.')
        preserved_scene:str=Field(max_length=1000,description='ENGLISH concise ORIGINAL environment, camera and lighting; exclude selected actor appearance.')
        source_subject:str=Field(max_length=500,description='ENGLISH short description identifying the selected SOURCE performer.')
        replacement_changes:str=Field(max_length=900,description='ENGLISH concrete visual changes from source to reference: face, anatomy, hands, clothing and accessories. Distinguish even similar characters.')
        scope_conflict:Literal['none','scene_change','audio_change','scene_and_audio']=Field(description='Unresolved explicit request for a new environment or new audio. Mention of existing sound is not an audio change.')

        @model_validator(mode='after')
        def complete_replacement(self):
            for name in ('effective_request','preserved_scene','source_subject','identity_notes','replacement_changes'):
                text=getattr(self,name)
                if '<' in text or '>' in text:raise ValueError('Las indicaciones no deben incluir etiquetas nativas.')
                if self.ready and not text.strip():raise ValueError('Falta definir el reemplazo o la escena original.')
            if self.scope_conflict!='none':
                if self.ready:raise ValueError('Aclara el cambio de escenario o audio antes de generar Object Swap.')
                if not any(q.strip() for q in self.questions):raise ValueError('Falta la pregunta para resolver el alcance de Object Swap.')
            return self
    return SwapPlan

def adapt(graph,plan,p):
    if not enabled(p):raise ValueError('Esta toma no utiliza las nuevas indicaciones de Object Swap.')
    plan=plan_schema().model_validate(plan)
    if not plan.ready:raise ValueError('Responde la aclaración de Qwen antes de generar.')
    from . import motion_direction
    render={k:getattr(plan,k) for k in ('effective_request','pose_notes','motion_notes','identity_notes','special_notes','reply_es','questions','ready')}
    render['destination_scene']=plan.preserved_scene
    render['source_subject']='the color-inverted foreground performer, used only for motion, articulation, trajectory, timing and camera-relative scale; inverted colors are a guide visualization, never final appearance'
    # The successful Motion compiler is reused without mutating its behavior,
    # the accepted request, sampler, reference budget or native media roles.
    params={**p,'mode':'motion',motion_direction.FIELD:motion_direction.VERSION}
    result,changed=motion_direction.adapt(graph,render,params)
    text=result['conditioning']['inputs']['prompt']
    text=text.replace('Natural destination ambience, no invented speech. The selected original audio is restored during final assembly.',
        'Preserve the selected original audio; it is restored during final assembly. No added speech, screams or other generated sound.')
    text=text.replace('non_diegetic_music:\nNone.','non_diegetic_music:\nNo added music.')
    result['conditioning']['inputs']['prompt']=text
    return result,changed
