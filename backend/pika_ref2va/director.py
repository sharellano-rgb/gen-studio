from pydantic import Field

MOTION_SCOPE='''DESTINATION SCENE AUTHORITY: The original-scene descriptions below are observations for timing and body motion, not instructions to reproduce that background. Render the user's destination for the entire window, including its last frame. Replace source scenery and supports that conflict with the destination; do not gradually revert to the original scene or props. Preserve the performer's source trajectory, articulation and camera-relative scale while adapting physical support to the destination. Camera motion follows the actual video, not an uncertain sampled claim that it is static.\n'''
MOTION_SHADOW='''CAST-SHADOW DESTINATION: Reconstruct the replacement character's contact shading and any visible projected shadow on the DESTINATION receiving surfaces under its lighting. Source shadow observations are contextual evidence, not a request to preserve source scenery, surfaces or source-shaped shadows. Do not paste the appearance reference's shadow. Preserve temporal and contact continuity without inventing hard shadows where lighting is diffuse or uncertain.\n'''

RULES='''PIKA REF2VA SCOPE: The source video is the motion authority; the single image supplies replacement appearance only, NEVER its pose. Object Swap preserves the source scene; Motion Transfer follows the user's destination-scene description while preserving body performance, trajectory and timing. Do not invent a Nimbus cloud, surfboard, particular character, left-foot opening or static camera for unrelated inputs. Separate the observed torso/chest/shoulder angle from head yaw/downward pitch; describe arms, planted/airborne feet, knees and visible contacts at the actual current-window opening. A user-corrected opening foot applies at that original source time only, not all segment boundaries. Use anatomical sides only when supported and keep screen directions distinct. Never mirror the source.
Use the complete-video context to understand the action, but local frames govern articulation. Carry later events only into their real timeline window; preserve changes in apparent scale, distance and camera tracking/tilt. Compare scene landmarks before claiming the camera is fixed. Do not say both fixed camera and tracking; when uncertain say follow the observed source camera. Describe shadows only from the full source/destination scene evidence; uncertain water/foam reflections are not definite cast shadows. Appearance references supply no destination lighting. Do not promise exact geometric control or pixel-identical backgrounds. Remove conflicting earlier assumptions when the user corrects them. Keep instructions concise, in English, and explain changes in Spanish. No native media tags: the adapter binds those roles. The generation uses an optional previous-window endpoint as a hidden continuity guide, not another identity.'''

def plan_schema():
    from ..qwen_director import Plan
    class RefPlan(Plan):
        pose_notes:str=Field(max_length=900)
        motion_notes:str=Field(max_length=1200)
        identity_notes:str=Field(max_length=700)
        special_notes:str=Field(max_length=1200)
    return RefPlan
