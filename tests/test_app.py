import base64
import hashlib
import hmac
import io
import json
import time

from PIL import Image

from backend import payments
from backend.db import Payment, SessionLocal
from conftest import signup, wait_done


def png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), "red").save(buf, "PNG")
    return buf.getvalue()


# ---------------------------------------------------------------- auth
def test_signup_login_logout(client):
    me = signup(client, "alice@example.com")
    assert me["credits"] == 12 and not me["is_admin"]
    assert client.get("/api/auth/me").status_code == 200
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/login", json={"email": "alice@example.com", "password": "wrongwrong"}).status_code == 401
    r = client.post("/api/auth/login", json={"email": "ALICE@example.com", "password": "hunter2hunter2"})
    assert r.status_code == 200
    assert client.post("/api/auth/signup", json={"email": "alice@example.com", "password": "x" * 9,
                                                  "accept_terms": True}).status_code == 409


def test_signup_requires_terms(client):
    r = client.post("/api/auth/signup", json={"email": "t@example.com", "password": "hunter2hunter2", "accept_terms": False})
    assert r.status_code == 400


def test_password_reset_flow(client):
    signup(client, "reset@example.com")
    client.post("/api/auth/logout")
    assert client.post("/api/auth/forgot", json={"email": "reset@example.com"}).json()["ok"]
    # Find token by inserting a known one (email is only logged without SMTP)
    from backend.db import PasswordReset, User, now
    from backend.security import new_reset_token
    from datetime import timedelta
    raw, digest = new_reset_token()
    with SessionLocal() as db:
        u = db.query(User).filter_by(email="reset@example.com").one()
        db.add(PasswordReset(user_id=u.id, token_hash=digest, expires_at=now() + timedelta(hours=1)))
        db.commit()
    assert client.post("/api/auth/reset", json={"token": raw, "password": "newpassword1"}).status_code == 200
    assert client.post("/api/auth/reset", json={"token": raw, "password": "again12345"}).status_code == 400
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"email": "reset@example.com", "password": "newpassword1"}).status_code == 200


def test_admin_from_env(client):
    me = signup(client, "admin@example.com")
    assert me["is_admin"]
    assert client.get("/api/admin/stats").status_code == 200


def test_non_admin_blocked(client):
    signup(client)
    assert client.get("/api/admin/stats").status_code == 403


# ---------------------------------------------------------------- generations & credits
def test_idea_to_image_to_model_charges_credits(client):
    signup(client)
    g = client.post("/api/generations/image", json={"idea": "a brass owl", "style": "stylized"}).json()
    img = wait_done(client, g["id"])
    assert img["status"] == "done" and img["output"]["image_url"]
    assert client.get("/api/auth/me").json()["credits"] == 11
    m = client.post("/api/generations/model", json={"image_generation_id": img["id"], "resolution": "512"}).json()
    model = wait_done(client, m["id"])
    assert model["status"] == "done", model
    assert client.get("/api/auth/me").json()["credits"] == 3  # 12 - 1 - 8
    # signed file URL works, and tampering fails
    url = model["output"]["glb_url"].replace("http://localhost:8000", "")
    assert client.get(url).status_code == 200
    assert client.get(url.replace("sig=", "sig=0")).status_code == 403


def test_upload_to_model(client):
    signup(client)
    r = client.post("/api/generations/model/upload", files={"file": ("a.png", png_bytes(), "image/png")},
                    data={"resolution": "512"})
    assert r.status_code == 200, r.text
    assert wait_done(client, r.json()["id"])["status"] == "done"


def test_bad_upload_rejected_without_charge(client):
    signup(client)
    r = client.post("/api/generations/model/upload", files={"file": ("a.png", b"not an image", "image/png")})
    assert r.status_code == 400
    assert client.get("/api/auth/me").json()["credits"] == 12


