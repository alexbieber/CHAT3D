from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from .. import credits, generations, payments
from ..db import Generation, Payment, User, get_db, now
from ..security import admin_user

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(admin_user)])

# Rough provider cost per generation, in USD, for margin reporting.
EST_COST_USD = {"image": 0.004, "512": 0.25, "1024": 0.30, "1536": 0.35}


class AdjustIn(BaseModel):
    amount: int = Field(..., ge=-100000, le=100000)
    note: str = Field(min_length=3, max_length=300)


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    since = now() - timedelta(days=30)
    revenue = {}
    for cur, total, n in db.execute(select(Payment.currency, func.sum(Payment.amount), func.count())
                                    .where(Payment.status == "paid").group_by(Payment.currency)):
        revenue[cur] = {"total": int(total or 0), "display": payments.format_amount(int(total or 0), cur), "count": n}
    revenue_30d = {}
    for cur, total in db.execute(select(Payment.currency, func.sum(Payment.amount))
                                 .where(Payment.status == "paid", Payment.paid_at >= since).group_by(Payment.currency)):
        revenue_30d[cur] = payments.format_amount(int(total or 0), cur)
    gen_counts = {f"{k}:{s}": n for k, s, n in db.execute(
        select(Generation.kind, Generation.status, func.count()).group_by(Generation.kind, Generation.status))}
    cost = 0.0  # aggregated in Python: Postgres can't GROUP BY a JSON column
    for kind, params in db.execute(select(Generation.kind, Generation.params)
                                   .where(Generation.status == "done", Generation.created_at >= since)):
        cost += EST_COST_USD["image"] if kind == "image" else EST_COST_USD.get((params or {}).get("resolution"), 0.3)
    return {
        "users": db.scalar(select(func.count()).select_from(User)),
        "users_30d": db.scalar(select(func.count()).select_from(User).where(User.created_at >= since)),
        "revenue": revenue, "revenue_30d": revenue_30d,
        "generations": gen_counts,
        "active_jobs": db.scalar(select(func.count()).select_from(Generation)
                                 .where(Generation.status.in_(("queued", "running")))),
        "est_provider_cost_30d_usd": round(cost, 2),
        "credits_outstanding": db.scalar(select(func.sum(User.credits))) or 0,
    }


@router.get("/users")
def users(q: Optional[str] = None, limit: int = 50, offset: int = 0, db: Session = Depends(get_db)):
    stmt = select(User)
    if q:
        stmt = stmt.where(or_(User.email.ilike(f"%{q}%"), User.name.ilike(f"%{q}%")))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(User.created_at.desc()).limit(min(limit, 200)).offset(offset)).all()
    spent = dict(db.execute(select(Payment.user_id, func.count()).where(Payment.status == "paid")
                            .group_by(Payment.user_id)).all())
    return {"total": total, "items": [
        {"id": u.id, "email": u.email, "name": u.name, "credits": u.credits, "is_admin": u.is_admin,
         "is_active": u.is_active, "purchases": spent.get(u.id, 0), "created_at": u.created_at.isoformat()}
        for u in rows]}


@router.post("/users/{user_id}/credits")
def adjust_credits(user_id: str, body: AdjustIn, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    if not db.get(User, user_id):
        raise HTTPException(404, "User not found.")
    balance = credits.admin_adjust(db, user_id, body.amount, f"{body.note} (by {admin.email})")
    db.commit()
    return {"credits": balance}


@router.post("/users/{user_id}/active")
def set_active(user_id: str, active: bool, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found.")
    if user.id == admin.id:
        raise HTTPException(400, "You can't suspend yourself.")
    user.is_active = active
    if not active:
        user.token_version += 1
    db.commit()
    return {"is_active": user.is_active}


@router.get("/generations")
def all_generations(status: Optional[str] = None, limit: int = 50, offset: int = 0, db: Session = Depends(get_db)):
    stmt = select(Generation, User.email).join(User, User.id == Generation.user_id)
    if status:
        stmt = stmt.where(Generation.status == status)
    rows = db.execute(stmt.order_by(Generation.created_at.desc()).limit(min(limit, 200)).offset(offset)).all()
    return {"items": [{**generations.serialize(g), "user_email": email} for g, email in rows]}


@router.post("/generations/{gen_id}/refund")
def refund_generation(gen_id: str, db: Session = Depends(get_db)):
    gen = db.get(Generation, gen_id)
    if not gen:
        raise HTTPException(404, "Not found.")
    res = db.execute(update(Generation).where(Generation.id == gen_id, Generation.refunded.is_(False))
                     .values(refunded=True))
    if res.rowcount != 1:
        raise HTTPException(409, "Already refunded.")
    credits.grant(db, gen.user_id, gen.cost_credits, "refund", ref=gen.id, note="Refund by support")
    db.commit()
    return {"ok": True}


@router.get("/payments")
def all_payments(limit: int = 100, db: Session = Depends(get_db)):
    rows = db.execute(select(Payment, User.email).join(User, User.id == Payment.user_id)
                      .where(Payment.status != "created").order_by(Payment.created_at.desc())
                      .limit(min(limit, 500))).all()
    return {"items": [{"id": p.id, "user_email": e, "provider": p.provider, "credits": p.credits,
                       "amount_display": payments.format_amount(p.amount, p.currency), "status": p.status,
                       "provider_payment_id": p.provider_payment_id, "created_at": p.created_at.isoformat()}
                      for p, e in rows]}


@router.post("/payments/{payment_id}/mark-refunded")
def mark_refunded(payment_id: str, db: Session = Depends(get_db)):
    """Record a refund issued in the Razorpay/Stripe dashboard and claw back unspent credits."""
    pay = db.get(Payment, payment_id)
    if not pay or pay.status != "paid":
        raise HTTPException(409, "Only paid payments can be marked refunded.")
    user = db.get(User, pay.user_id)
    take = min(user.credits, pay.credits)
    if take:
        credits.admin_adjust(db, user.id, -take, f"Payment {pay.id} refunded")
    pay.status = "refunded"
    db.commit()
    return {"ok": True, "credits_removed": take}
