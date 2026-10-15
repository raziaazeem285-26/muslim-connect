"""Admin dashboard: overview, user management, rooms, settings, audit log."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import desc, func, text
from sqlalchemy.orm import Session

from .. import models, schemas
from ..config import settings
from ..database import get_db
from ..deps import audit, get_client_ip, require_admin

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/overview")
def overview(admin: models.User = Depends(require_admin), db: Session = Depends(get_db)):
    now = dt.datetime.now(dt.timezone.utc)
    day_ago = now - dt.timedelta(days=1)
    return {
        "users_total": db.query(func.count(models.User.id)).filter(models.User.is_guest.is_(False)).scalar(),
        "guests_total": db.query(func.count(models.User.id)).filter(models.User.is_guest.is_(True)).scalar(),
        "messages_total": db.query(func.count(models.Message.id)).scalar(),
        "messages_24h": db.query(func.count(models.Message.id)).filter(models.Message.created_at > day_ago).scalar(),
        "rooms_total": db.query(func.count(models.Room.id)).filter(models.Room.is_active.is_(True)).scalar(),
        "questions_total": db.query(func.count(models.Question.id)).scalar(),
        "reports_open": db.query(func.count(models.Report.id)).filter(
            models.Report.status == models.ReportStatus.OPEN).scalar(),
        "scholar_pending": db.query(func.count(models.ScholarApplication.id)).filter(
            models.ScholarApplication.status == models.ScholarAppStatus.PENDING).scalar(),
        "banned_users": db.query(func.count(models.User.id)).filter(models.User.is_banned.is_(True)).scalar(),
    }


@router.get("/users")
def manage_users(q: str | None = None, role: str | None = None, banned: bool | None = None,
                 admin: models.User = Depends(require_admin), db: Session = Depends(get_db)):
    query = db.query(models.User)
    if q:
        like = f"%{q}%"
        query = query.filter((models.User.username.ilike(like)) | (models.User.display_name.ilike(like)))
    if role:
        query = query.filter(models.User.role == role)
    if banned is not None:
        query = query.filter(models.User.is_banned.is_(banned))
    users = query.order_by(desc(models.User.created_at)).limit(200).all()
    return {"users": [{
        "id": u.id, "username": u.username, "display_name": u.display_name, "email": u.email,
        "role": u.role.value, "is_guest": u.is_guest, "is_banned": u.is_banned,
        "banned_until": u.banned_until, "ban_reason": u.ban_reason,
        "is_scholar_verified": u.is_scholar_verified, "created_at": u.created_at,
        "last_seen_at": u.last_seen_at,
    } for u in users]}


@router.post("/users/{uid}/role")
def set_role(uid: int, role: str, request: Request,
             admin: models.User = Depends(require_admin), db: Session = Depends(get_db)):
    u = db.get(models.User, uid)
    if not u:
        raise HTTPException(404, "User not found.")
    if u.id == admin.id:
        raise HTTPException(400, "You cannot change your own role.")
    try:
        new_role = models.Role(role)
    except ValueError:
        raise HTTPException(400, "Invalid role.")
    if new_role == models.Role.GUEST:
        raise HTTPException(400, "Cannot assign guest role.")
    old = u.role
    u.role = new_role
    if new_role != models.Role.SCHOLAR:
        u.is_scholar_verified = False
    db.commit()
    audit(db, admin.id, "role_change", f"user:{uid}", f"{old.value} -> {new_role.value}", ip=get_client_ip(request))
    return {"ok": True}


@router.get("/rooms")
def manage_rooms(admin: models.User = Depends(require_admin), db: Session = Depends(get_db)):
    rooms = db.query(models.Room).order_by(models.Room.name).limit(200).all()
    return {"rooms": [{
        "id": r.id, "slug": r.slug, "name": r.name, "kind": r.kind.value,
        "visibility": r.visibility.value, "is_active": r.is_active,
        "allow_guests": r.allow_guests, "slow_mode_seconds": r.slow_mode_seconds,
    } for r in rooms]}


@router.post("/rooms/{rid}/toggle")
def toggle_room(rid: int, request: Request, admin: models.User = Depends(require_admin), db: Session = Depends(get_db)):
    r = db.get(models.Room, rid)
    if not r:
        raise HTTPException(404, "Room not found.")
    r.is_active = not r.is_active
    db.commit()
    audit(db, admin.id, "room_toggle", f"room:{rid}", f"active={r.is_active}", ip=get_client_ip(request))
    return {"ok": True, "is_active": r.is_active}


@router.post("/rooms/{rid}/slowmode")
def set_slowmode(rid: int, seconds: int, request: Request,
                 admin: models.User = Depends(require_admin), db: Session = Depends(get_db)):
    r = db.get(models.Room, rid)
    if not r:
        raise HTTPException(404, "Room not found.")
    if not 0 <= seconds <= 3600:
        raise HTTPException(400, "Slow mode must be 0-3600 seconds.")
    r.slow_mode_seconds = seconds
    db.commit()
    audit(db, admin.id, "room_slowmode", f"room:{rid}", f"{seconds}s", ip=get_client_ip(request))
    return {"ok": True}


@router.get("/settings")
def get_settings(admin: models.User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.query(models.Setting).all()
    return {"settings": {s.key: s.value for s in rows}}


@router.post("/settings/{key}")
def set_setting(key: str, data: schemas.SettingIn, request: Request,
                admin: models.User = Depends(require_admin), db: Session = Depends(get_db)):
    allowed = {"community_rules", "announcement", "guest_message_limit", "signup_enabled", "maintenance_mode"}
    if key not in allowed:
        raise HTTPException(400, "Unknown setting.")
    s = db.get(models.Setting, key)
    if not s:
        s = models.Setting(key=key)
        db.add(s)
    s.value = data.value
    s.updated_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    audit(db, admin.id, "setting_update", f"setting:{key}", ip=get_client_ip(request))
    return {"ok": True}


@router.get("/settings/public")
def public_settings(db: Session = Depends(get_db)):
    """Non-sensitive settings visible to everyone (rules, announcement)."""
    rows = db.query(models.Setting).filter(
        models.Setting.key.in_(["community_rules", "announcement"])).all()
    return {s.key: s.value for s in rows}


@router.get("/audit")
def audit_log(limit: int = 200, admin: models.User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.query(models.AuditLog).order_by(desc(models.AuditLog.created_at)).limit(limit).all()
    return {"entries": [{
        "id": a.id, "actor_id": a.actor_id, "action": a.action, "target": a.target,
        "detail": a.detail, "ip": a.ip, "created_at": a.created_at,
    } for a in rows]}


@router.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {"ok": db_ok, "env": settings.ENV}
