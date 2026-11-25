"""Pure CPU evidence for a separately versioned, opt-in Qwen visual policy.

No inference, storage, image processing or legacy-rule modification happens here.
The caller owns policy selection and must bind VERSION to the job/cache record.
Prepared source videos use 24 fps. Original-video offsets must be supplied by the
caller, including any hidden sequence context; reset child offsets are not used.
"""
from collections.abc import Mapping
import math

VERSION = 'qwen-motion-grounding-1'
ANALYSIS_FPS = 24

DIRECTOR_RULES_SUFFIX = '''
Compare independently observed full source frames in chronological order. A pose,
lifted limb or contact visible at one instant is not evidence that it is sustained
throughout the clip. Distinguish sampled instants from actions supported across
multiple instants; leave unseen transitions uncertain. Describe observed changes
in image position, apparent size, orientation, contacts, occlusions, framing and
scene across samples instead of extending the opening observation over the clip.
In motion_notes, when the measurements show clear changes, state their times,
screen-space direction and increases or decreases in apparent size. Do not
summarize those changes as stationary motion or subtle weight shifts. State only
the measured screen-space change, not an unsupported physical-depth explanation.
The original source video remains the authority for motion and temporal changes;
supporting descriptions must not replace its action with an invented static hold.
For Object Swap with the source background retained, account for the original
scene and framing as they change, including gradual changes and visible cuts;
do not declare a cut from a bounding box or changed wording alone. A detail crop
is the same instant as its full frame, not camera movement or a later action.
Bounding boxes are uncertain image-space estimates: their size changes alone do
not establish physical depth, speed or the cause of motion. Camera motion, pose
and occlusion can also change them. Use only observed evidence; do not invent
positions in missing or ambiguous samples or promise exact reconstruction.
Opening/initial-pose directions describe only local time zero of the current
source window. At a sequence join, use the current window's first source frame;
do not substitute a previous window's pose or apply this initial pose throughout.
'''.strip()

POSE_RULES_SUFFIX = '''
Describe the selected source subject's geometry, orientation, limb positions,
contacts and visible occlusions at this sampled instant. Use "source subject".
Do not repeat its identity, color, fur pattern, markings or clothing in
pose or motion directions: replacement appearance comes from the assigned
reference. Do not extend one sampled pose into a sustained action. Continue to
describe the full original environment and framing in their designated fields.
For an initial/opening pose, describe only local time zero of the current source
window, using its first source frame rather than a previous window's pose.
Later poses belong to their own timed motion observations.
'''.strip()

