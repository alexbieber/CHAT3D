"""Public REST API (API-key auth), billed in the same credits as the web app."""
import base64
import ipaddress
import socket
from typing import Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import config, generations
from ..db import Generation, User, get_db
from ..prompting import STYLES
from ..security import api_key_user, rate_limit

router = APIRouter(prefix="/v1", tags=["public API v1"])


class V1ImageIn(BaseModel):
    prompt: str = Field(min_length=2, max_length=1000, description="Describe the object.")
    style: str = Field("realistic", description="One of: " + ", ".join(STYLES))
    optimize_for_3d: bool = True
    seed: Optional[int] = Field(None, ge=0, le=2**31 - 1)


class V1ModelIn(BaseModel):
    image_generation_id: Optional[str] = Field(None, description="Id of a finished /v1/images generation")
    image_url: Optional[str] = Field(None, description="Public https URL of a PNG/JPG/WEBP")
    image_base64: Optional[str] = Field(None, description="Base64 image (optionally a data: URI)")
    resolution: str = Field("1024", description="512 | 1024 | 1536")
    decimation_target: int = 300000
    texture_size: int = 2048
    seed: Optional[int] = Field(None, ge=0, le=2**31 - 1)


def _safe_fetch(url: str) -> bytes:
    """Fetch a customer-supplied URL without letting it reach internal networks (SSRF)."""
    p = urlparse(url)
    if p.scheme != "https" or not p.hostname:
        raise HTTPException(400, "image_url must be an https URL.")
    try:
        for info in socket.getaddrinfo(p.hostname, 443):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                raise HTTPException(400, "image_url points to a private address.")
    except socket.gaierror:
        raise HTTPException(400, "image_url host could not be resolved.")
    try:
        with httpx.stream("GET", url, timeout=20, follow_redirects=False) as r:
            r.raise_for_status()
            data = b""
            for chunk in r.iter_bytes():
                data += chunk
                if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024:
                    raise HTTPException(413, f"Image must be under {config.MAX_UPLOAD_MB} MB.")
            return data
    except httpx.HTTPError:
        raise HTTPException(400, "Couldn't download image_url.")


def _limit(user: User):
    rate_limit(f"v1:{user.id}", 60, 60)


@router.get("/account", summary="Credit balance")
def account(user: User = Depends(api_key_user)):
    return {"email": user.email, "credits": user.credits,
            "pricing": {"image": config.COST_IMAGE, "model": config.COST_MODEL}}


@router.post("/images", summary="Idea → image (costs image credits)")
def v1_image(body: V1ImageIn, user: User = Depends(api_key_user), db: Session = Depends(get_db)):
    _limit(user)
    if body.style not in STYLES:
        raise HTTPException(400, "Unknown style.")
    gen = generations.create_image(db, user, body.prompt, body.style, body.optimize_for_3d, body.seed, source="api")
    return generations.serialize(gen)


@router.post("/models", summary="Image → 3D GLB (JSON body)")
def v1_model(body: V1ModelIn, user: User = Depends(api_key_user), db: Session = Depends(get_db)):
    _limit(user)
    common = dict(resolution=body.resolution, decimation_target=body.decimation_target,
                  texture_size=body.texture_size, seed=body.seed, source="api")
    if body.image_generation_id:
        parent = db.get(Generation, body.image_generation_id)
        if not parent or parent.deleted:
            raise HTTPException(404, "Image generation not found.")
        gen = generations.create_model(db, user, parent=parent, **common)
    elif body.image_url:
        gen = generations.create_model(db, user, image_bytes=_safe_fetch(body.image_url), **common)
    elif body.image_base64:
        raw = body.image_base64.split(",", 1)[1] if body.image_base64.startswith("data:") else body.image_base64
        try:
            data = base64.b64decode(raw, validate=True)
        except Exception:
            raise HTTPException(400, "image_base64 is not valid base64.")
        gen = generations.create_model(db, user, image_bytes=data, **common)
    else:
        raise HTTPException(400, "Send one of image_generation_id, image_url or image_base64.")
    return generations.serialize(gen)


@router.post("/models/upload", summary="Image → 3D GLB (multipart upload)")
async def v1_model_upload(file: UploadFile = File(...), resolution: str = Form("1024"),
                          decimation_target: int = Form(300000), texture_size: int = Form(2048),
                          seed: Optional[int] = Form(None),
                          user: User = Depends(api_key_user), db: Session = Depends(get_db)):
    _limit(user)
    gen = generations.create_model(db, user, image_bytes=await file.read(), resolution=resolution,
                                   decimation_target=decimation_target, texture_size=texture_size,
                                   seed=seed, source="api")
    return generations.serialize(gen)


@router.get("/generations/{gen_id}", summary="Poll a generation")
def v1_get(gen_id: str, user: User = Depends(api_key_user), db: Session = Depends(get_db)):
    gen = db.get(Generation, gen_id)
    if not gen or gen.user_id != user.id or gen.deleted:
        raise HTTPException(404, "Not found.")
    return generations.serialize(gen)
