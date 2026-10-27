"""Offline providers for development and tests: no network, no cost."""
import io
import random
import time

from .. import config
from .base import ContentBlocked, ImageResult, MeshResult, ProviderError, Report


def _sleep(report: Report, stage: str, p: int):
    report(stage + " (test mode)", p)
    time.sleep(config.MOCK_DELAY)


def generate_image(prompt: str, negative: str, seed: int | None, size: int, report: Report) -> ImageResult:
    from PIL import Image, ImageDraw

    if "mock-fail" in prompt:
        raise ProviderError("Simulated provider failure.")
    if "mock-nsfw" in prompt:
        raise ContentBlocked("That prompt produced content our safety filter blocks. Try a different idea.")
    seed = seed if seed is not None else random.randint(0, 2**31 - 1)
    _sleep(report, "Generating image", 50)
    rng = random.Random(seed)
    s = 512
    img = Image.new("RGB", (s, s), "white")
    d = ImageDraw.Draw(img)
    hue = tuple(rng.randint(40, 220) for _ in range(3))
    d.ellipse([s * .25, s * .32, s * .75, s * .82], fill=hue)
    d.rounded_rectangle([s * .42, s * .14, s * .58, s * .36], radius=12, fill=tuple(c // 2 for c in hue))
    d.text((14, 14), f"TEST IMAGE seed={seed}", fill="black")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return ImageResult(data=buf.getvalue(), ext="png", seed=seed)


def remove_background(image: bytes, report: Report) -> bytes:
    _sleep(report, "Removing background", 12)
    return image


def generate_mesh(image_png: bytes, opts: dict, report: Report) -> MeshResult:
    import numpy as np
    import trimesh

    if opts.get("seed") == 666:
        raise ProviderError("Simulated 3D failure.")
    for stage, p in [("Building 3D structure", 30), ("Shaping geometry", 55), ("Baking materials", 80)]:
        _sleep(report, stage, p)
    rng = np.random.default_rng(opts.get("seed") or 0)
    body = trimesh.creation.icosphere(subdivisions=3, radius=0.35)
    head = trimesh.creation.cylinder(radius=0.12, height=0.3)
    head.apply_translation([0, 0, 0.45])
    mesh = trimesh.util.concatenate([body, head])
    color = np.append(rng.integers(60, 230, 3), 255).astype(np.uint8)
    mesh.visual = trimesh.visual.ColorVisuals(mesh, vertex_colors=np.tile(color, (len(mesh.vertices), 1)))
    mesh.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))
    return MeshResult(glb=mesh.export(file_type="glb"), meta={"provider": "mock"})
