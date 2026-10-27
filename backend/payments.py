"""Credit-pack purchases through Razorpay (INR) and Stripe (USD).

Fulfilment is idempotent: a Payment row flips created -> paid with a
conditional UPDATE, and only the call that wins that flip grants credits.
So the browser callback and the webhook can both arrive safely.
"""
import hashlib
import hmac
import logging

import httpx
from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.orm import Session

from . import config, credits, mailer
from .db import Payment, User, now

log = logging.getLogger("chat3d.payments")


def get_pack(pack_id: str) -> dict:
    for p in config.CREDIT_PACKS:
        if p["id"] == pack_id:
            return p
    raise HTTPException(404, "Unknown credit pack.")


def fulfill(db: Session, provider: str, order_id: str, payment_id: str | None) -> Payment | None:
    pay = db.query(Payment).filter_by(provider=provider, provider_order_id=order_id).one_or_none()
    if not pay:
        log.warning("fulfill: unknown %s order %s", provider, order_id)
        return None
    res = db.execute(
        update(Payment).where(Payment.id == pay.id, Payment.status == "created")
        .values(status="paid", provider_payment_id=payment_id, paid_at=now())
    )
    if res.rowcount == 1:
        credits.grant(db, pay.user_id, pay.credits, "purchase", ref=pay.id,
                      note=f"Bought {pay.credits} credits ({provider})")
        db.commit()
        user = db.get(User, pay.user_id)
        mailer.send(user.email, f"{config.APP_NAME} receipt — {pay.credits} credits",
                    f"Thanks for your purchase.\n\nCredits: {pay.credits}\nAmount: {format_amount(pay.amount, pay.currency)}\n"
                    f"Reference: {pay.id}\n\n{config.COMPANY_NAME}\n{config.COMPANY_ADDRESS}\n")
    else:
        db.rollback()
    db.refresh(pay)
    return pay


def format_amount(amount: int, currency: str) -> str:
    sym = {"INR": "₹", "USD": "$"}.get(currency.upper(), currency.upper() + " ")
    return f"{sym}{amount / 100:,.2f}"


# --------------------------------------------------------------------------- Razorpay
def razorpay_enabled() -> bool:
    return bool(config.RAZORPAY_KEY_ID and config.RAZORPAY_KEY_SECRET)


def razorpay_create_order(db: Session, user: User, pack: dict) -> dict:
    if not razorpay_enabled():
        raise HTTPException(503, "Razorpay is not configured.")
    pay = Payment(user_id=user.id, provider="razorpay", provider_order_id="pending", pack_id=pack["id"],
                  credits=pack["credits"], amount=pack["inr"], currency="INR")
    db.add(pay)
    db.flush()
    r = httpx.post(
        "https://api.razorpay.com/v1/orders",
        auth=(config.RAZORPAY_KEY_ID, config.RAZORPAY_KEY_SECRET),
        json={"amount": pack["inr"], "currency": "INR", "receipt": pay.id,
              "notes": {"user_id": user.id, "pack_id": pack["id"], "payment_ref": pay.id}},
        timeout=20,
    )
    if r.status_code >= 300:
        db.rollback()
        log.error("Razorpay order failed: %s %s", r.status_code, r.text)
        raise HTTPException(502, "Couldn't start the Razorpay payment. Try again.")
    pay.provider_order_id = r.json()["id"]
    db.commit()
    return {"key_id": config.RAZORPAY_KEY_ID, "order_id": pay.provider_order_id, "amount": pack["inr"],
            "currency": "INR", "name": config.APP_NAME, "description": f"{pack['credits']} credits",
            "prefill": {"email": user.email, "name": user.name}}


def razorpay_verify_checkout(order_id: str, payment_id: str, signature: str) -> bool:
    expected = hmac.new(config.RAZORPAY_KEY_SECRET.encode(), f"{order_id}|{payment_id}".encode(),
                        hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def razorpay_verify_webhook(body: bytes, signature: str) -> bool:
    if not config.RAZORPAY_WEBHOOK_SECRET:
        return False
    expected = hmac.new(config.RAZORPAY_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def razorpay_handle_event(db: Session, event: dict) -> None:
    kind = event.get("event")
    if kind in ("payment.captured", "order.paid"):
        ent = event["payload"]["payment"]["entity"]
        fulfill(db, "razorpay", ent["order_id"], ent["id"])
    elif kind == "payment.failed":
        ent = event["payload"]["payment"]["entity"]
        db.execute(update(Payment).where(Payment.provider == "razorpay",
                                         Payment.provider_order_id == ent["order_id"],
                                         Payment.status == "created").values(status="failed"))
        db.commit()


# --------------------------------------------------------------------------- Stripe
def stripe_enabled() -> bool:
    return bool(config.STRIPE_SECRET_KEY)


def _stripe():
    import stripe

    stripe.api_key = config.STRIPE_SECRET_KEY
    return stripe


def stripe_create_checkout(db: Session, user: User, pack: dict) -> dict:
    if not stripe_enabled():
        raise HTTPException(503, "Stripe is not configured.")
    stripe = _stripe()
    pay = Payment(user_id=user.id, provider="stripe", provider_order_id="pending", pack_id=pack["id"],
                  credits=pack["credits"], amount=pack["usd"], currency="USD")
    db.add(pay)
    db.flush()
    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=[{"quantity": 1, "price_data": {
                "currency": "usd", "unit_amount": pack["usd"],
                "product_data": {"name": f"{config.APP_NAME} — {pack['credits']} credits"}}}],
            customer_email=user.email,
            client_reference_id=user.id,
            metadata={"payment_ref": pay.id, "user_id": user.id, "pack_id": pack["id"]},
            success_url=f"{config.APP_URL}/account?checkout=stripe&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{config.APP_URL}/pricing?checkout=cancelled",
        )
    except Exception:
        db.rollback()
        log.exception("Stripe checkout failed")
        raise HTTPException(502, "Couldn't start the Stripe checkout. Try again.")
    pay.provider_order_id = session.id
    db.commit()
    return {"url": session.url}


def stripe_confirm(db: Session, user: User, session_id: str) -> Payment | None:
    """Called when the customer lands back on /account — doesn't wait for the webhook."""
    stripe = _stripe()
    s = stripe.checkout.Session.retrieve(session_id).to_dict()
    if s.get("client_reference_id") != user.id:
        raise HTTPException(404, "Checkout not found.")
    if s.get("payment_status") == "paid":
        return fulfill(db, "stripe", s["id"], s.get("payment_intent"))
    return None


def stripe_handle_webhook(db: Session, body: bytes, sig: str) -> None:
    stripe = _stripe()
    if not config.STRIPE_WEBHOOK_SECRET:
        raise HTTPException(503, "Stripe webhook secret not configured.")
    try:
        event = stripe.Webhook.construct_event(body, sig, config.STRIPE_WEBHOOK_SECRET).to_dict()
    except Exception:
        raise HTTPException(400, "Invalid Stripe signature.")
    if event["type"] in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        s = event["data"]["object"]
        if s.get("payment_status") == "paid":
            fulfill(db, "stripe", s["id"], s.get("payment_intent"))
