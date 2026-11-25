"""Full-canvas scene editing with a separately tracked, pixel-protected character.

This is an independent pilot. It reuses installed nodes without enabling the
retired Ninja engine. Coordinates passed to track_graph are normalized to the
prepared canvas (the API projects them through source fitting/padding).
"""
from collections.abc import Mapping
from pathlib import PurePosixPath
import math
import re

from . import ninja_graphs as ninja

VERSION = "motion-layers-v1"
FPS = 24
MAX_FRAMES = 240
MAX_PIXELS = 2097152


def render_frame_count(frames):
    if type(frames) is not int or not 5 <= frames <= MAX_FRAMES:
        raise ValueError(f"Motion Layers admite entre 5 y {MAX_FRAMES} fotogramas.")
    return 5 + 17 * math.ceil((frames - 5) / 17)


def _validate(p, job_id):
    if not isinstance(p, Mapping):
        raise ValueError("Parámetros de Motion Layers no válidos.")
    count = render_frame_count(p.get("frames"))
    if type(p.get("render_frames")) is not int or p["render_frames"] != count:
        raise ValueError("La duración no coincide con la cuadrícula H3.")
    for key in ("width", "height"):
        if type(p.get(key)) is not int or not 32 <= p[key] <= 2048 or p[key] % 32:
            raise ValueError("La resolución debe ser múltiplo de 32.")
    if p["width"] * p["height"] > MAX_PIXELS:
        raise ValueError("La resolución supera el límite del piloto.")
    for key in ("x", "y"):
        value = p.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("Marca a la persona que se debe conservar.")
    if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", job_id):
        raise ValueError("Identificador del trabajo no válido.")
    return count


def track_graph(p, video, job_id):
    """Track all persons, then uniquely select the person containing the point.

    save-mask exports the existing ninja-mask.json report and NPZ descriptor;
    the CPU layers_mask step must validate every frame before rendering.
    """
    _validate(p, job_id)
    return ninja.track_graph({**p, "mode": "body", "recipe": "w2"}, video, job_id)


def scene_graph(p, video, reference, environment, job_id, bundle):
    """Generate only the environment, then restore protected source pixels.

    bundle.path is a relative Comfy input path under gen_studio/. The CPU
    validated NPZ contains float32 mask[N,H,W], white = protected character.
    Picture 1 is identity; optional Picture 2 is scene; Video 1 is stage one.
    """
    count = _validate(p, job_id)
    if not isinstance(bundle, Mapping) or not re.fullmatch(r"[0-9a-fA-F]{64}", str(bundle.get("sha256", ""))):
        raise ValueError("Se requiere la máscara validada y su SHA-256.")
    path = ninja._relative(bundle.get("path"), "bundle").replace(chr(92), "/")
    parts = PurePosixPath(path).parts
    if not parts or parts[0] != "gen_studio" or PurePosixPath(path).suffix.lower() != ".npz":
        raise ValueError("La máscara debe estar dentro de gen_studio en las entradas del motor.")
    if any(type(bundle.get(k)) is not int or bundle[k] != v for k, v in
           (("frames", count), ("width", p["width"]), ("height", p["height"]))):
        raise ValueError("La máscara no corresponde al vídeo preparado.")
    seed = p.get("seed")
    if type(seed) is not int or not 0 <= seed <= 9007199254740991:
        raise ValueError("Semilla no válida.")
    prompt = p.get("scene_prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("Se requiere la descripción de escena validada en inglés.")
    node = ninja._node
    graph = ninja._source(p, video, count)
    graph.update({
        "audio": node("GenNinjaAudio", frames=count, audio=["components", 1]),
        "load-mask": node("GenNinjaLoadMask", bundle=path, expected=count, sha256=bundle["sha256"].lower()),
        "model": node("UNETLoader", unet_name=ninja.BASE, weight_dtype="default"),
        "video-vae": node("VAELoader", vae_name=ninja.VIDEO_VAE),
        "audio-vae": node("VAELoader", vae_name=ninja.AUDIO_VAE),
        "clip": node("CLIPLoader", clip_name=ninja.ENCODER, type="minimax", device="default"),
        "reference": node("LoadImage", image=ninja._relative(reference, "reference")),
        "turbo": node("LoraLoaderModelOnly", model=["model", 0], lora_name=ninja.TURBO_W2, strength_model=.5),
        "shift": node("MiniMaxH3SigmaShift", model=["turbo", 0], shift_video=12., shift_audio=1.5),
        "condition": node("MiniMaxH3ReferenceToVideo", clip=["clip", 0], vae=["video-vae", 0],
                          audio_vae=["audio-vae", 0], prompt=prompt, width=p["width"], height=p["height"],
                          length=count, ref_image_size="max", **{
                              "ref_images.ref_image_0": ["reference", 0],
                              "ref_videos.ref_video_0": ["source-exact", 0]}),
        # Invert protection for the latent edit mask. Do not fill arm/leg holes,
        # expand across time, or blur a face. Token pooling alone is not a
        # preservation guarantee, so the final pixel composite is mandatory.
        "mask-ops": node("NKDMaskOps", mask=["load-mask", 0], vae=["video-vae", 0], model=["turbo", 0],
                         invert=True, black_point=0., white_point=1., despeckle=0, fill_holes=False,
                         close_gaps=0, temporal_expand=0, temporal_smooth=0, expand=0, blockify=0,
                         blockify_threshold=.05, feather=0, edge_low=0., edge_high=1.),
        "av-latent": node("NKDAVLatent", images=["source-exact", 0], audio=["audio", 0],
                          video_vae=["video-vae", 0], audio_vae=["audio-vae", 0],
                          latent_mask=["mask-ops", 2], audio_mode="keep", fps=FPS,
                          ramp_ticks=0, ramp_out_ticks=0, ramp_shape="cosine"),
        "guider": node("BasicGuider", model=["shift", 0], conditioning=["condition", 0]),
        "noise": node("RandomNoise", noise_seed=seed),
        "sampler": node("KSamplerSelect", sampler_name="sa_solver_pece"),
        "sigmas": node("NKDSigmasCurve", curve_data=ninja.SIGMA_CURVE, steps=8, max_sigma=1.),
        "sample": node("SamplerCustomAdvanced", noise=["noise", 0], guider=["guider", 0],
                       sampler=["sampler", 0], sigmas=["sigmas", 0], latent_image=["av-latent", 0]),
        "decode": node("VAEDecode", samples=["sample", 0], vae=["video-vae", 0]),
        "decode-exact": node("GenNinjaExact", images=["decode", 0], expected=count, width=p["width"], height=p["height"]),
        "composite": node("ImageCompositeMasked", destination=["decode-exact", 0], source=["source-exact", 0],
                          x=0, y=0, resize_source=False, mask=["load-mask", 0]),
        "output-exact": node("GenNinjaExact", images=["composite", 0], expected=count, width=p["width"], height=p["height"]),
    })
    if count > 124:
        # Same installed FFN memory optimization already used by the identity
        # pass. Chunk independent token rows, never time or the rendered shot.
        graph["chunk-ffn"] = node("MiniMaxChunkFeedForward", model=["turbo", 0], chunks=2, seq_threshold=4096)
        graph["shift"]["inputs"]["model"] = ["chunk-ffn", 0]
    if environment is not None:
        graph["environment"] = node("LoadImage", image=ninja._relative(environment, "environment"))
        graph["condition"]["inputs"]["ref_images.ref_image_1"] = ["environment", 0]
    ninja._save_video(graph, ["output-exact", 0], ["audio", 0], job_id, "scene", "save")
    return graph
