"""Default rhythm for newly proposed dialogue and newly prepared native shots."""
POLICY = 'parody-short-pauses-1'
SHORT_PAUSES = (
    'Default to brisk conversational pacing. Start speaking promptly and aim for about '
    '100-250 ms of silence between replies, counting the tail of one shot and the lead-in '
    'of the next together. Let a gesture or reaction happen under the dialogue when natural. '
    'Keep breaths, intelligible diction and the distinctive timing of a catchphrase. '
    'Use a longer beat only when the user requests it or a specific comic payoff needs it.'
)


def native_pacing(plan):
    if plan['speech'].get('pacing_policy') != POLICY:
        return ''
    line = plan['voice']['line']
    return (SHORT_PAUSES + ' Respect the approved dialogue, vocal phrasing and any explicitly '
            f"set pauses (before: {line.get('pause_before_ms', 0)} ms; after: {line.get('pause_after_ms', 0)} ms). "
            'Do not add a silent lead-in, prolonged stare or extra hold to fill the shot. '
            'Deliver the exact words once at a natural pace.')
