"""Conservative continuity checks, independent of detector/runtime libraries."""
import math


def matches_source_identity(seed_score, recent_score, missed):
    # Turning a small face can weaken the match to frame zero while adjacent
    # frames remain consistent. The relaxed anchor needs a strong recent match,
    # a short gap, and the caller's mask + spatial continuity checks.
    return seed_score >= 0.30 or (recent_score is not None and missed <= 2 and seed_score >= 0.20 and recent_score >= 0.55)


def compatible_box(previous, current, width, height, missed=0):
    def center(box):
        return ((box[0]+box[2])/2, (box[1]+box[3])/2)
    def area(box):
        return max(0, box[2]-box[0])*max(0, box[3]-box[1])
    a, b = center(previous), center(current)
    distance = math.hypot((a[0]-b[0])/width, (a[1]-b[1])/height)
    ratio = area(current)/max(1, area(previous))
    return distance <= min(0.15, 0.06+missed*0.015) and 0.4 <= ratio <= 2.5


def unique_continuous_face(boxes, previous, width, height, missed=0):
    """Seed from one face; accept multiple detections only with one continuous match.

    Never rank candidates by confidence or choose the closest of two plausible
    heads. Ambiguous samples retain the caller's existing short-gap limit.
    """
    if previous is None:
        return boxes[0] if len(boxes) == 1 else None
    candidates = [box for box in boxes if compatible_box(previous, box, width, height, missed)]
    return candidates[0] if len(candidates) == 1 else None


class SubjectGuard:
    def __init__(self):
        self.box = None
        self.missed = 0
        self.stopped = False

    def accept(self, candidates, width, height, scene_cut=False):
        if scene_cut:
            self.stopped = True
        if self.stopped:
            return None, 'scene_cut'
        # Caller passes only source-identity matches inside the SAM3 subject mask.
        if len(candidates) != 1:
            self.missed += 1
            return None, 'ambiguous' if candidates else 'not_detected'
        candidate = candidates[0]
        if self.box is not None and not compatible_box(self.box, candidate['box'], width, height, self.missed):
            self.missed += 1
            return None, 'discontinuity'
        self.box = candidate['box']
        self.missed = 0
        return candidate, None
