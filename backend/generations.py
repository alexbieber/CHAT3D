"""Creating, running and presenting generations.

Credits are charged up front when a generation is created (so a user can never
spend more than they own) and refunded automatically if it fails.
"""
import io
import logging
import random
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException
from PIL import Image
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from . import config, credits
from .db import Generation, SessionLocal, User, now
from .prompting import NEGATIVE, build_prompt
from .providers import bg_provider, image_provider, mesh_provider
from .providers.base import ProviderError
from .storage import new_key, storage

log = logging.getLogger("chat3d.worker")
_pool = ThreadPoolExecutor(max_workers=config.WORKER_THREADS, thread_name_prefix="gen")
RESOLUTIONS = ("512", "1024", "1536")
TEXTURES = (1024, 2048, 4096)


# --------------------------------------------------------------------------- validation
def model_cost(resolution: str) -> int:
    return config.COST_MODEL[resolution]


def _check_active_limit(db: Session, user: User) -> None:
    active = db.scalar(select(func.count()).select_from(Generation).where(
        Generation.user_id == user.id, Generation.status.in_(("queued", "running"))))
    if active >= config.MAX_ACTIVE_JOBS_PER_USER:
        raise HTTPException(429, f"You already have {active} generations running. Wait for one to finish.")


def validate_image_upload(data: bytes) -> bytes:
    if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"Images must be under {config.MAX_UPLOAD_MB} MB.")
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
        with Image.open(io.BytesIO(data)) as im:
            if im.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError
            im = im.convert("RGBA") if im.mode in ("RGBA", "LA", "P") else im.convert("RGB")
            im.thumbnail((2048, 2048))
            buf = io.BytesIO()
            im.save(buf, "PNG")  # re-encode: strips metadata and anything that isn't pixels
            return buf.getvalue()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "Upload a PNG, JPG or WEBP image.")


# --------------------------------------------------------------------------- create
def create_image(db: Session, user: User, idea: str, style: str, optimize: bool, seed: int | None,
                 source: str = "web") -> Generation:
    _check_active_limit(db, user)
    try:
        prompt = build_prompt(idea, style, optimize)
    except ValueError as e:
        raise HTTPException(400, str(e))
    gen = Generation(user_id=user.id, kind="image", source=source, prompt=prompt,
                     params={"idea": idea, "style": style, "optimize_for_3d": optimize, "seed": seed},
                     cost_credits=config.COST_IMAGE)
    db.add(gen)
    db.flush()
    credits.charge(db, user.id, config.COST_IMAGE, ref=gen.id, note="Image generation")
    db.commit()
    _pool.submit(_run, gen.id)
    return gen


def create_model(db: Session, user: User, *, image_bytes: bytes | None = None, parent: Generation | None = None,
                 resolution: str = "1024", decimation_target: int = 300000, texture_size: int = 2048,
                 seed: int | None = None, source: str = "web") -> Generation:
    if resolution not in RESOLUTIONS:
        raise HTTPException(400, "resolution must be 512, 1024 or 1536")
    if texture_size not in TEXTURES:
        raise HTTPException(400, "texture_size must be 1024, 2048 or 4096")
    if not 20000 <= decimation_target <= 1000000:
        raise HTTPException(400, "decimation_target must be between 20,000 and 1,000,000")
    _check_active_limit(db, user)
    if parent is not None:
        if parent.user_id != user.id or parent.kind != "image" or parent.status != "done":
            raise HTTPException(404, "Image not found.")
        input_key = parent.output_key
    elif image_bytes is not None:
        input_key = new_key(user.id, "inputs", "png")
        storage.put_bytes(input_key, validate_image_upload(image_bytes))
    else:
        raise HTTPException(400, "Provide an image.")
    cost = model_cost(resolution)
    gen = Generation(user_id=user.id, kind="model", source=source, input_key=input_key,
                     parent_id=parent.id if parent else None, cost_credits=cost,
                     params={"resolution": resolution, "decimation_target": decimation_target,
                             "texture_size": texture_size, "seed": seed})
    db.add(gen)
    db.flush()
    credits.charge(db, user.id, cost, ref=gen.id, note=f"3D model ({resolution})")
    db.commit()
    _pool.submit(_run, gen.id)
    return gen


# --------------------------------------------------------------------------- run
def _reporter(gen_id: str):
    def report(stage: str, progress: int):
        with SessionLocal() as db:
            db.execute(update(Generation).where(Generation.id == gen_id)
                       .values(stage=stage[:160], progress=max(0, min(99, int(progress)))))
            db.commit()
    return report


