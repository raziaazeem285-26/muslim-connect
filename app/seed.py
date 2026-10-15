"""Seed default rooms, settings, and demo accounts (clearly fictional)."""
from __future__ import annotations

from sqlalchemy.orm import Session

from . import models
from .security import hash_password


DEFAULT_ROOMS = [
    # slug, name, description, kind, visibility, language, country, allow_guests
    ("global", "🌍 Global Ummah", "Everyone welcome — introduce yourself and say salam!", "global", "public", "en", "", True),
    ("quran-study", "📖 Quran Study Circle", "Read, reflect and discuss the Quran together.", "topic", "public", "en", "", True),
    ("hadith-corner", "📜 Hadith Corner", "Authentic hadith discussions with references.", "topic", "public", "en", "", True),
    ("seerah", "🕌 Seerah & Islamic History", "Life of the Prophet ﷺ and Islamic history.", "topic", "public", "en", "", True),
    ("new-muslims", "🤲 New Muslims Support", "A kind space for reverts and new learners. No question is too small.", "topic", "public", "en", "", True),
    ("sisters", "🧕 Sisters' Lounge", "A respectful space for sisters (registered members).", "topic", "members", "en", "", False),
    ("brothers", "🤝 Brothers' Circle", "A respectful space for brothers (registered members).", "topic", "members", "en", "", False),
    ("urdu", "🇵🇰 اردو کمیونٹی", "Urdu-speaking community room.", "language", "public", "ur", "", True),
    ("arabic", "🇸🇦 المجتمع العربي", "Arabic-speaking community room.", "language", "public", "ar", "", True),
    ("ask-scholars", "🎓 Ask a Scholar", "Questions answered by verified scholars.", "topic", "members", "en", "", False),
]

DEFAULT_SETTINGS = {
    "community_rules": (
        "1. No hate speech, threats, harassment, or bullying.\n"
        "2. No spam, scams, or unauthorized advertising.\n"
        "3. Respect all schools of thought; discuss differences kindly.\n"
        "4. Never share anyone's private information.\n"
        "5. Do not present fabricated quotes as Quran or Hadith — cite sources.\n"
        "6. No religious discrimination against any person or group.\n"
        "7. Moderators may warn, mute, suspend, or ban. Appeals are welcome."
    ),
    "announcement": "Assalamu alaikum! Welcome to Muslim Connect. Please read the community guidelines before posting. 🌙",
}


def seed(db: Session) -> None:
    for slug, name, desc, kind, vis, lang, country, guests in DEFAULT_ROOMS:
        if not db.query(models.Room).filter(models.Room.slug == slug).first():
            db.add(models.Room(
                slug=slug, name=name, description=desc,
                kind=models.RoomKind(kind), visibility=models.RoomVisibility(vis),
                language=lang, country=country, allow_guests=guests,
            ))
    for k, v in DEFAULT_SETTINGS.items():
        if not db.get(models.Setting, k):
            db.add(models.Setting(key=k, value=v))
    # Demo accounts — clearly fictional, for local testing only
    demos = [
        ("demo_scholar", "Dr. Aminah (Demo)", "scholar@example.com", models.Role.SCHOLAR, True),
        ("demo_moderator", "Bilal (Demo Mod)", "moderator@example.com", models.Role.MODERATOR, False),
        ("demo_user", "Fatima (Demo)", "user@example.com", models.Role.USER, False),
    ]
    for username, display, email, role, scholar in demos:
        if not db.query(models.User).filter(models.User.username == username).first():
            u = models.User(
                username=username, display_name=display, email=email,
                password_hash=hash_password("demo-password-123"),
                role=role, email_verified=True, country="Demo",
                is_scholar_verified=scholar,
                scholar_title="Demo Scholar of Islamic Studies" if scholar else "",
                scholar_specialties="Fiqh, Seerah" if scholar else "",
                scholar_languages="English, Urdu" if scholar else "",
            )
            db.add(u)
    db.commit()
