"""fal.ai providers — all commercially licensed models:
  * FLUX.1 [schnell]  (Apache-2.0)           text -> image
  * BiRefNet v2       (MIT)                  background removal
  * TRELLIS.2         (MIT, Microsoft)       image -> textured GLB
"""
import time

from .. import config
from ..storage import fetch_url
from .base import ContentBlocked, ImageResult, MeshResult, ProviderError, Report


def _client():
    import fal_client

    if not config.FAL_KEY:
        raise ProviderError("The generation service is not configured (FAL_KEY missing).")
    return fal_client.SyncClient(key=config.FAL_KEY, default_timeout=600)


def _run(client, app: str, args: dict, report: Report, stage: str, p0: int, p1: int, budget_s: int = 600) -> dict:
    import fal_client

    handle = client.submit(app, args)
    start = time.time()
    while True:
        st = handle.status()
        if isinstance(st, fal_client.Completed):
            break
        if isinstance(st, fal_client.Queued):
            report(f"{stage} (queued)", p0)
        else:
            report(stage, int(p0 + (p1 - p0) * min(0.92, (time.time() - start) / 75)))
        if time.time() - start > budget_s:
            try:
                handle.cancel()
            finally:
                raise ProviderError("The generation service took too long. Your credits were refunded.")
        time.sleep(1.0)
    try:
        return handle.get()
    except Exception as exc:  # fal raises on validation/model errors
        raise ProviderError(f"Generation failed: {exc}") from exc


def _upload(client, data: bytes, content_type: str = "image/png") -> str:
    return client.upload(data, content_type, file_name="input." + content_type.split("/")[-1])


def generate_image(prompt: str, negative: str, seed: int | None, size: int, report: Report) -> ImageResult:
    client = _client()
    args = {
        "prompt": prompt,
        "image_size": {"width": size, "height": size},
        "num_inference_steps": 4,
        "num_images": 1,
        "enable_safety_checker": True,
        "output_format": "png",
    }
    if seed is not None:
        args["seed"] = seed
    out = _run(client, config.FAL_IMAGE_MODEL, args, report, "Generating image", 10, 90, budget_s=180)
    if any(out.get("has_nsfw_concepts") or []):
        raise ContentBlocked("That prompt produced content our safety filter blocks. Try a different idea.")
    img = (out.get("images") or [{}])[0]
    if not img.get("url"):
        raise ProviderError("The image service returned no image.")
    return ImageResult(data=fetch_url(img["url"]), ext="png", seed=out.get("seed"))


def remove_background(image: bytes, report: Report) -> bytes:
    client = _client()
    url = _upload(client, image)
    out = _run(client, config.FAL_BG_MODEL,
               {"image_url": url, "model": "General Use (Light)", "output_format": "png", "refine_foreground": True},
               report, "Removing background", 5, 15, budget_s=120)
    img = out.get("image") or {}
    if not img.get("url"):
        raise ProviderError("Background removal returned no image.")
    return fetch_url(img["url"])


def generate_mesh(image_png: bytes, opts: dict, report: Report) -> MeshResult:
    client = _client()
    url = _upload(client, image_png)
    args = {
        "image_url": url,
        "resolution": int(opts["resolution"]),
        "decimation_target": int(opts["decimation_target"]),
        "texture_size": int(opts["texture_size"]),
        "remesh": True,
    }
    if opts.get("seed") is not None:
        args["seed"] = int(opts["seed"])
    out = _run(client, config.FAL_MESH_MODEL, args, report, "Building 3D shape and materials", 18, 92)
    glb = (out.get("model_glb") or {}).get("url")
    if not glb:
        raise ProviderError("The 3D service returned no model.")
    report("Saving model", 95)
    return MeshResult(glb=fetch_url(glb), meta={"provider": "fal", "model": config.FAL_MESH_MODEL})
