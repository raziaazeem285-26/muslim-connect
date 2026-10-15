"""Pydantic request/response schemas."""
from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, EmailStr, Field, field_validator


# ---------------- Auth ----------------
class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_]+$")
    display_name: str = Field(min_length=1, max_length=80)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    country: str = Field(default="", max_length=80)
    language: str = Field(default="en", max_length=10)


class LoginIn(BaseModel):
    username_or_email: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserOut"


class GuestIn(BaseModel):
    display_name: str = Field(default="", max_length=80)
    language: str = Field(default="en", max_length=10)
    country: str = Field(default="", max_length=80)


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


# ---------------- Users ----------------
class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: str
    is_guest: bool
    email_verified: bool
    bio: str = ""
    country: str = ""
    language: str = "en"
    interests: str = ""
    avatar_emoji: str = "🕌"
    is_scholar_verified: bool = False
    scholar_title: str = ""
    online_visible: bool = True
    created_at: dt.datetime | None = None

    model_config = {"from_attributes": True}


class ProfileUpdateIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=80)
    bio: str | None = Field(default=None, max_length=1000)
    country: str | None = Field(default=None, max_length=80)
    language: str | None = Field(default=None, max_length=10)
    interests: str | None = Field(default=None, max_length=500)
    avatar_emoji: str | None = Field(default=None, max_length=16)
    online_visible: bool | None = None
    notify_messages: bool | None = None
    notify_answers: bool | None = None
    notify_moderation: bool | None = None
    theme: str | None = Field(default=None, max_length=10)
    ui_language: str | None = Field(default=None, max_length=10)


# ---------------- Rooms / chat ----------------
class RoomOut(BaseModel):
    id: int
    slug: str
    name: str
    description: str = ""
    kind: str
    visibility: str
    language: str = "en"
    country: str = ""
    allow_guests: bool = True
    member_count: int = 0
    unread: int = 0

    model_config = {"from_attributes": True}


class RoomCreateIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=2000)
    kind: str = Field(default="topic")
    visibility: str = Field(default="members")
    language: str = Field(default="en", max_length=10)
    country: str = Field(default="", max_length=80)
    allow_guests: bool = True


class MessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    reply_to_id: int | None = None
    client_uuid: str | None = Field(default=None, max_length=36)


class MessageEditIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class ReactionIn(BaseModel):
    emoji: str = Field(min_length=1, max_length=16)


class MessageOut(BaseModel):
    id: int
    uuid: str
    author: UserOut
    body: str
    reply_to_id: int | None = None
    edited_at: dt.datetime | None = None
    created_at: dt.datetime
    reactions: dict[str, int] = {}
    my_reactions: list[str] = []
    attachment_url: str | None = None
    attachment_mime: str | None = None
    attachment_name: str | None = None

    model_config = {"from_attributes": True}


class ConversationOut(BaseModel):
    id: int
    is_group: bool
    title: str
    participants: list[UserOut] = []
    last_message_at: dt.datetime | None = None
    unread: int = 0


class DMCreateIn(BaseModel):
    user_id: int


class GroupCreateIn(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    member_ids: list[int] = Field(default_factory=list, max_length=100)


# ---------------- Q&A ----------------
class CitationIn(BaseModel):
    type: str = Field(pattern=r"^(quran|hadith|other)$")
    text: str = Field(max_length=2000)
    reference: str = Field(max_length=200)
    verified: bool = False


class QuestionIn(BaseModel):
    title: str = Field(min_length=5, max_length=200)
    body: str = Field(min_length=10, max_length=10000)
    category: str = Field(pattern=r"^(quran|hadith|seerah|fiqh|general)$")
    tags: str = Field(default="", max_length=300)
    language: str = Field(default="en", max_length=10)


class AnswerIn(BaseModel):
    body: str = Field(min_length=10, max_length=15000)
    kind: str = Field(default="opinion", pattern=r"^(scripture|translation|interpretation|opinion)$")
    citations: list[CitationIn] = Field(default_factory=list, max_items=20)


class QuestionOut(BaseModel):
    id: int
    title: str
    body: str
    category: str
    tags: str
    author: UserOut
    language: str
    is_answered: bool
    answer_count: int = 0
    view_count: int = 0
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class AnswerOut(BaseModel):
    id: int
    question_id: int
    author: UserOut
    body: str
    kind: str
    citations: list[dict[str, Any]] = []
    useful_count: int = 0
    voted_useful: bool = False
    created_at: dt.datetime

    model_config = {"from_attributes": True}


# ---------------- Scholars ----------------
class ScholarApplyIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    biography: str = Field(default="", max_length=5000)
    country: str = Field(default="", max_length=80)
    languages: str = Field(default="", max_length=200)
    specialties: str = Field(default="", max_length=300)
    education: str = Field(default="", max_length=5000)
    credentials: str = Field(default="", max_length=5000)


class ScholarReviewIn(BaseModel):
    decision: str = Field(pattern=r"^(approved|rejected|revoked)$")
    review_note: str = Field(default="", max_length=2000)


# ---------------- Moderation ----------------
class ReportIn(BaseModel):
    target_type: str = Field(pattern=r"^(message|user|room|question|answer|group)$")
    target_id: int
    reason: str = Field(min_length=2, max_length=120)
    details: str = Field(default="", max_length=2000)


class ModActionIn(BaseModel):
    action: str = Field(pattern=r"^(warning|message_delete|room_mute|temp_suspend|perm_ban|unban)$")
    target_user_id: int | None = None
    target_message_id: int | None = None
    reason: str = Field(default="", max_length=2000)
    duration_hours: int | None = Field(default=None, ge=1, le=24 * 365)


class AppealIn(BaseModel):
    details: str = Field(min_length=5, max_length=2000)


# ---------------- Notifications ----------------
class NotificationOut(BaseModel):
    id: int
    kind: str
    title: str
    body: str = ""
    link: str = ""
    is_read: bool
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class BookmarkIn(BaseModel):
    target_type: str = Field(pattern=r"^(question|answer|message)$")
    target_id: int


class SettingIn(BaseModel):
    value: str = Field(max_length=10000)
