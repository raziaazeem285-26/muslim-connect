"""Islamic knowledge Q&A center."""
from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import desc, func, or_
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import audit, get_client_ip, get_current_user, get_optional_user, notify, rate_limit, require_registered
from ..security import sanitize_text

router = APIRouter(prefix="/api/qa", tags=["qa"])

CATEGORIES = {"quran": "Quran & Tafsir", "hadith": "Hadith", "seerah": "Seerah & History",
              "fiqh": "Fiqh & Daily Life", "general": "General Community"}

KIND_LABELS = {"scripture": "Scriptural quotation", "translation": "Translation",
               "interpretation": "Interpretation", "opinion": "Personal opinion"}


def _question_out(db: Session, q: models.Question) -> schemas.QuestionOut:
    count = db.query(func.count(models.Answer.id)).filter(models.Answer.question_id == q.id).scalar() or 0
    return schemas.QuestionOut(
        id=q.id, title=q.title, body=q.body, category=q.category, tags=q.tags,
        author=schemas.UserOut.model_validate(db.get(models.User, q.author_id)),
        language=q.language, is_answered=q.is_answered, answer_count=count,
        view_count=q.view_count, created_at=q.created_at,
    )


def _answer_out(db: Session, a: models.Answer, me: models.User | None) -> schemas.AnswerOut:
    voted = False
    if me:
        voted = db.query(models.UsefulVote).filter(
            models.UsefulVote.answer_id == a.id, models.UsefulVote.user_id == me.id).first() is not None
    try:
        citations = json.loads(a.citations) if a.citations else []
    except Exception:
        citations = []
    return schemas.AnswerOut(
        id=a.id, question_id=a.question_id, author=schemas.UserOut.model_validate(db.get(models.User, a.author_id)),
        body=a.body, kind=a.kind, citations=citations, useful_count=a.useful_count,
        voted_useful=voted, created_at=a.created_at,
    )


@router.get("/questions", response_model=list[schemas.QuestionOut])
def list_questions(category: str | None = None, q: str | None = None, tag: str | None = None,
                   limit: int = Query(50, le=100), offset: int = 0, db: Session = Depends(get_db)):
    query = db.query(models.Question)
    if category:
        query = query.filter(models.Question.category == category)
    if tag:
        query = query.filter(models.Question.tags.ilike(f"%{tag}%"))
    if q:
        like = f"%{q}%"
        query = query.filter(or_(models.Question.title.ilike(like), models.Question.body.ilike(like)))
    items = query.order_by(desc(models.Question.created_at)).offset(offset).limit(limit).all()
    return [_question_out(db, x) for x in items]


@router.get("/categories")
def categories():
    return [{"id": k, "label": v} for k, v in CATEGORIES.items()]


@router.post("/questions", response_model=schemas.QuestionOut, status_code=201)
def ask_question(data: schemas.QuestionIn, request: Request,
                 user: models.User = Depends(require_registered), db: Session = Depends(get_db)):
    rate_limit(request, f"qa:{user.id}", 20)
    q = models.Question(
        title=sanitize_text(data.title, 200), body=sanitize_text(data.body, 10000),
        category=data.category, tags=sanitize_text(data.tags, 300),
        author_id=user.id, language=data.language or "en",
    )
    db.add(q)
    db.commit()
    db.refresh(q)
    audit(db, user.id, "question_ask", f"question:{q.id}", ip=get_client_ip(request))
    return _question_out(db, q)


@router.get("/questions/{qid}", response_model=schemas.QuestionOut)
def get_question(qid: int, db: Session = Depends(get_db)):
    q = db.get(models.Question, qid)
    if not q:
        raise HTTPException(404, "Question not found.")
    q.view_count += 1
    db.commit()
    return _question_out(db, q)


@router.get("/questions/{qid}/answers", response_model=list[schemas.AnswerOut])
def list_answers(qid: int, me: models.User | None = Depends(get_optional_user), db: Session = Depends(get_db)):
    q = db.get(models.Question, qid)
    if not q:
        raise HTTPException(404, "Question not found.")
    answers = db.query(models.Answer).filter(models.Answer.question_id == qid).order_by(
        desc(models.Answer.useful_count), models.Answer.created_at).all()
    return [_answer_out(db, a, me) for a in answers]


@router.post("/questions/{qid}/answers", response_model=schemas.AnswerOut, status_code=201)
def post_answer(qid: int, data: schemas.AnswerIn, request: Request,
                user: models.User = Depends(require_registered), db: Session = Depends(get_db)):
    rate_limit(request, f"qa:{user.id}", 20)
    q = db.get(models.Question, qid)
    if not q:
        raise HTTPException(404, "Question not found.")
    citations = []
    for c in data.citations:
        citations.append({"type": c.type, "text": sanitize_text(c.text, 2000),
                          "reference": sanitize_text(c.reference, 200), "verified": bool(c.verified)})
    a = models.Answer(question_id=qid, author_id=user.id, body=sanitize_text(data.body, 15000),
                      kind=data.kind, citations=json.dumps(citations))
    db.add(a)
    db.commit()
    db.refresh(a)
    # notify asker
    if q.author_id != user.id:
        asker = db.get(models.User, q.author_id)
        if asker and not asker.is_guest and asker.notify_answers:
            notify(db, asker.id, "answer", f"New answer to: {q.title[:80]}",
                   f"{user.display_name} answered your question.", f"#/qa/{qid}")
    # notify scholars watching the category (simple: all verified scholars)
    audit(db, user.id, "answer_post", f"answer:{a.id}", ip=get_client_ip(request))
    return _answer_out(db, a, user)


@router.post("/answers/{aid}/useful")
def vote_useful(aid: int, user: models.User = Depends(require_registered), db: Session = Depends(get_db)):
    a = db.get(models.Answer, aid)
    if not a:
        raise HTTPException(404, "Answer not found.")
    existing = db.query(models.UsefulVote).filter(
        models.UsefulVote.answer_id == aid, models.UsefulVote.user_id == user.id).first()
    if existing:
        db.delete(existing)
        a.useful_count = max(0, a.useful_count - 1)
    else:
        db.add(models.UsefulVote(answer_id=aid, user_id=user.id))
        a.useful_count += 1
    db.commit()
    return {"ok": True, "useful_count": a.useful_count}


@router.post("/questions/{qid}/accept/{aid}")
def accept_answer(qid: int, aid: int, user: models.User = Depends(require_registered), db: Session = Depends(get_db)):
    q = db.get(models.Question, qid)
    a = db.get(models.Answer, aid)
    if not q or not a or a.question_id != qid:
        raise HTTPException(404, "Not found.")
    if q.author_id != user.id and user.role != models.Role.ADMIN:
        raise HTTPException(403, "Only the asker can accept an answer.")
    q.accepted_answer_id = aid
    q.is_answered = True
    db.commit()
    return {"ok": True}


@router.get("/search")
def search_qa(q: str = Query(..., min_length=2), db: Session = Depends(get_db)):
    like = f"%{q}%"
    questions = db.query(models.Question).filter(
        or_(models.Question.title.ilike(like), models.Question.body.ilike(like))
    ).order_by(desc(models.Question.created_at)).limit(20).all()
    return {"questions": [_question_out(db, x) for x in questions]}


@router.get("/kinds")
def answer_kinds():
    return [{"id": k, "label": v} for k, v in KIND_LABELS.items()]
