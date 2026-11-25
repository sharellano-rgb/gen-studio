"""Opt-in CPU compositor. No imports from or changes to the live video pipeline.

Masks are boolean arrays in the ORIGINAL decoded frame coordinates.
Authorization comes from the caller, never from automatic mask dilation.
This module proves pixel preservation, not segmentation or identity accuracy.
"""
import numpy as np

VERSION = "protected-character-compositor-1"

class CompositionError(ValueError):
    pass

def _image(value, shape=None):
    a = np.asarray(value)
    if a.dtype != np.uint8 or a.ndim != 3 or a.shape[2] != 3:
        raise CompositionError("Expected uint8 RGB image")
    if shape is not None and a.shape != shape:
        raise CompositionError("Canvas mismatch: explicit coordinate mapping required")
    return a

def _mask(value, shape, name):
    a = np.asarray(value)
    if a.dtype != np.bool_ or a.shape != shape:
        raise CompositionError(name + " must be a boolean mask at original resolution")
    return a

def verify(original, result, authorized):
    original = _image(original)
    result = _image(result, original.shape)
    authorized = _mask(authorized, original.shape[:2], "authorized")
    outside = ~authorized
    if not outside.any():
        raise CompositionError("No protected pixels: cannot claim protected background")
    delta = np.abs(result[outside].astype(np.int16) - original[outside].astype(np.int16))
    changed = np.any(delta != 0, axis=1)
    report = {
        "protected_pixels": int(outside.sum()),
        "protected_changed_pixels": int(changed.sum()),
        "protected_changed_fraction": float(changed.mean()),
        "protected_max_difference": int(delta.max()),
    }
    if report["protected_max_difference"]:
        raise CompositionError("Protected original pixels changed")
    return report

def compose(original, generated, source_mask, generated_mask, editable,
            reconstruction_region, *, alpha=None, reconstruction=None,
            protected_objects=None):
    original = _image(original)
    generated = _image(generated, original.shape)
    size = original.shape[:2]
    source_mask = _mask(source_mask, size, "source_mask")
    generated_mask = _mask(generated_mask, size, "generated_mask")
    editable = _mask(editable, size, "editable")
    reconstruction_region = _mask(reconstruction_region, size, "reconstruction_region")
    if not source_mask.any() or not generated_mask.any():
        raise CompositionError("Empty subject mask: require explicit absence handling")
    if np.any(editable & reconstruction_region):
        raise CompositionError("Editable and reconstruction regions must be disjoint")
    authorized = editable | reconstruction_region
    protected_objects = (np.zeros(size, bool) if protected_objects is None
                         else _mask(protected_objects, size, "protected_objects"))
    if np.any(authorized & protected_objects):
        raise CompositionError("Authorized region collides with a protected object/person")
    if np.any(generated_mask & ~editable):
        raise CompositionError("Generated silhouette exceeds authorized editable region")
    uncovered = source_mask & ~generated_mask
    if np.any(uncovered & ~reconstruction_region):
        raise CompositionError("Exposed original subject requires authorized reconstruction")
    if np.any(source_mask & ~authorized):
        raise CompositionError("Original subject remains outside authorized regions")
    if alpha is None:
        alpha = generated_mask.astype(np.float64)
    else:
        alpha = np.asarray(alpha)
        if alpha.shape != size or alpha.dtype.kind != "f" or not np.isfinite(alpha).all():
            raise CompositionError("Alpha must be a finite float mask")
        if np.any((alpha < 0) | (alpha > 1)):
            raise CompositionError("Alpha outside [0,1]")
    if np.any((alpha > 0) & ~editable):
        raise CompositionError("Alpha/feather extends outside authorized editable region")
    if np.any(generated_mask & (alpha <= 0)):
        raise CompositionError("Generated silhouette has missing alpha")
    if reconstruction_region.any() and reconstruction is None:
        raise CompositionError("Reconstruction pixels require an explicit synthesized image")
    result = original.copy()
    if reconstruction is not None:
        recon = _image(reconstruction, original.shape)
        result[reconstruction_region] = recon[reconstruction_region]
    active = alpha > 0
    a = alpha[active, None]
    result[active] = np.rint(
        a * generated[active].astype(np.float64)
        + (1 - a) * original[active].astype(np.float64)
    ).astype(np.uint8)
    report = verify(original, result, authorized)
    report.update({
        "version": VERSION, "editable_pixels": int(editable.sum()),
        "reconstruction_pixels": int(reconstruction_region.sum()),
        "uncovered_original_subject_pixels": int(uncovered.sum()),
        "authorized_fraction": float(authorized.mean()),
        "reconstruction_is_original_truth": False,
        "pixel_equality_scope": "decoded original RGB, before lossy encoding",
        "semantic_quality_verified": False,
    })
    return result, report