def _prepare_for_3d(png: bytes, report) -> bytes:
    """Background removal (skipped if the image already has transparency), then crop + pad to the object."""
    im = Image.open(io.BytesIO(png))
    has_alpha = im.mode == "RGBA" and im.getchannel("A").getextrema()[0] < 250
    if not has_alpha:
        bg = bg_provider()
        if bg is not None:
            im = Image.open(io.BytesIO(bg.remove_background(png, report)))
    im = im.convert("RGBA")
    bbox = im.getchannel("A").point(lambda a: 255 if a > 200 else 0).getbbox()
    if bbox:
        x0, y0, x1, y1 = bbox
        w, h = x1 - x0, y1 - y0
        side = int(max(w, h) * 1.15)
        canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        canvas.paste(im.crop(bbox), ((side - w) // 2, (side - h) // 2))
        im = canvas
    im.thumbnail((1024, 1024))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _run(gen_id: str) -> None:
    report = _reporter(gen_id)
    with SessionLocal() as db:
        gen = db.get(Generation, gen_id)
        if not gen or gen.status != "queued":
            return
        gen.status, gen.stage, gen.progress = "running", "Starting", 2
        db.commit()
        kind, prompt, params, input_key, user_id = gen.kind, gen.prompt, dict(gen.params), gen.input_key, gen.user_id
    try:
        if kind == "image":
            res = image_provider().generate_image(prompt, NEGATIVE, params.get("seed"), 1024, report)
            key = new_key(user_id, "images", res.ext)
            storage.put_bytes(key, res.data)
            meta = {"seed": res.seed, "bytes": len(res.data)}
        else:
            src = storage.get_bytes(input_key)
            clean = _prepare_for_3d(src, report)
            clean_key = new_key(user_id, "inputs", "png")
            storage.put_bytes(clean_key, clean)
            res = mesh_provider().generate_mesh(clean, params, report)
            key = new_key(user_id, "models", "glb")
            storage.put_bytes(key, res.glb)
            meta = {**res.meta, "clean_key": clean_key, "bytes": len(res.glb)}
        with SessionLocal() as db:
            gen = db.get(Generation, gen_id)
            gen.status, gen.stage, gen.progress = "done", "Done", 100
            gen.output_key, gen.output_meta, gen.finished_at = key, meta, now()
            db.commit()
    except Exception as exc:
        safe = str(exc) if isinstance(exc, ProviderError) else "Something went wrong while generating."
        if not isinstance(exc, ProviderError):
            log.error("generation %s crashed:\n%s", gen_id, traceback.format_exc())
        fail_and_refund(gen_id, safe + " Your credits were refunded.")


def fail_and_refund(gen_id: str, message: str) -> None:
    with SessionLocal() as db:
        gen = db.get(Generation, gen_id)
        if not gen:
            return
        gen.status, gen.error, gen.stage, gen.finished_at = "failed", message, "Failed", now()
        if not gen.refunded and gen.cost_credits:
            credits.grant(db, gen.user_id, gen.cost_credits, "refund", ref=gen.id, note="Refund: generation failed")
            gen.refunded = True
        db.commit()


def recover_interrupted() -> int:
    """On startup, anything left queued/running was interrupted by a restart: refund it."""
    with SessionLocal() as db:
        ids = db.scalars(select(Generation.id).where(Generation.status.in_(("queued", "running")))).all()
    for gid in ids:
        fail_and_refund(gid, "Interrupted by a server restart. Your credits were refunded — please try again.")
    return len(ids)


# --------------------------------------------------------------------------- present
def serialize(gen: Generation) -> dict:
    out = {
        "id": gen.id, "kind": gen.kind, "status": gen.status, "stage": gen.stage, "progress": gen.progress,
        "prompt": (gen.params or {}).get("idea") if gen.kind == "image" else None,
        "params": gen.params, "cost_credits": gen.cost_credits, "refunded": gen.refunded, "error": gen.error,
        "parent_id": gen.parent_id, "source": gen.source,
        "created_at": gen.created_at.isoformat() if gen.created_at else None,
        "finished_at": gen.finished_at.isoformat() if gen.finished_at else None,
        "output": None,
    }
    if gen.status == "done" and gen.output_key:
        if gen.kind == "image":
            out["output"] = {"image_url": storage.url(gen.output_key), "seed": (gen.output_meta or {}).get("seed")}
        else:
            meta = gen.output_meta or {}
            out["output"] = {
                "glb_url": storage.url(gen.output_key),
                "download_url": storage.url(gen.output_key, download_name=f"chat3d-{gen.id[:8]}.glb"),
                "size_mb": round(meta.get("bytes", 0) / 1e6, 2),
            }
    if gen.input_key and gen.kind == "model":
        out["input_image_url"] = storage.url(gen.input_key)
    return out
