"""User profiles, directory, follows, bookmarks, export."""
from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import desc, func, or_
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_current_user, get_optional_user, require_registered
from ..security import sanitize_text

router = APIRouter(prefix="/api/users", tags=["users"])


def _public_profile(u: models.User, viewer: models.User | None) -> dict:
    return {
        "id": u.id, "username": u.username, "display_name": u.display_name,
        "avatar_emoji": u.avatar_emoji, "bio": u.bio, "country": u.country,
        "language": u.language, "interests": u.interests,
        "is_guest": u.is_guest, "is_scholar_verified": u.is_scholar_verified,
        "scholar_title": u.scholar_title, "role": u.role.value,
        "online_visible": u.online_visible, "created_at": u.created_at,
        # email / precise identifiers are NEVER exposed publicly
    }


@router.get("/directory")
def directory(q: str | None = None, country: str | None = None, language: str | None = None,
              interest: str | None = None, scholars_only: bool = False,
              limit: int = Query(50, le=100),
              viewer: models.User | None = Depends(get_optional_user), db: Session = Depends(get_db)):
    query = db.query(models.User).filter(models.User.is_guest.is_(False),
                                         models.User.username.notlike("deleted\\_%", escape="\\"))
    if scholars_only:
        query = query.filter(models.User.is_scholar_verified.is_(True))
    if q:
        like = f"%{q}%"
        query = query.filter(or_(models.User.display_name.ilike(like), models.User.username.ilike(like)))
    if country:
        query = query.filter(models.User.country.ilike(f"%{country}%"))
    if language:
        query = query.filter(models.User.language == language)
    if interest:
        query = query.filter(models.User.interests.ilike(f"%{interest}%"))
    users = query.order_by(desc(models.User.created_at)).limit(limit).all()
    return {"users": [_public_profile(u, viewer) for u in users]}


@router.get("/{user_id}")
def get_profile(user_id: int, viewer: models.User | None = Depends(get_optional_user),
                db: Session = Depends(get_db)):
    u = db.get(models.User, user_id)
    if not u or u.is_guest:
        raise HTTPException(404, "User not found.")
    profile = _public_profile(u, viewer)
    if viewer and not viewer.is_guest:
        profile["is_following"] = db.query(models.Follow).filter(
            models.Follow.follower_id == viewer.id, models.Follow.followed_id == u.id).first() is not None
        profile["is_blocked"] = db.query(models.Block).filter(
            models.Block.blocker_id == viewer.id, models.Block.blocked_id == u.id).first() is not None
    return profile


@router.patch("/me", response_model=schemas.UserOut)
def update_profile(data: schemas.ProfileUpdateIn, user: models.User = Depends(require_registered),
                   db: Session = Depends(get_db)):
    for field in ("display_name", "bio", "country", "language", "interests", "avatar_emoji",
                  "online_visible", "notify_messages", "notify_answers", "notify_moderation",
                  "theme", "ui_language"):
        val = getattr(data, field)
        if val is not None:
            if isinstance(val, str):
                val = sanitize_text(val, 1000 if field == "bio" else 500)
            setattr(user, field, val)
    if data.theme and data.theme not in ("light", "dark"):
        raise HTTPException(400, "Invalid theme.")
    db.commit()
    db.refresh(user)
    return schemas.UserOut.model_validate(user)


@router.post("/{user_id}/follow")
def follow(user_id: int, user: models.User = Depends(require_registered), db: Session = Depends(get_db)):
    if user_id == user.id:
        raise HTTPException(400, "You cannot follow yourself.")
    target = db.get(models.User, user_id)
    if not target or target.is_guest:
        raise HTTPException(404, "User not found.")
    if not db.query(models.Follow).filter(models.Follow.follower_id == user.id,
                                           models.Follow.followed_id == user_id).first():
        db.add(models.Follow(follower_id=user.id, followed_id=user_id))
        db.commit()
    return {"ok": True}


