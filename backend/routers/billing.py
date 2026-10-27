import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config, payments
from ..db import Payment, User, get_db
from ..security import current_user

router = APIRouter(tags=["billing"])


class PackIn(BaseModel):
    pack_id: str


class RazorpayVerifyIn(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class StripeConfirmIn(BaseModel):
    session_id: str


def _pay_out(p: Payment) -> dict:
    return {"id": p.id, "provider": p.provider, "pack_id": p.pack_id, "credits": p.credits,
            "amount": p.amount, "currency": p.currency, "amount_display": payments.format_amount(p.amount, p.currency),
            "status": p.status, "created_at": p.created_at.isoformat(),
            "paid_at": p.paid_at.isoformat() if p.paid_at else None}


@router.get("/api/billing/packs")
def packs():
    return {"packs": config.CREDIT_PACKS, "razorpay": payments.razorpay_enabled(), "stripe": payments.stripe_enabled()}


@router.post("/api/billing/razorpay/order")
def razorpay_order(body: PackIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return payments.razorpay_create_order(db, user, payments.get_pack(body.pack_id))


@router.post("/api/billing/razorpay/verify")
def razorpay_verify(body: RazorpayVerifyIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    pay = db.scalar(select(Payment).where(Payment.provider == "razorpay",
                                          Payment.provider_order_id == body.razorpay_order_id))
    if not pay or pay.user_id != user.id:
        raise HTTPException(404, "Order not found.")
    if not payments.razorpay_verify_checkout(body.razorpay_order_id, body.razorpay_payment_id,
                                             body.razorpay_signature):
        raise HTTPException(400, "Payment signature didn't match. If you were charged, contact support.")
    pay = payments.fulfill(db, "razorpay", body.razorpay_order_id, body.razorpay_payment_id)
    db.refresh(user)
    return {"payment": _pay_out(pay), "credits": db.get(User, user.id).credits}


@router.post("/api/billing/stripe/checkout")
def stripe_checkout(body: PackIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return payments.stripe_create_checkout(db, user, payments.get_pack(body.pack_id))


@router.post("/api/billing/stripe/confirm")
def stripe_confirm(body: StripeConfirmIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    pay = payments.stripe_confirm(db, user, body.session_id)
    return {"payment": _pay_out(pay) if pay else None, "credits": db.get(User, user.id).credits}


@router.get("/api/billing/payments")
def my_payments(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Payment).where(Payment.user_id == user.id, Payment.status != "created")
                      .order_by(Payment.created_at.desc()).limit(100)).all()
    return {"items": [_pay_out(p) for p in rows]}


@router.post("/webhooks/razorpay", include_in_schema=False)
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)):
    body = await request.body()
    if not payments.razorpay_verify_webhook(body, request.headers.get("x-razorpay-signature", "")):
        raise HTTPException(400, "Invalid signature.")
    payments.razorpay_handle_event(db, json.loads(body))
    return {"ok": True}


@router.post("/webhooks/stripe", include_in_schema=False)
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    body = await request.body()
    payments.stripe_handle_webhook(db, body, request.headers.get("stripe-signature", ""))
    return {"ok": True}
