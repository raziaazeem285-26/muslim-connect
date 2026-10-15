"""Verified scholar application workflow and directory."""
from __future__ import annotations

import datetime as dt
import json
import os
import secrets

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy import desc, or_
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import audit, get_client_ip, get_current_user, notify, require_admin, require_registered
from ..paths import writable_dir
from ..security import sanitize_text

router = APIRouter(prefix="/api/scholars", tags=["scholars"])

DOC_DIR = writable_dir(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scholar_docs"),
    "mc_scholar_docs",
)
ALLOWED_DOC_MIMES = {"application/pdf", "image/png", "image/jpeg"}


def _scholar_out(u: models.User) -> dict:
    return {
        "id": u.id, "username": u.username, "display_name": u.display_name,
        "avatar_emoji": u.avatar_emoji, "country": u.country,
        "scholar_title": u.scholar_title, "scholar_bio": u.scholar_bio,
        "scholar_languages": u.scholar_languages, "scholar_specialties": u.scholar_specialties,
        "is_scholar_verified": u.is_scholar_verified,
    }


@router.post("/apply", status_code=201)
def apply(data: schemas.ScholarApplyIn, request: Request,
          user: models.User = Depends(require_registered), db: Session = Depends(get_db)):
    if user.is_scholar_verified:
        raise HTTPException(400, "You are already a verified scholar.")
    pending = db.query(models.ScholarApplication).filter(
        models.ScholarApplication.user_id == user.id,
        models.ScholarApplication.status == models.ScholarAppStatus.PENDING).first()
    if pending:
        raise HTTPException(400, "You already have a pending application.")
    app = models.ScholarApplication(
        user_id=user.id, full_name=sanitize_text(data.full_name, 120),
        biography=sanitize_text(data.biography, 5000), country=sanitize_text(data.country, 80),
        languages=sanitize_text(data.languages, 200), specialties=sanitize_text(data.specialties, 300),
        education=sanitize_text(data.education, 5000), credentials=sanitize_text(data.credentials, 5000),
    )
    db.add(app)
    db.commit()
    db.refresh(app)
    audit(db, user.id, "scholar_apply", f"application:{app.id}", ip=get_client_ip(request))
    # notify admins
    for admin in db.query(models.User).filter(models.User.role == models.Role.ADMIN).all():
        notify(db, admin.id, "scholar", "New scholar application",
               f"{data.full_name} applied for scholar verification.", "#/admin/scholars")
    return {"ok": True, "application_id": app.id}


@router.post("/apply/{app_id}/documents")
async def upload_documents(app_id: int, request: Request, files: list[UploadFile] = File(...),
                           user: models.User = Depends(require_registered), db: Session = Depends(get_db)):
    app = db.get(models.ScholarApplication, app_id)
    if not app or app.user_id != user.id:
        raise HTTPException(404, "Application not found.")
    if app.status != models.ScholarAppStatus.PENDING:
        raise HTTPException(400, "Application is no longer editable.")
    if len(files) > 5:
        raise HTTPException(400, "Maximum 5 documents.")
    paths: list[str] = json.loads(app.document_paths) if app.document_paths else []
    for f in files:
        if f.content_type not in ALLOWED_DOC_MIMES:
            raise HTTPException(400, f"Unsupported file type: {f.content_type}")
        data = await f.read()
        if len(data) > 10 * 1024 * 1024:
            raise HTTPException(400, "Document too large (max 10 MB).")
        fname = f"scholar_{app_id}_{secrets.token_hex(8)}"
        ext = ".pdf" if f.content_type == "application/pdf" else (".png" if f.content_type == "image/png" else ".jpg")
        with open(os.path.join(DOC_DIR, fname + ext), "wb") as fh:
            fh.write(data)
        paths.append(fname + ext)
    app.document_paths = json.dumps(paths)
    db.commit()
    return {"ok": True, "documents": len(paths)}


@router.get("/directory")
def directory(q: str | None = None, specialty: str | None = None, language: str | None = None,
              db: Session = Depends(get_db)):
    query = db.query(models.User).filter(models.User.is_scholar_verified.is_(True))
    if q:
        like = f"%{q}%"
        query = query.filter(or_(models.User.display_name.ilike(like), models.User.scholar_title.ilike(like)))
    if specialty:
        query = query.filter(models.User.scholar_specialties.ilike(f"%{specialty}%"))
    if language:
        query = query.filter(models.User.scholar_languages.ilike(f"%{language}%"))
    return {"scholars": [_scholar_out(u) for u in query.limit(100).all()],
            "disclaimer": "Verification confirms identity and stated credentials were reviewed by a human moderator. "
                          "It does not guarantee that every answer is correct. Always verify important religious "
                          "rulings with qualified scholars you trust."}


@router.get("/applications", dependencies=[Depends(require_admin)])
def list_applications(status: str | None = None, db: Session = Depends(get_db)):
    query = db.query(models.ScholarApplication).order_by(desc(models.ScholarApplication.created_at))
    if status:
        query = query.filter(models.ScholarApplication.status == status)
    out = []
    for a in query.limit(200).all():
        u = db.get(models.User, a.user_id)
        out.append({
            "id": a.id, "user_id": a.user_id, "username": u.username if u else "?",
            "full_name": a.full_name, "biography": a.biography, "country": a.country,
            "languages": a.languages, "specialties": a.specialties, "education": a.education,
            "credentials": a.credentials, "document_count": len(json.loads(a.document_paths)) if a.document_paths else 0,
            "status": a.status.value, "review_note": a.review_note, "created_at": a.created_at,
        })
    return {"applications": out}


@router.post("/applications/{app_id}/review")
def review_application(app_id: int, data: schemas.ScholarReviewIn, request: Request,
                       reviewer: models.User = Depends(require_admin), db: Session = Depends(get_db)):
    app = db.get(models.ScholarApplication, app_id)
    if not app:
        raise HTTPException(404, "Application not found.")
    app.reviewer_id = reviewer.id
    app.status = models.ScholarAppStatus(data.decision)
    app.review_note = sanitize_text(data.review_note, 2000)
    app.reviewed_at = dt.datetime.now(dt.timezone.utc)
    applicant = db.get(models.User, app.user_id)
    if data.decision == "approved" and applicant:
        applicant.is_scholar_verified = True
        applicant.role = models.Role.SCHOLAR
        applicant.scholar_title = app.full_name
        applicant.scholar_bio = app.biography
        applicant.scholar_languages = app.languages
        applicant.scholar_specialties = app.specialties
        notify(db, applicant.id, "scholar", "Scholar verification approved",
               "Your scholar application was approved after human review. Welcome!", "#/scholars")
    elif data.decision in ("rejected", "revoked") and applicant:
        applicant.is_scholar_verified = False
        if applicant.role == models.Role.SCHOLAR:
            applicant.role = models.Role.USER
        notify(db, applicant.id, "scholar", f"Scholar application {data.decision}",
               app.review_note or "Please contact support for details.", "#/scholars/apply")
    db.commit()
    audit(db, reviewer.id, "scholar_review", f"application:{app_id}:{data.decision}", ip=get_client_ip(request))
    return {"ok": True}
