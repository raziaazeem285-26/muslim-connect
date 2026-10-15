"""Muslim Connect — application configuration.

All secrets come from environment variables. See .env.example.
"""
import os
import secrets

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "Muslim Connect"
    TAGLINE: str = "One Ummah. One Community. Respect for All."
    ENV: str = "development"  # development | production
    SECRET_KEY: str = ""  # REQUIRED in production; generated ephemeral fallback in dev
    DATABASE_URL: str = "sqlite:///./muslim_connect.db"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 1 day
    GUEST_TOKEN_EXPIRE_MINUTES: int = 60 * 6
    GUEST_SESSION_TTL_HOURS: int = 72  # guest sessions expire per retention policy
    # Rate limits (per minute, per IP / per identity)
    RATE_LIMIT_AUTH_PER_MIN: int = 10
    RATE_LIMIT_MESSAGE_PER_MIN: int = 20
    RATE_LIMIT_API_PER_MIN: int = 120
    # Uploads
    MAX_UPLOAD_MB: int = 5
    ALLOWED_IMAGE_MIMES: str = "image/png,image/jpeg,image/webp,image/gif"
    # Email (optional SMTP for verification/reset; falls back to console log)
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "no-reply@muslimconnect.app"
    FRONTEND_URL: str = "http://localhost:8000"
    # First-run bootstrap admin (only used when no admin exists yet)
    BOOTSTRAP_ADMIN_EMAIL: str = ""
    BOOTSTRAP_ADMIN_PASSWORD: str = ""


settings = Settings()

if not settings.SECRET_KEY:
    if settings.ENV == "production":
        raise RuntimeError("SECRET_KEY must be set in production (see .env.example).")
    # Dev-only ephemeral key: sessions do not survive restarts, which is fine for dev.
    settings.SECRET_KEY = secrets.token_urlsafe(48)