def test_insufficient_credits(client):
    signup(client)
    r = client.post("/api/generations/model/upload", files={"file": ("a.png", png_bytes(), "image/png")},
                    data={"resolution": "1536", "seed": "1"})
    assert r.status_code == 200
    wait_done(client, r.json()["id"])
    r = client.post("/api/generations/model/upload", files={"file": ("a.png", png_bytes(), "image/png")},
                    data={"resolution": "1024"})
    assert r.status_code == 402
    assert client.get("/api/auth/me").json()["credits"] == 0


def test_failure_refunds(client):
    signup(client)
    g = client.post("/api/generations/image", json={"idea": "mock-fail thing"}).json()
    done = wait_done(client, g["id"])
    assert done["status"] == "failed" and done["refunded"] and "refunded" in done["error"]
    assert client.get("/api/auth/me").json()["credits"] == 12
    m = client.post("/api/generations/model/upload", files={"file": ("a.png", png_bytes(), "image/png")},
                    data={"resolution": "512", "seed": "666"}).json()
    assert wait_done(client, m["id"])["status"] == "failed"
    assert client.get("/api/auth/me").json()["credits"] == 12
    kinds = [t["kind"] for t in client.get("/api/credits/history").json()["items"]]
    assert kinds.count("refund") == 2


def test_cannot_use_someone_elses_image(client):
    signup(client)
    g = client.post("/api/generations/image", json={"idea": "a cup"}).json()
    wait_done(client, g["id"])
    client.post("/api/auth/logout")
    signup(client)
    r = client.post("/api/generations/model", json={"image_generation_id": g["id"]})
    assert r.status_code == 404
    assert client.get(f"/api/generations/{g['id']}").status_code == 404


def test_gallery_and_delete(client):
    signup(client)
    g = client.post("/api/generations/image", json={"idea": "a lamp"}).json()
    wait_done(client, g["id"])
    assert client.get("/api/generations?kind=image").json()["total"] == 1
    assert client.delete(f"/api/generations/{g['id']}").json()["ok"]
    assert client.get("/api/generations").json()["total"] == 0


# ---------------------------------------------------------------- API keys / public API
def test_api_key_flow(client):
    signup(client)
    k = client.post("/api/keys", json={"name": "ci"}).json()
    h = {"Authorization": f"Bearer {k['key']}"}
    client.post("/api/auth/logout")  # API works without cookies
    assert client.get("/v1/account", headers=h).json()["credits"] == 12
    g = client.post("/v1/images", headers=h, json={"prompt": "a robot"}).json()
    img = wait_done(client, g["id"], "/v1/generations/", headers=h)
    m = client.post("/v1/models", headers=h, json={"image_generation_id": img["id"], "resolution": "512"}).json()
    assert wait_done(client, m["id"], "/v1/generations/", headers=h)["status"] == "done"
    b64 = base64.b64encode(png_bytes()).decode()
    r = client.post("/v1/models", headers=h, json={"image_base64": b64, "resolution": "512"})
    assert r.status_code == 402  # 12 - 1 - 8 = 3 left
    assert client.post("/v1/models", headers=h, json={"image_url": "http://127.0.0.1/x.png"}).status_code == 400
    assert client.get("/v1/account", headers={"Authorization": "Bearer c3d_live_nope"}).status_code == 401


def test_revoked_key(client):
    signup(client)
    k = client.post("/api/keys", json={"name": "tmp"}).json()
    client.delete(f"/api/keys/{k['id']}")
    assert client.get("/v1/account", headers={"Authorization": f"Bearer {k['key']}"}).status_code == 401


# ---------------------------------------------------------------- payments
def _make_rzp_payment(user_id: str, order_id: str) -> None:
    with SessionLocal() as db:
        db.add(Payment(user_id=user_id, provider="razorpay", provider_order_id=order_id, pack_id="starter",
                       credits=50, amount=49900, currency="INR"))
        db.commit()


