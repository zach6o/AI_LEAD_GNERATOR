"""Gmail IMAP poller for Agent 8 (Reply Monitor).

Why IMAP and not the Gmail API:
  - Reuses the same app password we already use for SMTP.
  - Zero OAuth setup; works for any Google account with app passwords enabled.

We poll UNSEEN messages in INBOX. After successful processing we mark them
read so the next poll only sees new mail. Each message is returned as a
ParsedMessage with headers + plain-text body.
"""
from __future__ import annotations

import email
import imaplib
from dataclasses import dataclass, field
from email.header import decode_header, make_header
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime
from datetime import datetime, timezone

from ..config import get_settings


@dataclass
class ParsedMessage:
    uid: str                       # IMAP UID (stable within a folder/uidvalidity)
    message_id: str | None         # RFC822 Message-ID header
    in_reply_to: str | None        # RFC822 In-Reply-To header
    references: list[str] = field(default_factory=list)
    from_address: str = ""
    from_name: str = ""
    to_addresses: list[str] = field(default_factory=list)
    subject: str = ""
    body_text: str = ""             # decoded text/plain body (or fallback HTML stripped)
    received_at: datetime | None = None
    raw_size_bytes: int = 0


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _addresses(value: str | None) -> list[tuple[str, str]]:
    if not value:
        return []
    return [(_decode(name), addr.lower()) for name, addr in getaddresses([value]) if addr]


def _body_text(msg: Message) -> str:
    """Pick the best plain-text body. Falls back to a naive HTML-strip."""
    if msg.is_multipart():
        parts: list[tuple[str, str]] = []
        for part in msg.walk():
            ctype = (part.get_content_type() or "").lower()
            if ctype.startswith("multipart/"):
                continue
            disp = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disp:
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, errors="replace")
            except LookupError:
                text = payload.decode("utf-8", errors="replace")
            parts.append((ctype, text))
        for ctype, text in parts:
            if ctype == "text/plain":
                return text
        for ctype, text in parts:
            if ctype == "text/html":
                return _strip_html(text)
        return ""
    # Single-part message.
    payload = msg.get_payload(decode=True)
    if not payload:
        return ""
    charset = msg.get_content_charset() or "utf-8"
    try:
        text = payload.decode(charset, errors="replace")
    except LookupError:
        text = payload.decode("utf-8", errors="replace")
    ctype = (msg.get_content_type() or "").lower()
    if ctype == "text/html":
        return _strip_html(text)
    return text


def _strip_html(html: str) -> str:
    try:
        from bs4 import BeautifulSoup
        return BeautifulSoup(html, "lxml").get_text("\n", strip=True)
    except Exception:
        return html


def _parse_received_at(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def _references(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [r.strip() for r in raw.split() if r.strip()]


def _parse(uid: str, raw_bytes: bytes) -> ParsedMessage:
    msg = email.message_from_bytes(raw_bytes)
    froms = _addresses(msg.get("From"))
    name, addr = froms[0] if froms else ("", "")
    tos = [a for _, a in _addresses(msg.get("To"))]

    body = _body_text(msg)

    return ParsedMessage(
        uid=uid,
        message_id=(msg.get("Message-ID") or "").strip() or None,
        in_reply_to=(msg.get("In-Reply-To") or "").strip() or None,
        references=_references(msg.get("References")),
        from_address=addr,
        from_name=name,
        to_addresses=tos,
        subject=_decode(msg.get("Subject")),
        body_text=body,
        received_at=_parse_received_at(msg.get("Date")),
        raw_size_bytes=len(raw_bytes),
    )


class ImapError(RuntimeError):
    pass


def poll_unread(*, max_messages: int | None = None, mark_seen: bool = True) -> list[ParsedMessage]:
    """Return new UNSEEN messages from the configured folder.

    Reuses the SMTP app password by default. Messages are marked \\Seen after
    successful parse unless `mark_seen=False`.
    """
    s = get_settings()
    user = s.effective_imap_user
    password = s.effective_imap_password
    if not user or not password:
        raise ImapError(
            "IMAP credentials missing. Set IMAP_USER + IMAP_PASSWORD or "
            "SENDER_EMAIL + GMAIL_APP_PASSWORD."
        )
    cap = max_messages or s.imap_max_per_poll

    try:
        conn = imaplib.IMAP4_SSL(s.imap_host, s.imap_port, timeout=30)
    except OSError as e:
        raise ImapError(f"connect failed: {e}") from e

    try:
        try:
            conn.login(user, password)
        except imaplib.IMAP4.error as e:
            raise ImapError(f"login failed (check app password): {e}") from e

        status, _ = conn.select(s.imap_folder, readonly=False)
        if status != "OK":
            raise ImapError(f"select {s.imap_folder!r} failed: {status}")

        status, data = conn.search(None, "UNSEEN")
        if status != "OK":
            raise ImapError(f"search failed: {status}")

        ids = data[0].split() if data and data[0] else []
        ids = ids[-cap:] if len(ids) > cap else ids   # most recent N

        out: list[ParsedMessage] = []
        for raw_id in ids:
            uid = raw_id.decode("ascii", errors="ignore")
            status, fetched = conn.fetch(raw_id, "(RFC822)")
            if status != "OK" or not fetched:
                continue
            for part in fetched:
                if isinstance(part, tuple) and len(part) >= 2:
                    raw_bytes = part[1]
                    if isinstance(raw_bytes, (bytes, bytearray)):
                        try:
                            parsed = _parse(uid, bytes(raw_bytes))
                            out.append(parsed)
                            if mark_seen:
                                conn.store(raw_id, "+FLAGS", r"(\Seen)")
                        except Exception:
                            # Bad message — skip and leave it unread for the operator to look at.
                            continue
                    break
        return out
    finally:
        try:
            conn.logout()
        except Exception:
            pass
