"""Web app endpoints (cookie session)."""
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import config, generations
from ..db import CreditTxn, Generation, User, get_db
from ..prompting import STYLES
from ..security import current_user

router = APIRouter(prefix="/api", tags=["studio"])


class ImageIn(BaseModel):
    idea: str = Field(min_length=2, max_length=1000)
    style: str = "realistic"
    optimize_for_3d: bool = True
    seed: Optional[int] = Field(None, ge=0, le=2**31 - 1)


class ModelIn(BaseModel):
    image_generation_id: str
    resolution: str = "1024"
    decimation_target: int = 300000
    texture_size: int = 2048
    seed: Optional[int] = Field(None, ge=0, le=2**31 - 1)


@router.get("/config")
def public_config():
    return {**config.public_config(), "styles": list(STYLES)}


@router.post("/generations/image")
def create_image(body: ImageIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.style not in STYLES:
        raise HTTPException(400, "Unknown style.")
    gen = generations.create_image(db, user, body.idea, body.style, body.optimize_for_3d, body.seed)
    return generations.serialize(gen)


@router.post("/generations/model")
def create_model_from_image(body: ModelIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    parent = db.get(Generation, body.image_generation_id)
    if not parent or parent.deleted:
        raise HTTPException(404, "Image not found.")
    gen = generations.create_model(db, user, parent=parent, resolution=body.resolution,
                                   decimation_target=body.decimation_target, texture_size=body.texture_size,
                                   seed=body.seed)
    return generations.serialize(gen)


@router.post("/generations/model/upload")
async def create_model_from_upload(
    file: UploadFile = File(...), resolution: str = Form("1024"), decimation_target: int = Form(300000),
    texture_size: int = Form(2048), seed: Optional[int] = Form(None),
    user: User = Depends(current_user), db: Session = Depends(get_db),
):
    data = await file.read()
    gen = generations.create_model(db, user, image_bytes=data, resolution=resolution,
                                   decimation_target=decimation_target, texture_size=texture_size, seed=seed)
    return generations.serialize(gen)


@router.get("/generations")
def list_generations(kind: Optional[str] = None, status: Optional[str] = None, limit: int = 24, offset: int = 0,
                     user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(Generation).where(Generation.user_id == user.id, Generation.deleted.is_(False))
    if kind in ("image", "model"):
        q = q.where(Generation.kind == kind)
    if status:
        q = q.where(Generation.status == status)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(Generation.created_at.desc()).limit(min(limit, 100)).offset(offset)).all()
    return {"total": total, "items": [generations.serialize(g) for g in rows]}


def _own(db: Session, user: User, gen_id: str) -> Generation:
    gen = db.get(Generation, gen_id)
    if not gen or gen.user_id != user.id or gen.deleted:
        raise HTTPException(404, "Not found.")
    return gen


@router.get("/generations/{gen_id}")
def get_generation(gen_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return generations.serialize(_own(db, user, gen_id))


@router.delete("/generations/{gen_id}")
def delete_generation(gen_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    gen = _own(db, user, gen_id)
    if gen.status in ("queued", "running"):
        raise HTTPException(409, "Wait for it to finish before deleting.")
    gen.deleted = True
    db.commit()
    return {"ok": True}


@router.get("/credits/history")
def credit_history(limit: int = 50, user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(CreditTxn).where(CreditTxn.user_id == user.id)
                      .order_by(CreditTxn.created_at.desc()).limit(min(limit, 200))).all()
    return {"balance": user.credits, "items": [
        {"amount": r.amount, "balance_after": r.balance_after, "kind": r.kind, "note": r.note,
         "created_at": r.created_at.isoformat()} for r in rows]}
