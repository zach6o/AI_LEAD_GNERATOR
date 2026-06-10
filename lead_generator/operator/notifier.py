"""Outbound WhatsApp messages from the agent to the operator (you).

We reuse the same WhatsApp Business Cloud API + phone number ID that we use
for prospect outreach — the *only* difference is the recipient.

WhatsApp 24-hour window:
  - Once you send the bot any message, a 24h "customer service window" opens
    during which the bot can send free-form text/interactive messages back.
  - Outside the window, the bot must use an approved template. We try free-
    form first; if it fails with the recognizable error, we tell the caller
    so they can prompt you to /ping the bot to reopen the window.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

import httpx

from ..config import get_settings


GRAPH_URL = "https://graph.facebook.com/v21.0"


@dataclass
class NotifyResult:
    ok: bool
    provider_message_id: str | None = None
    error: str | None = None
    window_closed: bool = False


def _normalize_phone(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def _post(path: str, payload: dict) -> tuple[int, dict | str]:
    s = get_settings()
    headers = {
        "Authorization": f"Bearer {s.whatsapp_access_token}",
        "Content-Type": "application/json",
    }
    url = f"{GRAPH_URL}/{s.whatsapp_phone_number_id}/{path}"
    with httpx.Client(timeout=20.0) as http:
        r = http.post(url, headers=headers, json=payload)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text


def _is_window_closed_error(body: dict | str) -> bool:
    """Detect Meta's 'message outside the 24h window' error.

    Meta returns code 131047 ("Re-engagement message") for this case.
    """
    if not isinstance(body, dict):
        return False
    err = (body.get("error") or {})
    code = err.get("code")
    sub = (err.get("error_data") or {}).get("details", "") or ""
    msg = (err.get("message") or "").lower()
    if code == 131047:
        return True
    if "outside the allowed window" in msg or "re-engagement" in msg:
        return True
    if "24 hour" in sub.lower():
        return True
    return False


def _result_from_response(status: int, body: dict | str) -> NotifyResult:
    if status < 400 and isinstance(body, dict):
        wamid = (body.get("messages") or [{}])[0].get("id")
        return NotifyResult(ok=True, provider_message_id=wamid)
    window_closed = _is_window_closed_error(body)
    err_text = (
        body.get("error", {}).get("message")  # type: ignore[union-attr]
        if isinstance(body, dict)
        else str(body)
    )
    return NotifyResult(
        ok=False,
        error=f"{status}: {err_text}",
        window_closed=window_closed,
    )


def _operator_number() -> str | None:
    s = get_settings()
    num = _normalize_phone(s.operator_whatsapp_number)
    return num or None


def _preflight() -> NotifyResult | None:
    s = get_settings()
    if not s.whatsapp_access_token or not s.whatsapp_phone_number_id:
        return NotifyResult(ok=False, error="WhatsApp not configured (token/phone-id missing)")
    if not _operator_number():
        return NotifyResult(ok=False, error="OPERATOR_WHATSAPP_NUMBER not set")
    return None


# --------------------- public API ---------------------

def send_text(body: str) -> NotifyResult:
    """Send a free-form text message to the operator. Works inside the 24h window."""
    err = _preflight()
    if err:
        return err
    payload = {
        "messaging_product": "whatsapp",
        "to": _operator_number(),
        "type": "text",
        "text": {"preview_url": False, "body": body[:4096]},
    }
    return _result_from_response(*_post("messages", payload))


@dataclass
class Button:
    id: str          # opaque payload id, <= 256 chars, e.g. 'approve:<uuid>'
    title: str       # <= 20 chars displayed to the user


def send_buttons(body: str, buttons: Iterable[Button], header: str | None = None) -> NotifyResult:
    """Send an interactive message with up to 3 reply buttons.

    Inbound button taps come back via the webhook as:
        interactive.button_reply = {id, title}
    """
    err = _preflight()
    if err:
        return err

    btn_list = [
        {"type": "reply", "reply": {"id": b.id[:256], "title": b.title[:20]}}
        for b in list(buttons)[:3]
    ]
    interactive: dict = {
        "type": "button",
        "body": {"text": body[:1024]},
        "action": {"buttons": btn_list},
    }
    if header:
        interactive["header"] = {"type": "text", "text": header[:60]}

    payload = {
        "messaging_product": "whatsapp",
        "to": _operator_number(),
        "type": "interactive",
        "interactive": interactive,
    }
    return _result_from_response(*_post("messages", payload))


def is_window_closed(result: NotifyResult) -> bool:
    return not result.ok and result.window_closed
