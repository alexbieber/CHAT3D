"""Passwords, sessions, API keys, signed file URLs."""
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from datetime import timedelta

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .db import ApiKey, User, get_db, now

SESSION_COOKIE = "c3d_session"


# ---- passwords -------------------------------------------------------------
def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode()[:72], bcrypt.gensalt(rounds=12)).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode()[:72], hashed.encode())
    except ValueError:
        return False


# ---- sessions --------------------------------------------------------------
def make_session_token(user: User) -> str:
    payload = {
        "sub": user.id,
        "v": user.token_version,
        "exp": now() + timedelta(days=config.SESSION_DAYS),
    }
    return jwt.encode(payload, config.SECRET_KEY, algorithm="HS256")


def set_session_cookie(response, user: User) -> None:
    response.set_cookie(
        SESSION_COOKIE, make_session_token(user), max_age=config.SESSION_DAYS * 86400,
        httponly=True, secure=config.COOKIE_SECURE, samesite="lax", path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def _user_from_cookie(request: Request, db: Session) -> User | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    try:
        data = jwt.decode(token, config.SECRET_KEY, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    user = db.get(User, data.get("sub"))
    if not user or not user.is_active or user.token_version != data.get("v"):
        return None
    return user


def _check_origin(request: Request) -> None:
    """CSRF defence for cookie-authenticated writes (on top of SameSite=Lax)."""
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != config.APP_URL and config.IS_PROD:
        raise HTTPException(403, "Cross-site request blocked.")


def optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    return _user_from_cookie(request, db)


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = _user_from_cookie(request, db)
    if not user:
        raise HTTPException(401, "Sign in to continue.")
    _check_origin(request)
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "Admins only.")
    return user


# ---- API keys --------------------------------------------------------------
API_KEY_PREFIX = "c3d_live_"


def new_api_key() -> tuple[str, str, str]:
    """Returns (full_key, display_prefix, sha256_hash). The full key is shown once."""
    raw = API_KEY_PREFIX + secrets.token_urlsafe(32)
    return raw, raw[: len(API_KEY_PREFIX) + 6], hashlib.sha256(raw.encode()).hexdigest()


def api_key_user(request: Request, db: Session = Depends(get_db)) -> User:
    auth = request.headers.get("authorization", "")
    key = auth[7:].strip() if auth.lower().startswith("bearer ") else request.headers.get("x-api-key", "")
    if not key.startswith(API_KEY_PREFIX):
        raise HTTPException(401, "Missing or malformed API key. Send 'Authorization: Bearer c3d_live_...'.")
    digest = hashlib.sha256(key.encode()).hexdigest()
    row = db.scalar(select(ApiKey).where(ApiKey.key_hash == digest, ApiKey.revoked.is_(False)))
    if not row or not row.user.is_active:
        raise HTTPException(401, "Invalid or revoked API key.")
    row.last_used_at = now()
    db.commit()
    return row.user


# ---- tokens for password reset --------------------------------------------
def new_reset_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    return raw, hashlib.sha256(raw.encode()).hexdigest()


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# ---- signed file URLs (local storage) -------------------------------------
def sign_path(key: str, ttl: int | None = None) -> str:
    exp = int(time.time()) + (ttl or config.SIGNED_URL_TTL)
    sig = hmac.new(config.SECRET_KEY.encode(), f"{key}|{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"{config.APP_URL}/files/{key}?exp={exp}&sig={sig}"


def verify_signed(key: str, exp: int, sig: str) -> bool:
    if exp < time.time():
        return False
    good = hmac.new(config.SECRET_KEY.encode(), f"{key}|{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(good, sig)


# ---- simple in-memory rate limiter ----------------------------------------
_hits: dict[str, deque] = defaultdict(deque)


def rate_limit(bucket: str, limit: int, window_s: int) -> None:
    q, t = _hits[bucket], time.time()
    while q and q[0] < t - window_s:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "Too many attempts. Wait a minute and try again.")
    q.append(t)
