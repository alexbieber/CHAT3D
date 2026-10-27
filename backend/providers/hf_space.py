"""DEVELOPMENT ONLY: the public microsoft/TRELLIS.2 Space.

Not for paying customers: it has a small free GPU quota, a public queue, and
its built-in background removal (BRIA RMBG-2.0) is licensed CC BY-NC 4.0 —
non-commercial. Use MESH_PROVIDER=fal in production.
"""
import tempfile
from pathlib import Path

from .. import config
from .base import MeshResult, ProviderError, Report

DEFAULTS = (7.5, 0.7, 12, 5.0, 7.5, 0.5, 12, 3.0, 1.0, 0.0, 12, 3.0)


def generate_mesh(image_png: bytes, opts: dict, report: Report) -> MeshResult:
    if config.IS_PROD:
        raise ProviderError("hf_space is development-only and disabled in production.")
    from gradio_client import Client, handle_file

    report("Connecting to the TRELLIS.2 Space", 10)
    client = Client(config.HF_TRELLIS_SPACE, token=config.HF_TOKEN, verbose=False)
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "in.png"
        src.write_bytes(image_png)
        pre = client.predict(handle_file(str(src)), api_name="/preprocess_image")
        report("Building 3D (public Space)", 30)
        client.predict(handle_file(pre), int(opts.get("seed") or 0), str(opts["resolution"]), *DEFAULTS,
                       api_name="/image_to_3d")
        report("Baking GLB", 75)
        res = client.predict(int(opts["decimation_target"]), int(opts["texture_size"]), api_name="/extract_glb")
        path = res[0] if isinstance(res, (list, tuple)) else res
        return MeshResult(glb=Path(path).read_bytes(), meta={"provider": "hf_space"})