_USABLE_REASONS = {
    'small_subject', 'subject_already_large',
    'existing_opening_detail_with_full_context',
}
_BAD_REASONS = {'invalid_location', 'invalid_bbox'}


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _box(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if any(type(n) is not int for n in value):
        return None
    x0, y0, x1, y1 = value
    return list(value) if 0 <= x0 < x1 <= 1000 and 0 <= y0 < y1 <= 1000 else None


def _time(row, original_start):
    frame = row.get('frame')
    seconds = row.get('seconds')
    frame_ok = type(frame) is int and frame >= 0
    seconds_ok = _number(seconds)
    invalid = ('frame' in row and not frame_ok) or ('seconds' in row and not seconds_ok)
    if frame_ok and seconds_ok and not math.isclose(seconds, frame / ANALYSIS_FPS, abs_tol=1e-6, rel_tol=0):
        invalid = True
    local = None if invalid else (float(seconds) if seconds_ok else frame / ANALYSIS_FPS if frame_ok else None)
    return {'frame': frame if frame_ok else None, 'local_seconds': local,
            'original_seconds': original_start + local if original_start is not None and local is not None else None,
            'timing_status': 'invalid' if invalid else 'observed' if local is not None else 'missing'}


def _location(row):
    reason = row.get('reason')
    reason = reason if isinstance(reason, str) else None
    if row.get('ambiguous') is True or reason == 'ambiguous_subject':
        return 'ambiguous', 'ambiguous_subject', None
    if row.get('found') is False or reason == 'subject_not_found':
        return 'absent', 'subject_not_found', None
    if reason in _BAD_REASONS:
        return 'invalid', reason, None
    if ('found' in row and row['found'] is not True) or ('ambiguous' in row and row['ambiguous'] is not False):
        return 'invalid', 'invalid_location', None
    bbox = _box(row.get('bbox'))
    if bbox is None:
        return 'invalid', 'invalid_bbox', None
    if reason in _USABLE_REASONS or (row.get('found') is True and row.get('ambiguous') is False):
        return 'observed', reason if reason in _USABLE_REASONS else 'explicit_location', bbox
    return 'unverified', 'unverified_location', None


def _text(value):
    # Scene text is evidence for Qwen, never copied into the generation prompt.
    return value.strip()[:1200] if isinstance(value, str) and value.strip() else None


def evidence(analysis, p, *, source_start_seconds=None):
    """Return image-space samples and per-frame scene evidence, without inference.

    ``source_start_seconds`` is the confirmed original-video time of local frame
    zero, including hidden context. If omitted, original times remain unknown;
    neither analysis.source_start_seconds nor child sequence offsets are guessed.
    ``p`` supplies frames/sequence_input_keep to exclude padded frames when known.
    A valid bbox is still an estimate, not a calibrated confidence or depth cue.
    Scene text is joined only by matching frame AND time, never carried forward.
    """
    if not isinstance(analysis, Mapping) or not isinstance(p, Mapping):
        raise TypeError('analysis and p must be mappings.')
    if source_start_seconds is not None and not _number(source_start_seconds):
        raise ValueError('source_start_seconds must be a confirmed finite, nonnegative time.')
    original_start = float(source_start_seconds) if source_start_seconds is not None else None
    limits = [p[k] for k in ('frames', 'sequence_input_keep') if type(p.get(k)) is int and p[k] > 0]
    keep = min(limits) if limits else None
    raw_scenes = analysis.get('scene_trace')
    scenes = []
    for index, raw in enumerate(raw_scenes if isinstance(raw_scenes, (list, tuple)) else []):
        row = raw if isinstance(raw, Mapping) else {}
        scenes.append({'trace_index': index, **_time(row, original_start),
                       'source_environment': _text(row.get('source_environment')),
                       'framing': _text(row.get('framing'))})
    raw_trace = analysis.get('zoom_trace')
    samples = []
    for index, raw in enumerate(raw_trace if isinstance(raw_trace, (list, tuple)) else []):
        row = raw if isinstance(raw, Mapping) else {}
        timing = _time(row, original_start)
        status, reason, bbox = _location(row)
        if timing['timing_status'] != 'observed':
            bbox = None
        outside = keep is not None and timing['local_seconds'] is not None and timing['local_seconds'] >= keep / ANALYSIS_FPS
        if outside:
            status, reason, bbox = 'outside_source_interval', 'padded_or_outside_source', None
        usable = bbox is not None
        matches = [s for s in scenes if timing['frame'] is not None and s['frame'] == timing['frame']]
        scene = None
        scene_status = 'missing'
        if matches:
            scene_status = 'ambiguous' if len(matches) != 1 else 'invalid_time'
            candidate = matches[0]
            if len(matches) == 1 and timing['local_seconds'] is not None and candidate['timing_status'] == 'observed' and math.isclose(candidate['local_seconds'], timing['local_seconds'], abs_tol=1e-6, rel_tol=0):
                scene = {'source_environment': candidate['source_environment'], 'framing': candidate['framing']}
                scene_status = 'observed' if any(scene.values()) else 'missing'
        samples.append({'trace_index': index, **timing, 'location_status': status,
                        'reliability': 'usable_estimate' if usable else 'unavailable', 'reason': reason,
                        'cropped': row.get('cropped') if type(row.get('cropped')) is bool else None,
                        'bbox_1000': bbox,
                        'center_1000': [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2] if usable else None,
                        'width_1000': bbox[2] - bbox[0] if usable else None,
                        'height_1000': bbox[3] - bbox[1] if usable else None,
                        'relative_scale_to_start': None, 'scene_status': scene_status, 'scene': scene})
    samples.sort(key=lambda s: (s['local_seconds'] is None, s['local_seconds'] or 0, s['trace_index']))
    starts = [s for s in samples if s['local_seconds'] == 0 and s['reliability'] == 'usable_estimate']
    baseline = starts[0] if len(starts) == 1 else None
    if baseline:
        for sample in samples:
            if sample['reliability'] == 'usable_estimate':
                width = sample['width_1000'] / baseline['width_1000']
                height = sample['height_1000'] / baseline['height_1000']
                sample['relative_scale_to_start'] = {'width_ratio': width, 'height_ratio': height, 'area_ratio': width * height}
    return {'version': VERSION, 'coordinates': 'original full source frame; normalized 0..1000',
            'analysis_fps': ANALYSIS_FPS, 'source_start_seconds': original_start,
            'usable_source_frames': keep, 'baseline_local_seconds': 0 if baseline else None,
            'samples': samples, 'scene_trace': scenes}


def prompt(data):
    """Concise generation guidance from numeric evidence, with no source identity.

    Free text from the source analysis/scene descriptions is intentionally not
    echoed here. Pass structured evidence separately when asking Qwen to compare
    environment and framing; a missing scene observation never means static.
    """
    if data.get('version') != VERSION:
        raise ValueError('Unsupported motion-evidence policy.')
    lines = ['Full-source-frame temporal samples (estimated image coordinates; local clip times):']
    for sample in data['samples']:
        local = sample['local_seconds']
        stamp = f'{local:.3f}s' if local is not None else 'unknown time'
        if sample['original_seconds'] is not None:
            stamp += f" (original {sample['original_seconds']:.3f}s)"
        if sample['reliability'] != 'usable_estimate':
            lines.append(f"{stamp}: location unavailable ({sample['location_status']}; {sample['timing_status']} time); do not fill in a position.")
            continue
        x, y = sample['center_1000']
        line = f"{stamp}: center=({x / 10:.1f}%, {y / 10:.1f}%), width={sample['width_1000'] / 10:.1f}%, height={sample['height_1000'] / 10:.1f}% of the full frame"
        relative = sample['relative_scale_to_start']
        if relative:
            line += f"; apparent width/height vs start={relative['width_ratio']:.2f}x/{relative['height_ratio']:.2f}x"
        line += '; scene observation ' + ('available at this instant' if sample['scene_status'] == 'observed' else 'unavailable at this instant') + '.'
        lines.append(line)
    if not data['samples']:
        lines.append('No usable temporal localization evidence was recorded.')
    if data['baseline_local_seconds'] is None:
        lines.append('No unambiguous initial-size baseline; relative scale is unknown.')
    lines.append('Follow the original source video for action and temporal changes; these sparse observations do not request a fixed pose or size. Compare full-frame scene observations at their own instants; never carry the first scene description through the whole clip. Image position and apparent size may change with camera/framing, pose or occlusion: do not infer physical depth or speed from boxes, or interpret a detail crop as camera motion. Missing or ambiguous evidence is unknown, not stationary.')
    return '\n'.join(lines)
