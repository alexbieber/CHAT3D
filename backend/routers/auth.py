from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config, credits, mailer
from ..db import PasswordReset, User, aware, get_db, now
from ..security import (
    clear_session_cookie, current_user, hash_password, hash_token, new_reset_token, rate_limit,
    set_session_cookie, verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class SignupIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = Field("", max_length=120)
    accept_terms: bool


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class ForgotIn(BaseModel):
    email: EmailStr


class ResetIn(BaseModel):
    token: str
    password: str = Field(min_length=8, max_length=128)


class ChangePwIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


def me_payload(user: User) -> dict:
    return {"id": user.id, "email": user.email, "name": user.name, "credits": user.credits,
            "is_admin": user.is_admin, "created_at": user.created_at.isoformat()}


@router.post("/signup")
def signup(body: SignupIn, request: Request, response: Response, db: Session = Depends(get_db)):
    rate_limit(f"signup:{request.client.host if request.client else '-'}", 10, 3600)
    if not body.accept_terms:
        raise HTTPException(400, "Please accept the Terms and Privacy Policy.")
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "An account with this email already exists. Sign in instead.")
    user = User(email=email, name=body.name.strip(), password_hash=hash_password(body.password),
                is_admin=email in config.ADMIN_EMAILS)
    db.add(user)
    db.flush()
    if config.SIGNUP_CREDITS:
        credits.grant(db, user.id, config.SIGNUP_CREDITS, "signup", note="Welcome credits")
    db.commit()
    db.refresh(user)
    set_session_cookie(response, user)
    return me_payload(user)


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    rate_limit(f"login:{request.client.host if request.client else '-'}", 20, 300)
    rate_limit(f"login-email:{body.email.lower()}", 8, 300)
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Email or password is incorrect.")
    if not user.is_active:
        raise HTTPException(403, "This account is suspended. Contact support.")
    if user.email in config.ADMIN_EMAILS and not user.is_admin:
        user.is_admin = True
        db.commit()
    set_session_cookie(response, user)
    return me_payload(user)


@router.post("/logout")
def logout(response: Response):
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return me_payload(user)


@router.post("/forgot")
def forgot(body: ForgotIn, request: Request, db: Session = Depends(get_db)):
    rate_limit(f"forgot:{request.client.host if request.client else '-'}", 5, 900)
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user and user.is_active:
        raw, digest = new_reset_token()
        db.add(PasswordReset(user_id=user.id, token_hash=digest, expires_at=now() + timedelta(hours=1)))
        db.commit()
        mailer.send(user.email, f"Reset your {config.APP_NAME} password",
                    f"Use this link within an hour to set a new password:\n\n{config.APP_URL}/reset?token={raw}\n\n"
                    "If you didn't ask for this, ignore this email.")
    # Same answer either way so the form can't be used to discover accounts.
    return {"ok": True, "message": "If that email has an account, a reset link is on its way."}


@router.post("/reset")
def reset(body: ResetIn, response: Response, db: Session = Depends(get_db)):
    row = db.scalar(select(PasswordReset).where(PasswordReset.token_hash == hash_token(body.token)))
    if not row or row.used or aware(row.expires_at) < now():
        raise HTTPException(400, "This reset link has expired. Request a new one.")
    user = db.get(User, row.user_id)
    user.password_hash = hash_password(body.password)
    user.token_version += 1
    row.used = True
    db.commit()
    set_session_cookie(response, user)
    return me_payload(user)


@router.post("/change-password")
def change_password(body: ChangePwIn, response: Response, user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    user = db.merge(user)
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(400, "Current password is incorrect.")
    user.password_hash = hash_password(body.new_password)
    user.token_version += 1  # signs out other devices
    db.commit()
    set_session_cookie(response, user)
    return {"ok": True}
