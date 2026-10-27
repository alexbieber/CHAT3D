from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import ApiKey, User, get_db
from ..security import current_user, new_api_key

router = APIRouter(prefix="/api/keys", tags=["api keys"])
MAX_KEYS = 10


class KeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


def _out(k: ApiKey) -> dict:
    return {"id": k.id, "name": k.name, "prefix": k.prefix, "created_at": k.created_at.isoformat(),
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None}


@router.get("")
def list_keys(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(ApiKey).where(ApiKey.user_id == user.id, ApiKey.revoked.is_(False))
                      .order_by(ApiKey.created_at.desc())).all()
    return {"items": [_out(k) for k in rows]}


@router.post("")
def create_key(body: KeyIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    count = db.scalar(select(func.count()).select_from(ApiKey).where(
        ApiKey.user_id == user.id, ApiKey.revoked.is_(False)))
    if count >= MAX_KEYS:
        raise HTTPException(400, f"You can have up to {MAX_KEYS} active keys. Revoke one first.")
    raw, prefix, digest = new_api_key()
    key = ApiKey(user_id=user.id, name=body.name.strip(), prefix=prefix, key_hash=digest)
    db.add(key)
    db.commit()
    return {**_out(key), "key": raw}  # shown once


@router.delete("/{key_id}")
def revoke_key(key_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    key = db.get(ApiKey, key_id)
    if not key or key.user_id != user.id:
        raise HTTPException(404, "Key not found.")
    key.revoked = True
    db.commit()
    return {"ok": True}
