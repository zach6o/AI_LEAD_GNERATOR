"""WhatsApp Cloud API webhook receiver.

Meta verifies the webhook with a GET handshake, then posts inbound events
(messages, statuses) to the same URL. We:
  1. Reject requests whose X-Hub-Signature-256 doesn't match our app secret.
  2. Ignore messages from any number other than OPERATOR_WHATSAPP_NUMBER.
  3. Parse text and interactive button_reply payloads, hand off to
     operator.commands.handle_inbound, and send the reply back via the
     operator notifier.

Status callbacks ('delivered', 'read', errors) are accepted but ignored for
now — could be wired into outreach_messages.attempts later.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from typing import Any

from ..config import get_settings
from . import commands, notifier


def verify_handshake(*, mode: str | None, token: str | None, challenge: str | None) -> tuple[int, str]:
    """Implements the GET subscribe handshake: returns (status_code, body)."""
    s = get_settings()
    if not s.whatsapp_webhook_verify_token:
        return 500, "WHATSAPP_WEBHOOK_VERIFY_TOKEN not configured"
    if mode == "subscribe" and token == s.whatsapp_webhook_verify_token:
        return 200, challenge or ""
    return 403, "forbidden"


def verify_signature(body: bytes, header: str | None) -> bool:
    """Validate X-Hub-Signature-256: sha256=hex_digest."""
    s = get_settings()
    if not s.whatsapp_app_secret:
        # If unset, we *do not* accept (fail closed). Set it before going live.
        return False
    if not header or not header.startswith("sha256="):
        return False
    sent = header.split("=", 1)[1].strip().lower()
    mac = hmac.new(s.whatsapp_app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sent, mac)


def _is_operator(from_phone: str) -> bool:
    s = get_settings()
    expected = re.sub(r"\D", "", s.operator_whatsapp_number or "")
    actual = re.sub(r"\D", "", from_phone or "")
    return bool(expected) and expected == actual


def _extract_messages(payload: dict) -> list[dict]:
    """Pull message events out of the Cloud API webhook envelope."""
    out: list[dict] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            for m in value.get("messages") or []:
                out.append(m)
    return out


def _parse_message(m: dict) -> tuple[str, str | None, str | None]:
    """Return (from_phone, text, button_id)."""
    from_phone = m.get("from") or ""
    mtype = m.get("type")
    text: str | None = None
    button_id: str | None = None

    if mtype == "text":
        text = (m.get("text") or {}).get("body")
    elif mtype == "interactive":
        inter = m.get("interactive") or {}
        if inter.get("type") == "button_reply":
            button_id = (inter.get("button_reply") or {}).get("id")
        elif inter.get("type") == "list_reply":
            button_id = (inter.get("list_reply") or {}).get("id")
    elif mtype == "button":
        # Quick-reply template button.
        button_id = (m.get("button") or {}).get("payload")
    # Other types (image/audio/etc) deliberately not handled — operator only
    # talks to the bot in text + button taps.

    return from_phone, text, button_id


def handle_event(payload: dict) -> dict:
    """Process a webhook POST body. Returns a small audit dict for the response."""
    audit = {"processed": 0, "ignored": 0, "rejected_phone": 0}

    for m in _extract_messages(payload):
        from_phone, text, button_id = _parse_message(m)
        if not from_phone:
            audit["ignored"] += 1
            continue
        if not _is_operator(from_phone):
            audit["rejected_phone"] += 1
            continue
        if not (text or button_id):
            audit["ignored"] += 1
            continue

        try:
            reply = commands.handle_inbound(
                from_phone=from_phone, text=text, button_id=button_id, raw=m
            )
        except Exception as e:
            reply = f"⚠️ Internal error: {type(e).__name__}: {e}"

        # Send the reply back to the operator. Don't let send failure crash us.
        try:
            notifier.send_text(reply)
        except Exception:
            pass

        audit["processed"] += 1

    return audit
