"""Shared FastAPI dependencies: auth, permissions, rate limiting, audit."""
from __future__ import annotations

import datetime as dt

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from . import models
from .config import settings
from .database import get_db
from .security import decode_token, rate_limit_ok

bearer = HTTPBearer(auto_error=False)


def get_client_ip(request: Request) -> str:
    return request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown").split(",")[0].strip()


def rate_limit(request: Request, key_suffix: str, per_minute: int):
    key = f"{get_client_ip(request)}:{key_suffix}"
    if not rate_limit_ok(key, per_minute):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests. Please slow down.")


def _load_user(db: Session, payload: dict) -> models.User:
    sub = payload.get("sub", "")
    user = None
    if sub.startswith("user:"):
        user = db.get(models.User, int(sub.split(":", 1)[1]))
    elif sub.startswith("guest:"):
        gk = payload.get("gk")
        user = db.query(models.User).filter(models.User.guest_key == gk).first()
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session.")
    now = dt.datetime.now(dt.timezone.utc)
    if user.is_banned:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is banned.")
    if user.banned_until and user.banned_until.replace(tzinfo=dt.timezone.utc) > now:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is temporarily suspended.")
    if user.locked_until and user.locked_until.replace(tzinfo=dt.timezone.utc) > now:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account temporarily locked due to failed logins.")
    if user.is_guest and user.expires_at and user.expires_at.replace(tzinfo=dt.timezone.utc) < now:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Guest session expired.")
    return user


def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> models.User:
    if not creds:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated.")
    payload = decode_token(creds.credentials)
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token.")
    return _load_user(db, payload)


def get_optional_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> models.User | None:
    if not creds:
        return None
    payload = decode_token(creds.credentials)
    if not payload:
        return None
    try:
        return _load_user(db, payload)
    except HTTPException:
        return None


def require_registered(user: models.User = Depends(get_current_user)) -> models.User:
    if user.is_guest:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Registered account required for this action.")
    return user


def require_role(*roles: models.Role):
    def _check(user: models.User = Depends(get_current_user)) -> models.User:
        if user.role not in roles and not (models.Role.ADMIN in roles and user.role == models.Role.ADMIN):
            # admins pass moderator checks implicitly
            if user.role == models.Role.ADMIN:
                return user
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions.")
        return user

    return _check


def require_moderator(user: models.User = Depends(get_current_user)) -> models.User:
    if user.role not in (models.Role.MODERATOR, models.Role.ADMIN):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Moderator access required.")
    return user


def require_admin(user: models.User = Depends(get_current_user)) -> models.User:
    if user.role != models.Role.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator access required.")
    return user


def audit(db: Session, actor_id: int | None, action: str, target: str = "", detail: str = "", ip: str = ""):
    db.add(models.AuditLog(actor_id=actor_id, action=action, target=target, detail=detail[:2000], ip=ip))
    db.commit()


def notify(db: Session, user_id: int, kind: str, title: str, body: str = "", link: str = ""):
    db.add(models.Notification(user_id=user_id, kind=kind, title=title, body=body[:1000], link=link))
    db.commit()
