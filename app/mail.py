"""Email sending. Uses SMTP when configured; otherwise logs to console (dev fallback)."""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from .config import settings

log = logging.getLogger("muslim-connect.mail")


def send_email(to: str, subject: str, body: str) -> bool:
    """Returns True if handed off to SMTP; False if only logged (no SMTP configured)."""
    if not settings.SMTP_HOST:
        log.warning("[DEV EMAIL] to=%s subject=%s\n%s", to, subject, body)
        print(f"\n===== DEV EMAIL =====\nTo: {to}\nSubject: {subject}\n\n{body}\n=====================\n")
        return False
    msg = EmailMessage()
    msg["From"] = settings.SMTP_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as s:
            s.starttls()
            if settings.SMTP_USER:
                s.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            s.send_message(msg)
        return True
    except Exception as e:  # never crash the request on mail failure
        log.error("SMTP send failed: %s", e)
        return False


def verification_email(link: str) -> tuple[str, str]:
    return (
        "Verify your Muslim Connect account",
        f"Assalamu alaikum!\n\nPlease verify your email address by opening this link:\n{link}\n\n"
        "The link expires in 24 hours. If you did not create this account, please ignore this email.",
    )


def reset_email(link: str) -> tuple[str, str]:
    return (
        "Reset your Muslim Connect password",
        f"Assalamu alaikum!\n\nReset your password with this link:\n{link}\n\n"
        "The link expires in 1 hour. If you did not request this, please ignore this email.",
    )
