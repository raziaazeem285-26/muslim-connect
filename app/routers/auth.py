"""Authentication: register, login, guest, email verify, password reset."""
from __future__ import annotations

import datetime as dt
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..config import settings
from ..database import get_db
from ..deps import audit, get_client_ip, get_current_user, rate_limit, require_registered
from ..mail import reset_email, send_email, verification_email
from ..security import (
    create_access_token, hash_password, hash_token, new_guest_key, sanitize_text, verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

GUEST_NAMES = ["Traveller", "Seeker", "Guest", "Wanderer", "Visitor", "Pilgrim"]


def _user_out(user: models.User) -> schemas.UserOut:
    return schemas.UserOut.model_validate(user)


def _issue_user_token(user: models.User) -> str:
    return create_access_token(f"user:{user.id}", {"role": user.role.value, "guest": False})


def _issue_guest_token(user: models.User) -> str:
    return create_access_token(
        f"guest:{user.id}", {"role": "guest", "guest": True, "gk": user.guest_key},
        minutes=settings.GUEST_TOKEN_EXPIRE_MINUTES,
    )


@router.post("/register", response_model=schemas.TokenOut, status_code=201)
def register(data: schemas.RegisterIn, request: Request, db: Session = Depends(get_db)):
    rate_limit(request, "auth", settings.RATE_LIMIT_AUTH_PER_MIN)
    username = data.username.strip()
    if db.query(models.User).filter(models.User.username.ilike(username)).first():
        raise HTTPException(400, "Username is already taken.")
    if db.query(models.User).filter(models.User.email == data.email.lower()).first():
        raise HTTPException(400, "An account with this email already exists.")
    user = models.User(
        username=username,
        display_name=sanitize_text(data.display_name, 80),
        email=data.email.lower(),
        password_hash=hash_password(data.password),
        country=sanitize_text(data.country, 80),
        language=data.language or "en",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    # bootstrap first admin if configured and none exists
    if settings.BOOTSTRAP_ADMIN_EMAIL and user.email == settings.BOOTSTRAP_ADMIN_EMAIL.lower():
        if not db.query(models.User).filter(models.User.role == models.Role.ADMIN).first():
            user.role = models.Role.ADMIN
            db.commit()
    # email verification token
    raw = secrets.token_urlsafe(32)
    db.add(models.EmailToken(
        user_id=user.id, purpose="verify", token_hash=hash_token(raw),
        expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=24),
    ))
    db.commit()
    link = f"{settings.FRONTEND_URL}/#/verify?token={raw}"
    subject, body = verification_email(link)
    send_email(user.email, subject, body)
    audit(db, user.id, "register", f"user:{user.id}", ip=get_client_ip(request))
    return schemas.TokenOut(access_token=_issue_user_token(user), user=_user_out(user))


@router.post("/login", response_model=schemas.TokenOut)
def login(data: schemas.LoginIn, request: Request, db: Session = Depends(get_db)):
    rate_limit(request, "auth", settings.RATE_LIMIT_AUTH_PER_MIN)
    ident = data.username_or_email.strip().lower()
    user = db.query(models.User).filter(models.User.username.ilike(ident)).first()
    if not user and "@" in ident:
        user = db.query(models.User).filter(models.User.email == ident).first()
    now = dt.datetime.now(dt.timezone.utc)
    if user and user.locked_until and user.locked_until.replace(tzinfo=dt.timezone.utc) > now:
        raise HTTPException(403, "Account temporarily locked after too many failed attempts. Try again later.")
    if not user or user.is_guest or not verify_password(data.password, user.password_hash or ""):
        if user and not user.is_guest:
            user.failed_logins += 1
            if user.failed_logins >= 8:
                user.locked_until = now + dt.timedelta(minutes=15)
                user.failed_logins = 0
            db.commit()
        # generic message: do not reveal which identifier was wrong
        raise HTTPException(401, "Invalid credentials.")
    user.failed_logins = 0
    user.last_seen_at = now
    db.commit()
    audit(db, user.id, "login", f"user:{user.id}", ip=get_client_ip(request))
    return schemas.TokenOut(access_token=_issue_user_token(user), user=_user_out(user))


@router.post("/guest", response_model=schemas.TokenOut, status_code=201)
def guest_entry(data: schemas.GuestIn, request: Request, db: Session = Depends(get_db)):
    rate_limit(request, "guest", settings.RATE_LIMIT_AUTH_PER_MIN)
    ip = get_client_ip(request)
    # anti-ban-evasion: limit fresh guest sessions per IP per hour (soft)
    # (enforced via rate limiter window; bans are tracked per guest_key + fingerprint below)
    import random
    gk = new_guest_key()
    name = sanitize_text(data.display_name, 80) or f"{random.choice(GUEST_NAMES)}-{secrets.token_hex(2).upper()}"
    now = dt.datetime.now(dt.timezone.utc)
    user = models.User(
        username=f"guest_{secrets.token_hex(6)}",
        display_name=name,
        role=models.Role.GUEST,
        is_guest=True,
        guest_key=gk,
        language=data.language or "en",
        country=sanitize_text(data.country, 80),
        avatar_emoji="🧳",
        expires_at=now + dt.timedelta(hours=settings.GUEST_SESSION_TTL_HOURS),
        last_seen_at=now,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    audit(db, user.id, "guest_entry", f"guest:{user.id}", ip=ip)
    return schemas.TokenOut(access_token=_issue_guest_token(user), user=_user_out(user))


@router.post("/logout")
def logout(request: Request, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    audit(db, user.id, "logout", f"user:{user.id}", ip=get_client_ip(request))
    return {"ok": True}


@router.get("/me", response_model=schemas.UserOut)
def me(user: models.User = Depends(get_current_user)):
    return _user_out(user)


@router.post("/verify")
def verify_email(token: str, db: Session = Depends(get_db)):
    th = hash_token(token)
    rec = db.query(models.EmailToken).filter(
        models.EmailToken.token_hash == th, models.EmailToken.purpose == "verify",
        models.EmailToken.used_at.is_(None),
    ).first()
    now = dt.datetime.now(dt.timezone.utc)
    if not rec or rec.expires_at.replace(tzinfo=dt.timezone.utc) < now:
        raise HTTPException(400, "Invalid or expired verification link.")
    rec.used_at = now
    user = db.get(models.User, rec.user_id)
    user.email_verified = True
    db.commit()
    return {"ok": True, "message": "Email verified."}


@router.post("/verify/resend")
def resend_verification(user: models.User = Depends(require_registered), db: Session = Depends(get_db)):
    if user.email_verified:
        return {"ok": True, "message": "Already verified."}
    raw = secrets.token_urlsafe(32)
    db.add(models.EmailToken(
        user_id=user.id, purpose="verify", token_hash=hash_token(raw),
        expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=24),
    ))
    db.commit()
    link = f"{settings.FRONTEND_URL}/#/verify?token={raw}"
    subject, body = verification_email(link)
    emailed = send_email(user.email, subject, body)
    return {"ok": True, "emailed": emailed}


@router.post("/password/reset-request")
def reset_request(data: schemas.PasswordResetRequest, request: Request, db: Session = Depends(get_db)):
    rate_limit(request, "auth", settings.RATE_LIMIT_AUTH_PER_MIN)
    user = db.query(models.User).filter(models.User.email == data.email.lower()).first()
    # Always respond OK to avoid email enumeration.
    if user and not user.is_guest:
        raw = secrets.token_urlsafe(32)
        db.add(models.EmailToken(
            user_id=user.id, purpose="reset", token_hash=hash_token(raw),
            expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1),
        ))
        db.commit()
        link = f"{settings.FRONTEND_URL}/#/reset-password?token={raw}"
        subject, body = reset_email(link)
        send_email(user.email, subject, body)
        audit(db, user.id, "password_reset_request", f"user:{user.id}", ip=get_client_ip(request))
    return {"ok": True, "message": "If the email exists, a reset link has been sent."}


@router.post("/password/reset-confirm")
def reset_confirm(data: schemas.PasswordResetConfirm, db: Session = Depends(get_db)):
    th = hash_token(data.token)
    rec = db.query(models.EmailToken).filter(
        models.EmailToken.token_hash == th, models.EmailToken.purpose == "reset",
        models.EmailToken.used_at.is_(None),
    ).first()
    now = dt.datetime.now(dt.timezone.utc)
    if not rec or rec.expires_at.replace(tzinfo=dt.timezone.utc) < now:
        raise HTTPException(400, "Invalid or expired reset link.")
    rec.used_at = now
    user = db.get(models.User, rec.user_id)
    user.password_hash = hash_password(data.new_password)
    user.failed_logins = 0
    user.locked_until = None
    db.commit()
    return {"ok": True, "message": "Password updated. Please log in."}


@router.post("/password/change")
def change_password(data: schemas.ChangePasswordIn, user: models.User = Depends(require_registered),
                    db: Session = Depends(get_db)):
    if not verify_password(data.current_password, user.password_hash or ""):
        raise HTTPException(400, "Current password is incorrect.")
    user.password_hash = hash_password(data.new_password)
    db.commit()
    return {"ok": True}


@router.post("/upgrade-guest", response_model=schemas.TokenOut)
def upgrade_guest(data: schemas.RegisterIn, request: Request,
                 user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Convert a guest session into a full registered account (keeps profile where sensible)."""
    if not user.is_guest:
        raise HTTPException(400, "Only guest sessions can be upgraded.")
    rate_limit(request, "auth", settings.RATE_LIMIT_AUTH_PER_MIN)
    if db.query(models.User).filter(models.User.username.ilike(data.username.strip())).first():
        raise HTTPException(400, "Username is already taken.")
    if db.query(models.User).filter(models.User.email == data.email.lower()).first():
        raise HTTPException(400, "An account with this email already exists.")
    user.username = data.username.strip()
    user.display_name = sanitize_text(data.display_name, 80)
    user.email = data.email.lower()
    user.password_hash = hash_password(data.password)
    user.is_guest = False
    user.role = models.Role.USER
    user.guest_key = None
    user.expires_at = None
    user.country = sanitize_text(data.country, 80) or user.country
    db.commit()
    db.refresh(user)
    audit(db, user.id, "guest_upgraded", f"user:{user.id}", ip=get_client_ip(request))
    return schemas.TokenOut(access_token=_issue_user_token(user), user=_user_out(user))


@router.delete("/me", status_code=200)
def delete_account(user: models.User = Depends(require_registered), db: Session = Depends(get_db)):
    """Account deletion: anonymizes profile, removes PII, keeps moderated content orphaned."""
    uid = user.id
    user.email = None
    user.password_hash = None
    user.display_name = "Deleted User"
    user.username = f"deleted_{uid}_{secrets.token_hex(4)}"
    user.bio = ""
    user.is_banned = True  # prevent further logins; content remains for moderation context
    user.ban_reason = "account deleted by user"
    db.query(models.EmailToken).filter(models.EmailToken.user_id == uid).delete()
    db.commit()
    audit(db, None, "account_deleted", f"user:{uid}")
    return {"ok": True}
