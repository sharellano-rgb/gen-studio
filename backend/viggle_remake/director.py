"""Prompt guidance, without changing native conditioning roles or sampling."""
RULES="""VIGGLE REMAKE SWAP: This implementation uses native swap weights for both character replacement and source-driven motion transfer. Video 1 governs exact body trajectory, framing, camera, background and timing. Picture 1 supplies the replacement identity, anatomy and clothing; never retain the selected source person's face or clothes. Keep other actors and objects. Do not promise arbitrary environment/object editing with swap weights; ask a concrete clarification if requested goals contradict this scope. An appearance reference does not dictate opening posture. Only an explicitly painted_frame reference is aligned to its supplied source frame; do not invent pins. Native continuation freezes 17 context frames from the previous output within a shot and resets at cuts. Do not restart earlier poses at later windows. Respect full-frame observations for scene/shadows and detail crops only for visible articulation; do not invent hidden feet or exact contacts. Preserve source occlusions and distinguish temporary occlusion from identity failure. Keep instructions concise; source pixels govern fine motion. Visual review is advisory, never proof of exact fidelity or grounds for unlimited regeneration."""

def plan_schema():
    from ..qwen_director import Plan
    from pydantic import Field
    class VigglePlan(Plan):
        pose_notes:str=Field(max_length=900)
        motion_notes:str=Field(max_length=1200)
        identity_notes:str=Field(max_length=700)
        special_notes:str=Field(max_length=1000)
    return VigglePlan

MULTI_RULES="""MULTIPLE CHARACTERS: additional_characters is an ordered source-to-reference assignment, not multiple views of the first person. Picture 1 belongs to the primary target; Picture 2 and later belong only to their respective listed targets. Track their original identity across crossings, occlusions, exits and cuts; left/right describes their initial location, never reassign identities when positions exchange. Report ambiguity and ask a concrete clarification before generating if assignments are unresolved. Analyze every selected target with the full frame and its own detail crop. Keep individual anatomy, clothing, shadows, contacts and source motion distinct; never fuse characters or transfer one person's actions to another. Preserve unselected people. Appearance references do not impose pose or destination background. Do not claim exact tracking from sparse observations."""

def native_prompt(p):
    from .painted import enabled
    if enabled(p):
        from .characters import assignments
        definitions=' '.join(f"<Subject {i}> is the source performer identified as {row['target']}; its new appearance is the corresponding edited person in <Picture 1>." for i,row in enumerate(assignments(p),1))
        return ('subject_definitions: <Video 1> supplies the exact performance and scene. <Picture 1> is the edited frame of THIS source window, containing the assigned replacements together. '+definitions+'\n'
          'summary: The assigned replacement characters perform the original actions in the original scene.\n'
          'retention_analysis: Each selected source identity and outfit changes to its matching edited identity. The full framing, actual source poses, timing, camera, props and all unselected people remain.\n'
          'detailed_description: The pinned edited frame defines appearance and composition at its actual local frame only. Each character follows their own source motion and changing apparent size before and after it; never freeze that pose. Source descriptions identify actors, not props to add. Only objects visible in this source window belong in the scene; never repeat another shot. Preserve padding.\n'
          'overall_soundscape: Preserve original source audio.\nnon_diegetic_music: Preserve source music; add none.')
    if p.get('additional_characters'):
        from .characters import assignments
        rows=assignments(p)
        definitions=' '.join(f"<Subject {i}> is the SOURCE performer described as {c['target']}; its replacement identity, anatomy and clothes come only from <Picture {i}>." for i,c in enumerate(rows,1))
        return ('subject_definitions: <Video 1> is the source scene and performance. '+definitions+'\n'
          'summary: The selected performers appear simultaneously as their individually assigned replacement characters.\n'
          'retention_analysis: Preserve each source performer motion, timing, path and interactions, the camera, setting, objects and all unselected people. Change only the selected identities and clothing.\n'
          'detailed_description: Keep every subject-reference assignment fixed across turns, crossings and occlusions. Never swap identities when screen positions exchange. Each appearance adapts to its own source pose, scale, contacts and scene lighting; never copy the reference stance or background. User direction: '+p['prompt']+'\n'
          'overall_soundscape: Preserve the source scene audio.\n'
          'non_diegetic_music: Preserve existing source music; add no new music.')
    return ("subject_definitions: <Video 1> is the source performance and scene. <Picture 1> defines the replacement character's identity, anatomy and clothing. "
      "The selected source performer is: "+p['target']+".\n"
      "summary: Replace that performer throughout the source video with the character in <Picture 1>.\n"
      "retention_analysis: Preserve the source motion, timing, camera, setting and other subjects. Remove the selected source identity and clothing. Preserve source occlusions and contacts.\n"
      "detailed_description: Follow <Video 1> for articulation, body orientation, trajectory, scale and support. Reference appearance must adapt to the actual moving pose, not freeze the reference pose. User direction: "+p['prompt']+"\n"
      "overall_soundscape: Preserve the source scene audio.\n"
      "non_diegetic_music: Preserve existing source music; add no new music.")