def test_razorpay_checkout_verify_is_idempotent(client):
    me = signup(client)
    _make_rzp_payment(me["id"], "order_A")
    sig = hmac.new(b"rzp_secret", b"order_A|pay_1", hashlib.sha256).hexdigest()
    body = {"razorpay_order_id": "order_A", "razorpay_payment_id": "pay_1", "razorpay_signature": sig}
    assert client.post("/api/billing/razorpay/verify", json={**body, "razorpay_signature": "bad"}).status_code == 400
    r = client.post("/api/billing/razorpay/verify", json=body)
    assert r.status_code == 200 and r.json()["credits"] == 62
    # webhook for the same payment arrives afterwards: no double credit
    event = json.dumps({"event": "payment.captured",
                        "payload": {"payment": {"entity": {"id": "pay_1", "order_id": "order_A"}}}}).encode()
    hsig = hmac.new(b"rzp_hook", event, hashlib.sha256).hexdigest()
    assert client.post("/webhooks/razorpay", content=event, headers={"x-razorpay-signature": hsig}).status_code == 200
    assert client.post("/webhooks/razorpay", content=event, headers={"x-razorpay-signature": "x"}).status_code == 400
    assert client.get("/api/auth/me").json()["credits"] == 62
    assert client.get("/api/billing/payments").json()["items"][0]["status"] == "paid"


def test_razorpay_webhook_alone_credits(client):
    me = signup(client)
    _make_rzp_payment(me["id"], "order_B")
    event = json.dumps({"event": "order.paid",
                        "payload": {"payment": {"entity": {"id": "pay_2", "order_id": "order_B"}}}}).encode()
    hsig = hmac.new(b"rzp_hook", event, hashlib.sha256).hexdigest()
    client.post("/webhooks/razorpay", content=event, headers={"x-razorpay-signature": hsig})
    assert client.get("/api/auth/me").json()["credits"] == 62


def test_stripe_webhook(client):
    me = signup(client)
    with SessionLocal() as db:
        db.add(Payment(user_id=me["id"], provider="stripe", provider_order_id="cs_test_1", pack_id="creator",
                       credits=200, amount=2399, currency="USD"))
        db.commit()
    payload = json.dumps({"id": "evt_1", "object": "event", "type": "checkout.session.completed",
                          "data": {"object": {"id": "cs_test_1", "object": "checkout.session",
                                              "payment_status": "paid", "payment_intent": "pi_1"}}})
    t = int(time.time())
    sig = hmac.new(b"whsec_test", f"{t}.{payload}".encode(), hashlib.sha256).hexdigest()
    hdr = {"stripe-signature": f"t={t},v1={sig}"}
    assert client.post("/webhooks/stripe", content=payload, headers=hdr).status_code == 200
    assert client.post("/webhooks/stripe", content=payload, headers=hdr).status_code == 200
    assert client.get("/api/auth/me").json()["credits"] == 212
    assert client.post("/webhooks/stripe", content=payload, headers={"stripe-signature": "t=1,v1=bad"}).status_code == 400


def test_admin_adjust_and_refund(client):
    victim = signup(client)
    client.post("/api/auth/logout")
    r = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "hunter2hunter2"})
    if r.status_code != 200:
        signup(client, "admin@example.com")
    r = client.post(f"/api/admin/users/{victim['id']}/credits", json={"amount": 5, "note": "goodwill"})
    assert r.json()["credits"] == 17
    assert client.post(f"/api/admin/users/{victim['id']}/credits", json={"amount": -100, "note": "oops"}).status_code == 402
    assert client.get("/api/admin/users?q=example").json()["total"] >= 1
    stats = client.get("/api/admin/stats").json()
    assert "revenue" in stats and stats["users"] >= 2


def test_pages_render(client):
    for p in ["/", "/pricing", "/login", "/signup", "/app", "/account", "/admin", "/developers",
              "/terms", "/privacy", "/refunds", "/acceptable-use"]:
        r = client.get(p)
        assert r.status_code == 200, p
        assert "{{" not in r.text, p


def test_format_amount():
    assert payments.format_amount(49900, "INR") == "₹499.00"
    assert payments.format_amount(699, "USD") == "$6.99"
