"""Credit ledger. Every balance change writes a CreditTxn in the same DB transaction."""
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .db import CreditTxn, User


class InsufficientCredits(HTTPException):
    def __init__(self, need: int, have: int):
        super().__init__(402, f"This needs {need} credits and you have {have}. Buy credits to continue.")


def _apply(db: Session, user_id: str, amount: int, kind: str, ref: str | None, note: str) -> int:
    """Atomic conditional update: never lets the balance go negative, even under concurrency."""
    stmt = update(User).where(User.id == user_id).values(credits=User.credits + amount)
    if amount < 0:
        stmt = stmt.where(User.credits >= -amount)
    res = db.execute(stmt)
    if res.rowcount != 1:
        have = db.scalar(select(User.credits).where(User.id == user_id)) or 0
        raise InsufficientCredits(-amount, have)
    balance = db.scalar(select(User.credits).where(User.id == user_id))
    db.add(CreditTxn(user_id=user_id, amount=amount, balance_after=balance, kind=kind, ref=ref, note=note))
    return balance


def grant(db: Session, user_id: str, amount: int, kind: str, ref: str | None = None, note: str = "") -> int:
    return _apply(db, user_id, abs(amount), kind, ref, note)


def charge(db: Session, user_id: str, amount: int, ref: str, note: str = "") -> int:
    return _apply(db, user_id, -abs(amount), "spend", ref, note)


def admin_adjust(db: Session, user_id: str, amount: int, note: str) -> int:
    return _apply(db, user_id, amount, "admin", None, note)
