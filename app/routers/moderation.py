"""Reports, blocks, mutes, bans, appeals, and moderator actions."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import audit, get_client_ip, get_current_user, notify, require_moderator, require_registered
from ..security import sanitize_text

router = APIRouter(prefix="/api/moderation", tags=["moderation"])


@router.post("/reports", status_code=201)
def file_report(data: schemas.ReportIn, request: Request,
                user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    # abuse-of-reporting guard: max 10 open reports per reporter
    open_count = db.query(func.count(models.Report.id)).filter(
        models.Report.reporter_id == user.id, models.Report.status == models.ReportStatus.OPEN).scalar() or 0
    if open_count >= 10:
        raise HTTPException(429, "Too many open reports. Please wait for review.")
    # target must exist (basic check for message/user targets)
    ttype = models.ReportTarget(data.target_type)
    if ttype == models.ReportTarget.MESSAGE and not db.get(models.Message, data.target_id):
        raise HTTPException(404, "Reported message not found.")
    if ttype == models.ReportTarget.USER and not db.get(models.User, data.target_id):
        raise HTTPException(404, "Reported user not found.")
    if ttype == models.ReportTarget.ROOM and not db.get(models.Room, data.target_id):
        raise HTTPException(404, "Reported room not found.")
    r = models.Report(reporter_id=user.id, target_type=ttype, target_id=data.target_id,
                      reason=sanitize_text(data.reason, 120), details=sanitize_text(data.details, 2000))
    db.add(r)
    db.commit()
    db.refresh(r)
    for mod in db.query(models.User).filter(models.User.role.in_([models.Role.ADMIN, models.Role.MODERATOR])).all():
        notify(db, mod.id, "moderation", "New report filed",
               f"{data.target_type} #{data.target_id}: {data.reason}", "#/mod/reports")
    audit(db, user.id, "report_file", f"{data.target_type}:{data.target_id}", ip=get_client_ip(request))
    return {"ok": True, "report_id": r.id}


@router.get("/reports")
def list_reports(status: str | None = None, mod: models.User = Depends(require_moderator), db: Session = Depends(get_db)):
    q = db.query(models.Report).order_by(desc(models.Report.created_at))
    if status:
        q = q.filter(models.Report.status == status)
    out = []
    for r in q.limit(200).all():
        reporter = db.get(models.User, r.reporter_id)
        out.append({"id": r.id, "reporter": reporter.username if reporter else "?",
                    "target_type": r.target_type.value, "target_id": r.target_id,
                    "reason": r.reason, "details": r.details, "status": r.status.value,
                    "created_at": r.created_at})
    return {"reports": out}


@router.post("/reports/{rid}/status")
def set_report_status(rid: int, status: str, mod: models.User = Depends(require_moderator),
                      db: Session = Depends(get_db)):
    r = db.get(models.Report, rid)
    if not r:
        raise HTTPException(404, "Report not found.")
    try:
        r.status = models.ReportStatus(status)
    except ValueError:
        raise HTTPException(400, "Invalid status.")
    if r.status in (models.ReportStatus.ACTIONED, models.ReportStatus.DISMISSED):
        r.resolved_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    audit(db, mod.id, "report_status", f"report:{rid}:{status}")
    return {"ok": True}


@router.get("/reports/mine")
def my_reports(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = db.query(models.Report).filter(models.Report.reporter_id == user.id).order_by(
        desc(models.Report.created_at)).limit(100).all()
    return {"reports": [{"id": r.id, "target_type": r.target_type.value, "target_id": r.target_id,
                         "reason": r.reason, "status": r.status.value, "created_at": r.created_at} for r in items]}


@router.post("/actions")
def mod_action(data: schemas.ModActionIn, request: Request,
               mod: models.User = Depends(require_moderator), db: Session = Depends(get_db)):
    action = models.ModAction(data.action)
    now = dt.datetime.now(dt.timezone.utc)
    target_user = db.get(models.User, data.target_user_id) if data.target_user_id else None
    if data.target_user_id and not target_user:
        raise HTTPException(404, "Target user not found.")
    if target_user and target_user.role == models.Role.ADMIN and mod.role != models.Role.ADMIN:
        raise HTTPException(403, "Only admins can act against admins.")
    reason = sanitize_text(data.reason, 2000) or action.value
    expires = None
    if data.duration_hours:
        expires = now + dt.timedelta(hours=data.duration_hours)

    if action == models.ModAction.MESSAGE_DELETE and data.target_message_id:
        m = db.get(models.Message, data.target_message_id)
        if m:
            m.deleted_at = now
    elif action == models.ModAction.WARNING and target_user:
        notify(db, target_user.id, "moderation", "Moderator warning", reason, "#/safety")
    elif action == models.ModAction.ROOM_MUTE and target_user and data.target_message_id:
        m = db.get(models.Message, data.target_message_id)
        if m and m.room_id:
            mem = db.query(models.RoomMembership).filter(
                models.RoomMembership.room_id == m.room_id,
                models.RoomMembership.user_id == target_user.id).first()
            if mem:
                mem.muted_until = expires or (now + dt.timedelta(hours=24))
    elif action == models.ModAction.TEMP_SUSPEND and target_user:
        target_user.banned_until = expires or (now + dt.timedelta(hours=24))
        target_user.ban_reason = reason
        notify(db, target_user.id, "moderation", "Account temporarily suspended", reason, "#/safety")
    elif action == models.ModAction.PERM_BAN and target_user:
        target_user.is_banned = True
        target_user.ban_reason = reason
        notify(db, target_user.id, "moderation", "Account banned", reason, "#/safety")
    elif action == models.ModAction.UNBAN and target_user:
        target_user.is_banned = False
        target_user.banned_until = None
        target_user.ban_reason = ""
        notify(db, target_user.id, "moderation", "Account restored",
               "Your account access has been restored.", "#/")

    db.add(models.ModerationAction(actor_id=mod.id, action=action,
                                   target_user_id=data.target_user_id,
                                   target_message_id=data.target_message_id,
                                   reason=reason, expires_at=expires))
    db.commit()
    audit(db, mod.id, f"mod_{action.value}",
          f"user:{data.target_user_id} msg:{data.target_message_id}", reason, ip=get_client_ip(request))
    return {"ok": True}


@router.get("/actions")
def action_history(mod: models.User = Depends(require_moderator), db: Session = Depends(get_db)):
    items = db.query(models.ModerationAction).order_by(desc(models.ModerationAction.created_at)).limit(200).all()
    return {"actions": [{"id": a.id, "actor_id": a.actor_id, "action": a.action.value,
                         "target_user_id": a.target_user_id, "target_message_id": a.target_message_id,
                         "reason": a.reason, "created_at": a.created_at} for a in items]}


@router.post("/block/{user_id}")
def block_user(user_id: int, user: models.User = Depends(require_registered), db: Session = Depends(get_db)):
    if user_id == user.id:
        raise HTTPException(400, "You cannot block yourself.")
    target = db.get(models.User, user_id)
    if not target:
        raise HTTPException(404, "User not found.")
    if not db.query(models.Block).filter(models.Block.blocker_id == user.id,
                                         models.Block.blocked_id == user_id).first():
        db.add(models.Block(blocker_id=user.id, blocked_id=user_id))
        db.commit()
    return {"ok": True}


@router.delete("/block/{user_id}")
def unblock_user(user_id: int, user: models.User = Depends(require_registered), db: Session = Depends(get_db)):
    db.query(models.Block).filter(models.Block.blocker_id == user.id,
                                  models.Block.blocked_id == user_id).delete()
    db.commit()
    return {"ok": True}


@router.get("/block")
def blocked_list(user: models.User = Depends(require_registered), db: Session = Depends(get_db)):
    rows = db.query(models.Block).filter(models.Block.blocker_id == user.id).all()
    out = []
    for b in rows:
        u = db.get(models.User, b.blocked_id)
        if u:
            out.append({"id": u.id, "username": u.username, "display_name": u.display_name})
    return {"blocked": out}


@router.post("/appeals")
def file_appeal(data: schemas.AppealIn, request: Request, db: Session = Depends(get_db)):
    # Appeals must work even for banned/suspended users: decode token without ban checks.
    from fastapi.security import HTTPAuthorizationCredentials
    from ..deps import bearer as _bearer
    from ..security import decode_token as _decode
    auth = request.headers.get("authorization", "")
    token = auth[7:] if auth.lower().startswith("bearer ") else ""
    payload = _decode(token) if token else None
    if not payload:
        raise HTTPException(401, "Not authenticated.")
    sub = payload.get("sub", "")
    user = None
    if sub.startswith("user:"):
        user = db.get(models.User, int(sub.split(":", 1)[1]))
    if not user or user.is_guest:
        raise HTTPException(401, "Not authenticated.")
    r = models.Report(reporter_id=user.id, target_type=models.ReportTarget.USER, target_id=user.id,
                      reason="Ban/suspension appeal", details=sanitize_text(data.details, 2000))
    db.add(r)
    db.commit()
    audit(db, user.id, "appeal_file", f"user:{user.id}", ip=get_client_ip(request))
    return {"ok": True, "report_id": r.id}
