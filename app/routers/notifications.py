"""In-app notifications."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_current_user

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("", response_model=list[schemas.NotificationOut])
def list_notifications(unread_only: bool = False, limit: int = 50,
                       user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(models.Notification).filter(models.Notification.user_id == user.id)
    if unread_only:
        q = q.filter(models.Notification.is_read.is_(False))
    return [schemas.NotificationOut.model_validate(n)
            for n in q.order_by(desc(models.Notification.created_at)).limit(limit).all()]


@router.get("/unread-count")
def unread_count(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    n = db.query(func.count(models.Notification.id)).filter(
        models.Notification.user_id == user.id, models.Notification.is_read.is_(False)).scalar() or 0
    return {"unread": n}


@router.post("/{nid}/read")
def mark_read(nid: int, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    n = db.get(models.Notification, nid)
    if not n or n.user_id != user.id:
        raise HTTPException(404, "Notification not found.")
    n.is_read = True
    db.commit()
    return {"ok": True}


@router.post("/read-all")
def mark_all_read(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.query(models.Notification).filter(models.Notification.user_id == user.id).update({"is_read": True})
    db.commit()
    return {"ok": True}
