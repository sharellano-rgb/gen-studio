"""Deterministic character assignment; no scene-specific identities or prompts."""
def compile_prompt(targets, reference_count, direction='', *, edit_policy=None, characters=None):
    if len(targets) != reference_count or not 1 <= reference_count <= 9:
        raise ValueError('Asigna un personaje a cada referencia seleccionada.')
    cleaned = [' '.join(t.split()) for t in targets]
    if any(not t or len(t)>180 or '<' in t or '>' in t for t in cleaned):
        raise ValueError('Describe a quién reemplaza cada imagen, sin etiquetas Picture o Video.')
    if edit_policy is not None:
        from .studio_editor import compile_policy_lines, CharacterBinding
        lines = ['Use <Video 1> as the source video. Edit every instance of the assigned source subjects throughout the clip, following the explicit edit settings.']
        for index, target in enumerate(cleaned, 1):
            lines.append(f'Source subject assignment: "{target}". Use <Picture {index}> for the assigned character identity, subject to the explicit edit settings below.')
        if characters:
            offset = 0
            for raw in characters:
                card = CharacterBinding.model_validate(raw)
                pictures = ' and '.join(f'<Picture {i}>' for i in range(offset+1, offset+len(card.reference_ids)+1))
                lines.append(f'The references {pictures} depict views of the SAME character identity. They do not add subjects to the source video.')
                offset += len(card.reference_ids)
            if offset != reference_count:
                raise ValueError('Las fichas no coinciden con las referencias.')
        lines += ['Preserve the source number of subjects, relative positions, individual motions, expressions, hand contacts, camera and foreground objects. Leave all unassigned subjects unchanged.',
                  'Multiple views in a reference sheet depict ONE identity. Do not display reference sheets or their backgrounds.',
                  'Follow the original timeline at the same speed from first to last frame. Do not loop, restart or repeat opening actions.']
        if direction.strip():lines.append('Additional scene direction: '+direction.strip())
        lines += compile_policy_lines(edit_policy)
        prompt = '\n'.join(lines)
        if len(prompt)>4000:raise ValueError('Acorta las descripciones o la dirección adicional.')
        return prompt
    groups = {}
    for index,target in enumerate(cleaned,1):
        groups.setdefault(target.casefold(), {'target':target,'pictures':[]})['pictures'].append(index)
    lines = [f'Replace ALL {len(groups)} assigned subjects in <Video 1> for the ENTIRE clip.']
    for group in groups.values():
        pictures=' and '.join(f'<Picture {i}>' for i in group['pictures'])
        lines.append(f"Source subject: {group['target']}. Replace this complete character with the identity, face, anatomy, clothing and accessories shown in {pictures}. Keep this identity assigned to this subject throughout.")
    lines += [
        'Different assigned subjects must retain distinct reference identities. Multiple views in a reference sheet depict ONE character, not extra people. Do not display the sheets or their backgrounds.',
        'Preserve the source number of subjects, their relative positions, individual motions, expressions, hand contacts, camera, background and objects. Leave all unassigned subjects unchanged.',
        'Follow the original timeline at the same speed from first to last frame. Preserve the order and timing of gestures. Do not loop, restart or repeat opening actions at the end.'
    ]
    if direction.strip():lines.append('Additional scene direction: '+direction.strip())
    prompt='\n'.join(lines)
    if len(prompt)>4000:raise ValueError('Acorta las descripciones o la dirección adicional.')
    return prompt