@router.delete("/{user_id}/follow")
def unfollow(user_id: int, user: models.User = Depends(require_registered), db: Session = Depends(get_db)):
    db.query(models.Follow).filter(models.Follow.follower_id == user.id,
                                   models.Follow.followed_id == user_id).delete()
    db.commit()
    return {"ok": True}


@router.post("/bookmarks")
def add_bookmark(data: schemas.BookmarkIn, user: models.User = Depends(require_registered), db: Session = Depends(get_db)):
    if not db.query(models.Bookmark).filter(
            models.Bookmark.user_id == user.id, models.Bookmark.target_type == data.target_type,
            models.Bookmark.target_id == data.target_id).first():
        db.add(models.Bookmark(user_id=user.id, target_type=data.target_type, target_id=data.target_id))
        db.commit()
    return {"ok": True}


@router.delete("/bookmarks")
def remove_bookmark(target_type: str, target_id: int, user: models.User = Depends(require_registered),
                    db: Session = Depends(get_db)):
    db.query(models.Bookmark).filter(models.Bookmark.user_id == user.id,
                                     models.Bookmark.target_type == target_type,
                                     models.Bookmark.target_id == target_id).delete()
    db.commit()
    return {"ok": True}


@router.get("/me/bookmarks")
def my_bookmarks(user: models.User = Depends(require_registered), db: Session = Depends(get_db)):
    rows = db.query(models.Bookmark).filter(models.Bookmark.user_id == user.id).order_by(
        desc(models.Bookmark.created_at)).limit(200).all()
    out = []
    for b in rows:
        item: dict = {"target_type": b.target_type, "target_id": b.target_id, "created_at": b.created_at}
        if b.target_type == "question":
            q = db.get(models.Question, b.target_id)
            if q:
                item.update({"title": q.title, "link": f"#/qa/{q.id}"})
        elif b.target_type == "answer":
            a = db.get(models.Answer, b.target_id)
            if a:
                item.update({"title": a.body[:80], "link": f"#/qa/{a.question_id}"})
        out.append(item)
    return {"bookmarks": out}


@router.get("/me/export")
def export_data(user: models.User = Depends(require_registered), db: Session = Depends(get_db)):
    """GDPR-style data export: the user's own profile, messages, questions, answers."""
    messages = db.query(models.Message).filter(models.Message.author_id == user.id,
                                               models.Message.deleted_at.is_(None)).limit(5000).all()
    questions = db.query(models.Question).filter(models.Question.author_id == user.id).all()
    answers = db.query(models.Answer).filter(models.Answer.author_id == user.id).all()
    data = {
        "exported_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "profile": {"username": user.username, "display_name": user.display_name, "bio": user.bio,
                    "country": user.country, "language": user.language, "interests": user.interests,
                    "created_at": str(user.created_at)},
        "messages": [{"id": m.id, "body": m.body, "room_id": m.room_id,
                      "conversation_id": m.conversation_id, "created_at": str(m.created_at)} for m in messages],
        "questions": [{"id": q.id, "title": q.title, "body": q.body} for q in questions],
        "answers": [{"id": a.id, "question_id": a.question_id, "body": a.body} for a in answers],
    }
    return JSONResponse(data)


@router.get("/me/suggested-rooms")
def suggested_rooms(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Suggest public rooms matching the user's language/interests."""
    rooms = db.query(models.Room).filter(models.Room.is_active.is_(True),
                                         models.Room.visibility == models.RoomVisibility.PUBLIC).all()
    interests = {i.strip().lower() for i in (user.interests or "").split(",") if i.strip()}
    scored = []
    for r in rooms:
        score = 0
        if r.language == user.language:
            score += 2
        hay = (r.name + " " + r.description).lower()
        score += sum(1 for i in interests if i and i in hay)
        if r.kind == models.RoomKind.GLOBAL:
            score += 1
        scored.append((score, r))
    scored.sort(key=lambda x: -x[0])
    return {"rooms": [{"slug": r.slug, "name": r.name, "description": r.description,
                       "kind": r.kind.value, "language": r.language} for _, r in scored[:12]]}
