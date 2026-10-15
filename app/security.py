"""Password hashing, JWT tokens, rate limiting helpers."""
from __future__ import annotations

import datetime as dt
import hashlib
import secrets
import time
from collections import defaultdict
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from .config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return pwd_context.verify(password, password_hash or "")
    except Exception:
        return False


def create_access_token(subject: str, extra: dict[str, Any] | None = None, minutes: int | None = None) -> str:
    expire = dt.datetime.now(dt.timezone.utc) + dt.timedelta(
        minutes=minutes if minutes is not None else settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload: dict[str, Any] = {"sub": subject, "exp": expire, "iat": dt.datetime.now(dt.timezone.utc)}
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None


def new_guest_key() -> str:
    return "g_" + secrets.token_urlsafe(24)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Simple in-memory sliding-window rate limiter (per key). For multi-worker
# production deployments, replace with Redis-backed limiting.
# ---------------------------------------------------------------------------
_hits: dict[str, list[float]] = defaultdict(list)


def rate_limit_ok(key: str, per_minute: int) -> bool:
    now = time.time()
    window_start = now - 60
    bucket = _hits[key]
    # prune
    while bucket and bucket[0] < window_start:
        bucket.pop(0)
    if len(bucket) >= per_minute:
        return False
    bucket.append(now)
    return True


def rate_limit_reset(key: str) -> None:
    _hits.pop(key, None)


# ---------------------------------------------------------------------------
# Content safety helpers
# ---------------------------------------------------------------------------
BANNED_PATTERNS = [
    # crude but effective first-pass abuse filter; human review decides the rest
    "http://bit.ly",  # placeholder example kept minimal; real list is configurable
]

SPAM_KEYWORDS = ["free money", "click here to win", "crypto double", "viagra", "xxx "]

import re  # noqa: E402


_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_MENTION_RE = re.compile(r"@([A-Za-z0-9_]{3,40})")


def extract_urls(text: str) -> list[str]:
    return _URL_RE.findall(text or "")


def extract_mentions(text: str) -> list[str]:
    return _MENTION_RE.findall(text or "")


def looks_like_spam(text: str) -> bool:
    t = (text or "").lower()
    if len(_URL_RE.findall(t)) >= 4:
        return True
    return any(k in t for k in SPAM_KEYWORDS)


def sanitize_text(text: str, max_len: int = 4000) -> str:
    """Trim and cap length. HTML-escaping happens at render time (frontend escapes)."""
    text = (text or "").strip()
    if len(text) > max_len:
        text = text[:max_len]
    return text
