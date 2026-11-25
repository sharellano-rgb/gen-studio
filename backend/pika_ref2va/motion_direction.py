"""Versioned destination-first conditioning for new Pika Motion Transfer takes."""
from copy import deepcopy
from pydantic import Field, model_validator

FIELD='pika_motion_direction'
VERSION='destination-first-1'

def enabled(p):
    value=p.get(FIELD)
    if value is not None and value!=VERSION:raise ValueError('Versión de indicaciones Pika desconocida.')
    return value==VERSION and p.get('engine')=='pika_ref2va' and p.get('mode')=='motion'

RULES='''MOTION TRANSFER DESTINATION CONTRACT:
The explicit user request and relevant later USER corrections define the output. Source observations describe the INPUT, not the destination to render. The supplied motion_task.original_user_request is an instruction, not an image caption. Integrate inherited feedback only within its bound source interval; current explicit corrections take precedence.
Return effective_request: a concise ENGLISH description of the requested final transformation, including the replacement identity and destination, with later corrections integrated. Never silently omit a requested environment change. Return destination_scene: a concrete ENGLISH description of the intended output setting, supports and lighting; do not substitute the observed source background. If the user requests a fantasy setting, describe that setting without copying the source venue, spectators or props unless explicitly requested. When a user explicitly wants to retain the original environment, describe that retained setting. Do not invent an unrelated setting when none is specified; retain the source setting unless the goal is genuinely ambiguous, then ask briefly.
Return source_subject: a concise visual description identifying the selected original performer, not the replacement. identity_notes must concretely describe the assigned appearance reference. Distinguish source and replacement even if both have similar clothing. Transfer the source action to the reference character, replacing the entire source head/face/body/outfit, never merely restyling the original person.
pose_notes and motion_notes describe only source-driven local posture, contacts, trajectory, camera-relative scale and timing. Avoid source-venue prose in these fields. Do not label a whole action a stationary rotation when the actual frames show translation, changing distance or camera motion. Keep uncertainty explicit.
special_notes must agree with effective_request and destination_scene, including destination contact shadows. Source shadow receiving surfaces and colored stage lighting must not override an explicitly changed destination. The reference white backdrop is not the destination. Never promise pixel-exact motion or identity. Do not copy raw observation paragraphs into the render directions. Keep all text fields concise and without media tags; the adapter binds the native roles. reply_es must state the intended replacement AND destination so the user can inspect the plan.'''

def plan_schema():
    from .director import plan_schema as base_schema
    class MotionPlan(base_schema()):
        effective_request:str=Field(max_length=1000,description='ENGLISH resolved user goal: replacement, destination and requested retention. Integrate relevant explicit corrections.')
        destination_scene:str=Field(max_length=1000,description='ENGLISH intended OUTPUT environment, supports and lighting, not a caption of the input.')
        source_subject:str=Field(max_length=500,description='ENGLISH short visual description of the selected SOURCE performer.')

        @model_validator(mode='after')
        def complete_destination(self):
            for name in ('effective_request','destination_scene','source_subject'):
                text=getattr(self,name)
                if '<' in text or '>' in text:raise ValueError('Las indicaciones no deben incluir etiquetas nativas.')
                if self.ready and not text.strip():raise ValueError('Falta definir el personaje o escenario solicitado.')
            return self
    return MotionPlan

def adapt(graph,plan,p):
    if not enabled(p):raise ValueError('Esta toma no utiliza las nuevas indicaciones de Motion Transfer.')
    plan=plan_schema().model_validate(plan)
    if not plan.ready:raise ValueError('Responde la aclaración de Qwen antes de generar.')
    result=deepcopy(graph)
    nodes=[(k,n) for k,n in result.items() if n['class_type']=='MiniMaxH3ReferenceToVideo']
    if len(nodes)!=1:raise ValueError('Falta un único condicionamiento nativo de Pika.')
    key,node=nodes[0]
    start=int(p.get('sequence_start_frame',0));context=int(p.get('sequence_context',0));keep=int(p.get('sequence_keep',p['frames']))
    node['inputs']['prompt']=f'''subject_definitions:
<Subject 1> is the selected original performer in <Video 1>: {plan.source_subject}
<Subject 2> is the replacement character in <Picture 1>: {plan.identity_notes}
The output destination is: {plan.destination_scene}

summary:
[reference generation] {plan.effective_request}

retention_analysis:
<Subject 1>: source appearance removed; pose, articulation, head orientation, contacts, timing, trajectory and camera-relative scale attribute_transfer from <Video 1> to <Subject 2>.
<Subject 2>: identity, face, head, anatomy, outfit and accessories fully_preserved from <Picture 1>; its static pose, background and photographed lighting removed. Replace the source performer from the first visible frame through the last, including turns and occlusions.
Destination: {plan.destination_scene} Keep this requested destination throughout the entire window. Remove source scenery, people and supporting props that conflict with it; retain source elements only when requested in the resolved goal. Do not revert to the input environment.

detailed_description:
{plan.effective_request}
{plan.pose_notes}
{plan.motion_notes}
{plan.special_notes}
CURRENT WINDOW: selected-take frames {start} to {start+context+keep-1}, with {context} hidden continuity frames and {keep} visible frames. Continue only the action in this interval; do not restart the whole-video opening or repeat later events. Actual local video motion governs uncertain sampled captions. Preserve observed camera motion and changing scale. A continuity guide controls the previous endpoint, not a new character or setting.
Preserve the reference character in the requested destination through the final frame. Reconstruct contact shading and any cast shadow on destination surfaces under destination lighting; do not paste source-shaped shadows or the reference backdrop. Do not invent hard shadows when lighting is uncertain.

overall_soundscape:
Natural destination ambience, no invented speech. The selected original audio is restored during final assembly.

non_diegetic_music:
None.'''
    return result,[{'node':key,'field':'prompt'}]
