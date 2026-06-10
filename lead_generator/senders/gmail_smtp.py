"""Gmail sender via SMTP using an app password.

Why SMTP and not the Gmail API:
  - SMTP needs only an app password (16 chars) — no OAuth, no consent screen.
  - For a single-sender outreach tool this is the smallest moving part that
    actually delivers mail end-to-end through Gmail's MX servers.

If we later need read access (reply monitoring), we'll move to the Gmail API.
"""
from __future__ import annotations

import smtplib
from email.message import EmailMessage
from email.utils import make_msgid

from ..config import get_settings
from .base import SendResult

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def send_email(
    *,
    to_email: str,
    subject: str,
    body: str,
) -> SendResult:
    s = get_settings()
    if not s.sender_email:
        return SendResult(ok=False, error="SENDER_EMAIL not configured")
    if not s.gmail_app_password:
        return SendResult(ok=False, error="GMAIL_APP_PASSWORD not configured")
    if not to_email:
        return SendResult(ok=False, error="prospect has no email")

    msg = EmailMessage()
    msg["From"] = f"{s.sender_name} <{s.sender_email}>"
    msg["To"] = to_email
    msg["Subject"] = subject or "(no subject)"
    msg["Reply-To"] = s.sender_email
    msg["Message-ID"] = make_msgid(domain=(s.sender_email.split("@")[-1] or "gmail.com"))
    msg.set_content(body, subtype="plain", charset="utf-8")

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as conn:
            conn.ehlo()
            conn.starttls()
            conn.ehlo()
            conn.login(s.sender_email, s.gmail_app_password)
            conn.send_message(msg)
        return SendResult(ok=True, provider_message_id=msg["Message-ID"])
    except smtplib.SMTPAuthenticationError as e:
        return SendResult(ok=False, error=f"auth failed (check GMAIL_APP_PASSWORD): {e.smtp_code} {e.smtp_error!r}")
    except smtplib.SMTPException as e:
        return SendResult(ok=False, error=f"SMTP error: {type(e).__name__}: {e}")
    except OSError as e:
        return SendResult(ok=False, error=f"network error: {e}")
