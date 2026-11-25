"""Remaining-time ranges from measured, comparable jobs in one workspace."""
import json
import math
import re
from datetime import datetime, timezone

MIN_SAMPLES = 3
HISTORY_LIMIT = 200
_ACTIVE = {"preparing", "submitting", "running", "downloading", "refining"}


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def _configuration(job):
    """Work dimensions must match; random seeds and reference IDs may differ."""
    p = job.get("parameters")
    if not isinstance(p, dict) or p.get("engine") != "h3_direct" or p.get("operation") or p.get("hybrid_parent_id"):
        return None
    if any(key.startswith("long_variant_") for key in p):
        return None
    if p.get("preserve_face") or p.get("guide_face_reference"):
        return None
    preset, version, resolution = p.get("direct_preset"), p.get("direct_character_version"), p.get("resolution")
    if preset not in {"base", "turbo8", "realism", "hands"} or version not in {"v1", "v1.5"}:
        return None
    if not isinstance(resolution, str) or not re.fullmatch(r"[1-9][0-9]{1,3}x[1-9][0-9]{1,3}", resolution):
        return None
    if any(size > 4096 for size in map(int, resolution.split("x"))):
        return None
    frames, steps = p.get("frames"), p.get("steps")
    if type(frames) is not int or not 5 <= frames <= 360 or type(steps) is not int or not 1 <= steps <= 60:
        return None
    references, strength = p.get("direct_reference_ids"), p.get("lora_strength")
    if not isinstance(references, list) or not 1 <= len(references) <= 9:
        return None
    if any(not isinstance(value, str) or not value for value in references):
        return None
    if not _number(strength) or not 0 <= strength <= 2:
        return None
    vsa, crop, high_quality = p.get("direct_vsa", False), p.get("direct_crop_face", True), p.get("direct_high_quality", False)
    if any(type(value) is not bool for value in (vsa, crop, high_quality)):
        return None
    if vsa and (preset != "hands" or version != "v1.5" or steps != 8):
        return None
    reference_size, reference_type = p.get("ref_image_size"), p.get("direct_reference_type", "auto")
    if reference_size not in {"match", "max"} or reference_type not in {"auto", "human", "nonhuman"}:
        return None
    context, guide = p.get("long_context_frames", 0), p.get("direct_continuity_guide")
    if type(context) is not int or context not in (0, 1):
        return None
    if guide is not None and (not isinstance(guide, dict) or not isinstance(guide.get("version"), str)):
        return None
    if bool(context) != bool(guide):
        return None
    is_segment = bool(p.get("long_parent_id"))
    if is_segment and type(p.get("long_segment_index")) is not int:
        return None
    return (preset, version, resolution, frames, steps, len(references),
            strength, vsa, crop, high_quality, reference_size, reference_type,
            is_segment, context, guide.get("version") if guide else None)


def _timing(job):
    activity = job.get("activity")
    if not isinstance(activity, dict) or activity.get("version") != 1:
        return None
    if activity.get("complete_timing") is not True:
        return None
    if any(activity.get(flag) for flag in ("recovered", "cached_execution")):
        return None
    if job.get("cancel_requested") or job.get("assembly_only_retry"):
        return None
    elapsed = activity.get("processing_seconds")
    if not _number(elapsed) or elapsed < 0 or _timestamp(activity.get("started_at")) is None:
        return None
    return activity


def _active_child(store, parent):
    p = parent.get("parameters", {})
    pointer = parent.get("progress_source")
    if p.get("engine") != "h3_direct" or p.get("operation") != "long_character":
        return None
    if parent.get("status") not in _ACTIVE or not isinstance(pointer, dict):
        return None
    child_id = pointer.get("job_id")
    if not isinstance(child_id, str) or child_id == parent.get("id"):
        return None
    child = store.job(child_id)
    cp = child.get("parameters", {}) if isinstance(child, dict) else {}
    if (cp.get("engine") != "h3_direct" or cp.get("operation")
            or cp.get("long_parent_id") != parent.get("id")
            or type(cp.get("long_segment_index")) is not int
            or cp["long_segment_index"] < 0):
        return None
    return child


def estimate_for_job(store, job, now=None):
    """Return a remaining processing range, or None without enough evidence.

    Read only the given workspace Store and at most 200 recent completions.
    Legacy created_at/updated_at are never timing evidence. A parent's estimate
    describes its active segment only, never an extrapolation to the full video.
    """
    if job.get("parameters", {}).get("operation") == "long_character":
        job = _active_child(store, job)
        if job is None:
            return None
    config, activity = _configuration(job), _timing(job)
    if config is None or activity is None or job.get("status") not in _ACTIVE:
        return None
    if activity.get("phase") != "processing":
        return None
    clock = now or datetime.now(timezone.utc)
    if not isinstance(clock, datetime) or clock.tzinfo is None:
        return None
    current = clock.timestamp()
    phase_since, started = _timestamp(activity.get("phase_since")), _timestamp(activity["started_at"])
    if phase_since is None or phase_since < started or phase_since > current:
        return None
    elapsed = activity["processing_seconds"] + current - phase_since
    with store.connect() as db:
        rows = db.execute(
            "SELECT state FROM jobs WHERE json_extract(state, '$.status') = 'completed' "
            "ORDER BY rowid DESC LIMIT ?", (HISTORY_LIMIT,)
        ).fetchall()
    durations, seen = [], set()
    for row in rows:
        try:
            sample = json.loads(row[0])
        except (TypeError, ValueError):
            continue
        if not isinstance(sample, dict):
            continue
        sample_id = sample.get("id")
        if not isinstance(sample_id, str) or sample_id == job.get("id") or sample_id in seen:
            continue
        if sample.get("status") != "completed" or sample.get("result_available") is not True or sample.get("error"):
            continue
        timing = _timing(sample)
        if _configuration(sample) != config or timing is None:
            continue
        if timing.get("phase") != "finished" or timing.get("sample_observed") is not True:
            continue
        start, finish = _timestamp(timing["started_at"]), _timestamp(timing.get("finished_at"))
        duration = timing["processing_seconds"]
        if finish is None or not start < finish <= current or not 0 < duration <= finish - start + 1:
            continue
        seen.add(sample_id)
        durations.append(duration)
    if len(durations) < MIN_SAMPLES:
        return None
    # Allow variation in model loading and preparation; do not promise a deadline.
    lower, upper = max(0, min(durations) * 0.8 - elapsed), max(durations) * 1.2 - elapsed
    if upper <= 0:
        return None
    return {"scope": "segment" if config[12] else "job",
            "lower_seconds": int(math.floor(lower / 5) * 5),
            "upper_seconds": int(math.ceil(upper / 5) * 5),
            "sample_count": len(durations)}
