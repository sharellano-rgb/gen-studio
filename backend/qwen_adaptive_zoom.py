"""Optional same-frame detail view for Qwen's existing snapshot analysis.

This helper does not select analysis policies, change snapshot instructions,
store records, or call inference services. Callers opt new jobs into VERSION and
bind ``crop`` to the exact source frame represented by ``full_image``.
"""
from collections.abc import Awaitable, Callable, Mapping
import math
from typing import Any

VERSION = 'qwen-adaptive-zoom-1'


def _field(location: Any, name: str) -> Any:
    return location.get(name) if isinstance(location, Mapping) else getattr(location, name, None)


def _bbox(value: Any) -> list[int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if any(type(number) is not int for number in value):
        return None
    x0, y0, x1, y1 = value
    if not (0 <= x0 < x1 <= 1000 and 0 <= y0 < y1 <= 1000):
        return None
    return [x0, y0, x1, y1]


async def snapshot(
    *,
    target: Any,
    seconds: int | float,
    full_image: str,
    locate: Callable[[str, Any], Awaitable[Any]],
    crop: Callable[[list[int]], Awaitable[str]],
    describe: Callable[[list[str], dict[str, Any]], Awaitable[Any]],
) -> tuple[Any, dict[str, Any]]:
    """Return the existing Snapshot result and a small, image-free audit trace.

    ``locate`` returns the existing Location model (or equivalent mapping).
    ``crop`` receives its original-image bbox in normalized 0..1000 coordinates;
    its caller-owned closure must use this exact timestamp/source frame.
    ``describe`` retains the caller's original Snapshot schema and instructions.
    A malformed/ambiguous location falls back to the full image, never a guessed
    crop. Callback errors propagate so cancellation and retries stay with callers.
    """
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
        raise ValueError('Snapshot time must be a finite, nonnegative number.')
    if not isinstance(full_image, str) or not full_image:
        raise ValueError('A full source-frame image is required.')

    location = await locate(full_image, target)
    bbox = None
    cropped = False
    images = [full_image]
    views = [{
        'image_index': 1,
        'role': 'full original source frame: scene context and subject placement',
        'seconds': seconds,
    }]
    context: dict[str, Any] = {
        'target': target,
        'seconds_from_start': seconds,
        'views': views,
    }

    found = _field(location, 'found')
    ambiguous = _field(location, 'ambiguous')
    if ambiguous is True:
        reason = 'ambiguous_subject'
    elif found is False:
        reason = 'subject_not_found'
    elif found is not True or ambiguous is not False:
        reason = 'invalid_location'
    else:
        bbox = _bbox(_field(location, 'bbox'))
        if bbox is None:
            reason = 'invalid_bbox'
        else:
            width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
            small = height <= 450 or width * height <= 200_000 or (width <= 250 and height < 750)
            reason = 'small_subject' if small else 'subject_already_large'
            if small:
                detail = await crop(bbox.copy())
                if not isinstance(detail, str) or not detail:
                    raise ValueError('The same-frame crop did not return an image.')
                images.append(detail)
                cropped = True
                views.append({
                    'image_index': 2,
                    'role': 'enlarged detail of the same subject in image 1, at the same instant',
                    'seconds': seconds,
                    'subject_bbox': bbox.copy(),
                    'coordinates': 'original full source frame, normalized 0..1000; caller may pad this bbox',
                })
                context['view_relationship'] = (
                    'Image 1 is the full source frame. Image 2 is a detail crop of that same frame '
                    'for examining visible anatomy and contacts. It is not another camera view '
                    'or another moment. Scene and image positions are referenced to image 1.'
                )

    result = await describe(images, context)
    trace = {
        'version': VERSION,
        'seconds': seconds,
        'bbox': bbox,
        'cropped': cropped,
        'reason': reason,
    }
    return result, trace
