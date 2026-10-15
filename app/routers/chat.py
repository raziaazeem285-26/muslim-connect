"""Rooms, messages (REST), reactions, uploads, DMs/groups, and real-time WebSocket."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import secrets
import uuid
from collections import defaultdict

from fastapi import (
    APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect,
)
from sqlalchemy import desc, func, or_
from sqlalchemy.orm import Session

from .. import models, schemas
from ..config import settings
from ..database import SessionLocal, get_db
from ..paths import writable_dir
from ..deps import (
    audit, get_client_ip, get_current_user, get_optional_user, notify, rate_limit, require_registered,
)
from ..security import decode_token, extract_mentions, looks_like_spam, sanitize_text

router = APIRouter(prefix="/api", tags=["chat"])

UPLOAD_DIR = writable_dir(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads"),
    "mc_uploads",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _room_out(db: Session, room: models.Room, user: models.User | None) -> schemas.RoomOut:
    member_count = db.query(func.count(models.RoomMembership.id)).filter(
        models.RoomMembership.room_id == room.id).scalar() or 0
    unread = 0
    if user:
        mem = db.query(models.RoomMembership).filter(
            models.RoomMembership.room_id == room.id, models.RoomMembership.user_id == user.id).first()
        since = mem.last_read_at if mem and mem.last_read_at else dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
        unread = db.query(func.count(models.Message.id)).filter(
            models.Message.room_id == room.id,
            models.Message.created_at > since,
            models.Message.author_id != user.id,
            models.Message.deleted_at.is_(None),
        ).scalar() or 0
    return schemas.RoomOut(
        id=room.id, slug=room.slug, name=room.name, description=room.description,
        kind=room.kind.value, visibility=room.visibility.value, language=room.language,
        country=room.country, allow_guests=room.allow_guests,
        member_count=member_count, unread=unread,
    )


def _can_access_room(db: Session, room: models.Room, user: models.User | None) -> bool:
    if not room.is_active:
        return False
    if room.visibility == models.RoomVisibility.PUBLIC:
        if user and user.is_guest and not room.allow_guests:
            return False
        return True
    if user is None:
        return False
    if user.is_guest:
        return False
    return True  # members/private handled by membership check below


def _require_room_access(db: Session, room: models.Room, user: models.User | None) -> models.RoomMembership | None:
    if room.visibility == models.RoomVisibility.PUBLIC:
        if user and user.is_guest and not room.allow_guests:
            raise HTTPException(403, "Guests cannot access this room.")
        return None
    if user is None or user.is_guest:
        raise HTTPException(401, "Please sign in to access this room.")
    mem = db.query(models.RoomMembership).filter(
        models.RoomMembership.room_id == room.id, models.RoomMembership.user_id == user.id).first()
    if not mem:
        raise HTTPException(403, "You are not a member of this room.")
    if mem.muted_until and mem.muted_until.replace(tzinfo=dt.timezone.utc) > dt.datetime.now(dt.timezone.utc):
        raise HTTPException(403, "You are muted in this room.")
    return mem


def _message_out(db: Session, m: models.Message, me: models.User | None) -> schemas.MessageOut:
    reactions: dict[str, int] = {}
    my: list[str] = []
    for r in m.reactions:
        reactions[r.emoji] = reactions.get(r.emoji, 0) + 1
        if me and r.user_id == me.id:
            my.append(r.emoji)
    body = m.body if not m.deleted_at else "[message deleted]"
    return schemas.MessageOut(
        id=m.id, uuid=m.uuid, author=schemas.UserOut.model_validate(m.author),
        body=body, reply_to_id=m.reply_to_id, edited_at=m.edited_at, created_at=m.created_at,
        reactions=reactions, my_reactions=my,
        attachment_url=f"/uploads/{os.path.basename(m.attachment_path)}" if m.attachment_path else None,
        attachment_mime=m.attachment_mime, attachment_name=m.attachment_name,
    )


def _check_banned(user: models.User):
    now = dt.datetime.now(dt.timezone.utc)
    if user.is_banned:
        raise HTTPException(403, "Account is banned.")
    if user.banned_until and user.banned_until.replace(tzinfo=dt.timezone.utc) > now:
        raise HTTPException(403, "Account is temporarily suspended.")


# ---------------------------------------------------------------------------
# Real-time hub (in-process pub/sub; swap for Redis in multi-worker prod)
# ---------------------------------------------------------------------------
class Hub:
    def __init__(self):
        self.room_sockets: dict[int, set[WebSocket]] = defaultdict(set)
        self.conv_sockets: dict[int, set[WebSocket]] = defaultdict(set)
        self.presence: dict[int, dict[int, dt.datetime]] = defaultdict(dict)  # room_id -> {user_id: last_seen}
        self.lock = asyncio.Lock()

    async def join_room(self, room_id: int, ws: WebSocket):
        async with self.lock:
            self.room_sockets[room_id].add(ws)

    async def leave_room(self, room_id: int, ws: WebSocket):
        async with self.lock:
            self.room_sockets[room_id].discard(ws)

    async def join_conv(self, conv_id: int, ws: WebSocket):
        async with self.lock:
            self.conv_sockets[conv_id].add(ws)

    async def leave_conv(self, conv_id: int, ws: WebSocket):
        async with self.lock:
            self.conv_sockets[conv_id].discard(ws)

    async def broadcast_room(self, room_id: int, payload: dict, exclude: WebSocket | None = None):
        async with self.lock:
            targets = [w for w in list(self.room_sockets[room_id]) if w is not exclude]
        data = json.dumps(payload, default=str)
        for w in targets:
            try:
                await w.send_text(data)
            except Exception:
                pass

    async def broadcast_conv(self, conv_id: int, payload: dict, exclude: WebSocket | None = None):
        async with self.lock:
            targets = [w for w in list(self.conv_sockets[conv_id]) if w is not exclude]
        data = json.dumps(payload, default=str)
        for w in targets:
            try:
                await w.send_text(data)
            except Exception:
                pass

    def touch_presence(self, room_id: int, user_id: int):
        self.presence[room_id][user_id] = dt.datetime.now(dt.timezone.utc)

    def online_in_room(self, room_id: int) -> list[int]:
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=90)
        return [uid for uid, ts in self.presence.get(room_id, {}).items() if ts > cutoff]


hub = Hub()


# ---------------------------------------------------------------------------
# Rooms
# ---------------------------------------------------------------------------
@router.get("/rooms", response_model=list[schemas.RoomOut])
def list_rooms(kind: str | None = None, q: str | None = None,
               user: models.User | None = Depends(get_optional_user),
               db: Session = Depends(get_db)):
    query = db.query(models.Room).filter(models.Room.is_active.is_(True))
    if kind:
        query = query.filter(models.Room.kind == kind)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(models.Room.name.ilike(like), models.Room.description.ilike(like)))
    rooms = query.order_by(models.Room.kind, models.Room.name).limit(200).all()
    out = []
    for r in rooms:
        if r.visibility != models.RoomVisibility.PUBLIC and (not user or user.is_guest):
            continue
        out.append(_room_out(db, r, user))
    return out


@router.get("/rooms/{slug}", response_model=schemas.RoomOut)
def get_room(slug: str, user: models.User | None = Depends(get_optional_user), db: Session = Depends(get_db)):
    room = db.query(models.Room).filter(models.Room.slug == slug, models.Room.is_active.is_(True)).first()
    if not room or not _can_access_room(db, room, user):
        raise HTTPException(404, "Room not found.")
    return _room_out(db, room, user)


@router.post("/rooms", response_model=schemas.RoomOut, status_code=201)
def create_room(data: schemas.RoomCreateIn, request: Request,
                user: models.User = Depends(require_registered), db: Session = Depends(get_db)):
    rate_limit(request, "api", settings.RATE_LIMIT_API_PER_MIN)
    slug_base = "".join(c.lower() if c.isalnum() else "-" for c in data.name).strip("-")[:60] or "room"
    slug = slug_base
    i = 2
    while db.query(models.Room).filter(models.Room.slug == slug).first():
        slug = f"{slug_base}-{i}"
        i += 1
    try:
        kind = models.RoomKind(data.kind)
        visibility = models.RoomVisibility(data.visibility)
    except ValueError:
        raise HTTPException(400, "Invalid kind or visibility.")
    if visibility == models.RoomVisibility.PUBLIC and user.role not in (models.Role.ADMIN, models.Role.MODERATOR):
        raise HTTPException(403, "Only moderators can create public rooms.")
    room = models.Room(
        slug=slug, name=sanitize_text(data.name, 120), description=sanitize_text(data.description, 2000),
        kind=kind, visibility=visibility, language=data.language or "en",
        country=sanitize_text(data.country, 80), allow_guests=data.allow_guests, created_by_id=user.id,
    )
    db.add(room)
    db.commit()
    db.refresh(room)
    db.add(models.RoomMembership(room_id=room.id, user_id=user.id, is_moderator=True))
    db.commit()
    audit(db, user.id, "room_create", f"room:{room.id}", ip=get_client_ip(request))
    return _room_out(db, room, user)


@router.post("/rooms/{slug}/join")
def join_room(slug: str, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    room = db.query(models.Room).filter(models.Room.slug == slug, models.Room.is_active.is_(True)).first()
    if not room or not _can_access_room(db, room, user):
        raise HTTPException(404, "Room not found.")
    if room.visibility == models.RoomVisibility.PRIVATE:
        raise HTTPException(403, "This room is invite-only.")
    mem = db.query(models.RoomMembership).filter(
        models.RoomMembership.room_id == room.id, models.RoomMembership.user_id == user.id).first()
    if not mem:
        db.add(models.RoomMembership(room_id=room.id, user_id=user.id))
        db.commit()
    return {"ok": True}


@router.post("/rooms/{slug}/leave")
def leave_room(slug: str, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    room = db.query(models.Room).filter(models.Room.slug == slug).first()
    if not room:
        raise HTTPException(404, "Room not found.")
    db.query(models.RoomMembership).filter(
        models.RoomMembership.room_id == room.id, models.RoomMembership.user_id == user.id).delete()
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Messages (REST)
# ---------------------------------------------------------------------------
def _persist_message(db: Session, author: models.User, body: str, room_id: int | None,
                     conv_id: int | None, reply_to_id: int | None, client_uuid: str | None,
                     attachment: tuple[str, str, str] | None = None) -> models.Message:
    body = sanitize_text(body)
    if not body and not attachment:
        raise HTTPException(400, "Message body is empty.")
    if client_uuid:
        existing = db.query(models.Message).filter(models.Message.uuid == client_uuid).first()
        if existing:
            return existing  # idempotent retry
    if looks_like_spam(body):
        raise HTTPException(400, "Message looks like spam and was blocked.")
    if reply_to_id:
        parent = db.get(models.Message, reply_to_id)
        if not parent or parent.deleted_at:
            raise HTTPException(400, "Replied message not found.")
        if room_id and parent.room_id != room_id:
            raise HTTPException(400, "Reply must be in the same room.")
        if conv_id and parent.conversation_id != conv_id:
            raise HTTPException(400, "Reply must be in the same conversation.")
    m = models.Message(
        uuid=client_uuid or str(uuid.uuid4()),
        author_id=author.id, room_id=room_id, conversation_id=conv_id,
        body=body, reply_to_id=reply_to_id,
    )
    if attachment:
        m.attachment_path, m.attachment_mime, m.attachment_name = attachment
    db.add(m)
    db.commit()
    db.refresh(m)
    # mentions -> notifications
    for uname in set(extract_mentions(body)):
        target = db.query(models.User).filter(models.User.username.ilike(uname)).first()
        if target and target.id != author.id and not target.is_guest and target.notify_messages:
            notify(db, target.id, "mention", f"{author.display_name} mentioned you",
                   body[:200], f"#/room/{room_id}" if room_id else f"#/messages/{conv_id}")
    return m


@router.get("/rooms/{slug}/messages", response_model=list[schemas.MessageOut])
def room_messages(slug: str, before_id: int | None = None, limit: int = Query(50, le=100),
                  user: models.User | None = Depends(get_optional_user), db: Session = Depends(get_db)):
    room = db.query(models.Room).filter(models.Room.slug == slug, models.Room.is_active.is_(True)).first()
    if not room or not _can_access_room(db, room, user):
        raise HTTPException(404, "Room not found.")
    _require_room_access(db, room, user)
    q = db.query(models.Message).filter(models.Message.room_id == room.id,
                                        models.Message.deleted_at.is_(None))
    if before_id:
        q = q.filter(models.Message.id < before_id)
    msgs = q.order_by(desc(models.Message.id)).limit(limit).all()
    msgs.reverse()
    # mark read
    if user:
        mem = db.query(models.RoomMembership).filter(
            models.RoomMembership.room_id == room.id, models.RoomMembership.user_id == user.id).first()
        if mem:
            mem.last_read_at = dt.datetime.now(dt.timezone.utc)
            db.commit()
    return [_message_out(db, m, user) for m in msgs]


@router.post("/rooms/{slug}/messages", response_model=schemas.MessageOut, status_code=201)
async def post_room_message(slug: str, data: schemas.MessageIn, request: Request,
                            user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    rate_limit(request, f"msg:{user.id}", settings.RATE_LIMIT_MESSAGE_PER_MIN)
    _check_banned(user)
    room = db.query(models.Room).filter(models.Room.slug == slug, models.Room.is_active.is_(True)).first()
    if not room or not _can_access_room(db, room, user):
        raise HTTPException(404, "Room not found.")
    mem = _require_room_access(db, room, user)
    if room.slow_mode_seconds and mem:
        last = db.query(models.Message).filter(
            models.Message.room_id == room.id, models.Message.author_id == user.id,
            models.Message.deleted_at.is_(None)).order_by(desc(models.Message.id)).first()
        if last:
            delta = (dt.datetime.now(dt.timezone.utc) - last.created_at.replace(tzinfo=dt.timezone.utc)).total_seconds()
            if delta < room.slow_mode_seconds:
                raise HTTPException(429, f"Slow mode: wait {int(room.slow_mode_seconds - delta)}s.")
    # guest message cap per session
    if user.is_guest:
        count = db.query(func.count(models.Message.id)).filter(models.Message.author_id == user.id).scalar() or 0
        if count >= 100:
            raise HTTPException(403, "Guest message limit reached. Create a free account to continue.")
    m = _persist_message(db, user, data.body, room.id, None, data.reply_to_id, data.client_uuid)
    if user:  # auto-join public rooms on first message (idempotent)
        exists = db.query(models.RoomMembership).filter(
            models.RoomMembership.room_id == room.id, models.RoomMembership.user_id == user.id).first()
        if not exists:
            db.add(models.RoomMembership(room_id=room.id, user_id=user.id))
            db.commit()
    out = _message_out(db, m, user)
    await hub.broadcast_room(room.id, {"type": "message", "room_id": room.id, "message": json.loads(out.model_dump_json())})
    hub.touch_presence(room.id, user.id)
    return out


@router.patch("/messages/{msg_id}", response_model=schemas.MessageOut)
def edit_message(msg_id: int, data: schemas.MessageEditIn,
                 user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    m = db.get(models.Message, msg_id)
    if not m or m.deleted_at:
        raise HTTPException(404, "Message not found.")
    if m.author_id != user.id:
        raise HTTPException(403, "You can only edit your own messages.")
    age = (dt.datetime.now(dt.timezone.utc) - m.created_at.replace(tzinfo=dt.timezone.utc)).total_seconds()
    if age > 15 * 60:
        raise HTTPException(403, "Edit window (15 min) has passed.")
    m.body = sanitize_text(data.body)
    m.edited_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    return _message_out(db, m, user)


@router.delete("/messages/{msg_id}")
async def delete_message(msg_id: int, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    m = db.get(models.Message, msg_id)
    if not m or m.deleted_at:
        raise HTTPException(404, "Message not found.")
    is_mod = user.role in (models.Role.ADMIN, models.Role.MODERATOR)
    if m.author_id != user.id and not is_mod:
        raise HTTPException(403, "You can only delete your own messages.")
    m.deleted_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    payload = {"type": "message_deleted", "message_id": m.id}
    if m.room_id:
        await hub.broadcast_room(m.room_id, payload)
    elif m.conversation_id:
        await hub.broadcast_conv(m.conversation_id, payload)
    return {"ok": True}


@router.post("/messages/{msg_id}/reactions")
async def toggle_reaction(msg_id: int, data: schemas.ReactionIn,
                          user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    m = db.get(models.Message, msg_id)
    if not m or m.deleted_at:
        raise HTTPException(404, "Message not found.")
    existing = db.query(models.Reaction).filter(
        models.Reaction.message_id == msg_id, models.Reaction.user_id == user.id,
        models.Reaction.emoji == data.emoji).first()
    if existing:
        db.delete(existing)
    else:
        db.add(models.Reaction(message_id=msg_id, user_id=user.id, emoji=data.emoji[:16]))
    db.commit()
    payload = {"type": "reaction", "message_id": msg_id}
    if m.room_id:
        await hub.broadcast_room(m.room_id, payload)
    elif m.conversation_id:
        await hub.broadcast_conv(m.conversation_id, payload)
    return {"ok": True}


@router.post("/rooms/{slug}/upload", status_code=201)
async def upload_image(slug: str, request: Request, file: UploadFile = File(...),
                       user: models.User = Depends(require_registered), db: Session = Depends(get_db)):
    rate_limit(request, f"upload:{user.id}", 10)
    room = db.query(models.Room).filter(models.Room.slug == slug, models.Room.is_active.is_(True)).first()
    if not room or not _can_access_room(db, room, user):
        raise HTTPException(404, "Room not found.")
    _require_room_access(db, room, user)
    allowed = [m.strip() for m in settings.ALLOWED_IMAGE_MIMES.split(",")]
    if file.content_type not in allowed:
        raise HTTPException(400, f"Only image uploads allowed: {', '.join(allowed)}")
    data = await file.read()
    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(400, f"File too large (max {settings.MAX_UPLOAD_MB} MB).")
    # magic-byte validation
    sigs = {b"\xff\xd8\xff": ".jpg", b"\x89PNG": ".png", b"GIF8": ".gif", b"RIFF": ".webp"}
    ext = next((e for s, e in sigs.items() if data.startswith(s)), None)
    if not ext:
        raise HTTPException(400, "File content does not look like an image.")
    if ext == ".webp" and not data[8:12] == b"WEBP":
        raise HTTPException(400, "Invalid image file.")
    fname = f"{secrets.token_hex(16)}{ext}"
    with open(os.path.join(UPLOAD_DIR, fname), "wb") as f:
        f.write(data)
    m = _persist_message(db, user, "", room.id, None, None, None,
                         attachment=(os.path.join(UPLOAD_DIR, fname), file.content_type, file.filename[:200]))
    out = _message_out(db, m, user)
    await hub.broadcast_room(room.id, {"type": "message", "room_id": room.id, "message": json.loads(out.model_dump_json())})
    return {"ok": True, "message": json.loads(out.model_dump_json())}


# ---------------------------------------------------------------------------
# DMs & groups
# ---------------------------------------------------------------------------
def _conv_out(db: Session, conv: models.Conversation, user: models.User) -> schemas.ConversationOut:
    parts = [schemas.UserOut.model_validate(db.get(models.User, p.user_id)) for p in conv.participants]
    last = db.query(models.Message).filter(
        models.Message.conversation_id == conv.id, models.Message.deleted_at.is_(None)
    ).order_by(desc(models.Message.id)).first()
    me_part = next((p for p in conv.participants if p.user_id == user.id), None)
    unread = 0
    if me_part:
        since = me_part.last_read_at or dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
        unread = db.query(func.count(models.Message.id)).filter(
            models.Message.conversation_id == conv.id, models.Message.created_at > since,
            models.Message.author_id != user.id, models.Message.deleted_at.is_(None)).scalar() or 0
    title = conv.title
    if not conv.is_group:
        other = next((p for p in parts if p.id != user.id), None)
        title = other.display_name if other else title
    return schemas.ConversationOut(id=conv.id, is_group=conv.is_group, title=title,
                                   participants=parts, last_message_at=last.created_at if last else None,
                                   unread=unread)


def _get_conv_for(db: Session, conv_id: int, user: models.User) -> models.Conversation:
    conv = db.get(models.Conversation, conv_id)
    if not conv:
        raise HTTPException(404, "Conversation not found.")
    if not any(p.user_id == user.id for p in conv.participants):
        raise HTTPException(403, "You are not part of this conversation.")
    return conv


@router.get("/conversations", response_model=list[schemas.ConversationOut])
def list_conversations(user: models.User = Depends(require_registered), db: Session = Depends(get_db)):
    convs = (db.query(models.Conversation)
             .join(models.ConversationParticipant,
                   models.ConversationParticipant.conversation_id == models.Conversation.id)
             .filter(models.ConversationParticipant.user_id == user.id)
             .order_by(desc(models.Conversation.id)).limit(100).all())
    return [_conv_out(db, c, user) for c in convs]


@router.post("/conversations/dm", response_model=schemas.ConversationOut, status_code=201)
def start_dm(data: schemas.DMCreateIn, user: models.User = Depends(require_registered), db: Session = Depends(get_db)):
    if data.user_id == user.id:
        raise HTTPException(400, "You cannot message yourself.")
    other = db.get(models.User, data.user_id)
    if not other or other.is_guest:
        raise HTTPException(404, "User not found.")
    blocked = db.query(models.Block).filter(
        or_((models.Block.blocker_id == user.id) & (models.Block.blocked_id == other.id),
            (models.Block.blocker_id == other.id) & (models.Block.blocked_id == user.id))).first()
    if blocked:
        raise HTTPException(403, "This conversation is not available.")
    # find existing 1:1
    existing = (db.query(models.Conversation)
                .join(models.ConversationParticipant,
                      models.ConversationParticipant.conversation_id == models.Conversation.id)
                .filter(models.Conversation.is_group.is_(False),
                        models.ConversationParticipant.user_id.in_([user.id, other.id]))
                .group_by(models.Conversation.id)
                .having(func.count(models.ConversationParticipant.id) == 2).first())
    if existing:
        return _conv_out(db, existing, user)
    conv = models.Conversation(is_group=False, created_by_id=user.id)
    db.add(conv)
    db.commit()
    db.refresh(conv)
    db.add_all([models.ConversationParticipant(conversation_id=conv.id, user_id=user.id),
                models.ConversationParticipant(conversation_id=conv.id, user_id=other.id)])
    db.commit()
    return _conv_out(db, conv, user)


@router.post("/conversations/group", response_model=schemas.ConversationOut, status_code=201)
def create_group(data: schemas.GroupCreateIn, user: models.User = Depends(require_registered), db: Session = Depends(get_db)):
    member_ids = {i for i in data.member_ids if i != user.id}
    others = db.query(models.User).filter(models.User.id.in_(member_ids)).all()
    if any(u.is_guest for u in others):
        raise HTTPException(400, "Guests cannot join group chats.")
    conv = models.Conversation(is_group=True, title=sanitize_text(data.title, 120), created_by_id=user.id)
    db.add(conv)
    db.commit()
    db.refresh(conv)
    db.add(models.ConversationParticipant(conversation_id=conv.id, user_id=user.id, is_admin=True))
    for u in others:
        db.add(models.ConversationParticipant(conversation_id=conv.id, user_id=u.id))
    db.commit()
    return _conv_out(db, conv, user)


@router.get("/conversations/{conv_id}/messages", response_model=list[schemas.MessageOut])
def conv_messages(conv_id: int, before_id: int | None = None, limit: int = Query(50, le=100),
                  user: models.User = Depends(require_registered), db: Session = Depends(get_db)):
    conv = _get_conv_for(db, conv_id, user)
    q = db.query(models.Message).filter(models.Message.conversation_id == conv.id,
                                        models.Message.deleted_at.is_(None))
    if before_id:
        q = q.filter(models.Message.id < before_id)
    msgs = q.order_by(desc(models.Message.id)).limit(limit).all()
    msgs.reverse()
    part = next(p for p in conv.participants if p.user_id == user.id)
    part.last_read_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    return [_message_out(db, m, user) for m in msgs]


@router.post("/conversations/{conv_id}/messages", response_model=schemas.MessageOut, status_code=201)
async def post_conv_message(conv_id: int, data: schemas.MessageIn, request: Request,
                            user: models.User = Depends(require_registered), db: Session = Depends(get_db)):
    rate_limit(request, f"msg:{user.id}", settings.RATE_LIMIT_MESSAGE_PER_MIN)
    _check_banned(user)
    conv = _get_conv_for(db, conv_id, user)
    m = _persist_message(db, user, data.body, None, conv.id, data.reply_to_id, data.client_uuid)
    out = _message_out(db, m, user)
    await hub.broadcast_conv(conv.id, {"type": "message", "conversation_id": conv.id,
                                       "message": json.loads(out.model_dump_json())})
    for p in conv.participants:
        if p.user_id != user.id:
            pu = db.get(models.User, p.user_id)
            if pu and pu.notify_messages:
                notify(db, p.user_id, "message", f"New message from {user.display_name}",
                       m.body[:200], f"#/messages/{conv.id}")
    return out


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------
@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket, token: str = Query("")):
    await websocket.accept()
    payload = decode_token(token)
    if not payload:
        await websocket.send_text(json.dumps({"type": "error", "message": "Authentication required."}))
        await websocket.close()
        return
    db = SessionLocal()
    try:
        from ..deps import _load_user  # local import to avoid cycles
        try:
            user = _load_user(db, payload)
        except HTTPException as e:
            await websocket.send_text(json.dumps({"type": "error", "message": e.detail}))
            await websocket.close()
            return
        joined_rooms: set[int] = set()
        joined_convs: set[int] = set()
        await websocket.send_text(json.dumps({"type": "hello", "user_id": user.id}))
        while True:
            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                break
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            kind = msg.get("type")
            if kind == "join_room":
                room = db.query(models.Room).filter(models.Room.slug == msg.get("slug")).first()
                if room and _can_access_room(db, room, user):
                    try:
                        _require_room_access(db, room, user)
                    except HTTPException:
                        await websocket.send_text(json.dumps({"type": "error", "message": "Access denied."}))
                        continue
                    await hub.join_room(room.id, websocket)
                    joined_rooms.add(room.id)
                    hub.touch_presence(room.id, user.id)
                    await websocket.send_text(json.dumps({
                        "type": "presence", "room_id": room.id,
                        "online": hub.online_in_room(room.id)}))
            elif kind == "leave_room":
                rid = msg.get("room_id")
                if rid in joined_rooms:
                    await hub.leave_room(rid, websocket)
                    joined_rooms.discard(rid)
            elif kind == "join_conv":
                try:
                    conv = _get_conv_for(db, int(msg.get("conversation_id", 0)), user)
                except HTTPException:
                    continue
                await hub.join_conv(conv.id, websocket)
                joined_convs.add(conv.id)
            elif kind == "typing":
                rid, cid = msg.get("room_id"), msg.get("conversation_id")
                payload_out = {"type": "typing", "user_id": user.id,
                               "display_name": user.display_name, "room_id": rid, "conversation_id": cid}
                if rid and rid in joined_rooms:
                    hub.touch_presence(rid, user.id)
                    await hub.broadcast_room(rid, payload_out, exclude=websocket)
                elif cid and cid in joined_convs:
                    await hub.broadcast_conv(cid, payload_out, exclude=websocket)
            elif kind == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    finally:
        for rid in list(joined_rooms):
            await hub.leave_room(rid, websocket)
        for cid in list(joined_convs):
            await hub.leave_conv(cid, websocket)
        db.close()
