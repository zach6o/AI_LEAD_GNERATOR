"""WhatsApp sender via Meta WhatsApp Business Cloud API.

Cold outreach to a number outside the rolling 24h customer-service window
MUST use a pre-approved template. The template here has a single BODY
parameter — we ship the entire Agent-5-generated message as that parameter.

Meta API docs: https://developers.facebook.com/docs/whatsapp/cloud-api
"""
from __future__ import annotations

import re

import httpx

from ..config import get_settings
from .base import SendResult

GRAPH_URL = "https://graph.facebook.com/v21.0"


def _normalize_phone(phone: str) -> str:
    """Return digits-only number. Cloud API wants no '+', spaces or dashes.
    Example: '+91 89895 38512' -> '918989538512'."""
    return re.sub(r"\D", "", phone or "")


def send_whatsapp(
    *,
    to_phone: str,
    body: str,
) -> SendResult:
    s = get_settings()
    if not s.whatsapp_access_token or not s.whatsapp_phone_number_id:
        return SendResult(ok=False, error="WhatsApp not configured (set WHATSAPP_ACCESS_TOKEN + WHATSAPP_PHONE_NUMBER_ID)")
    if not to_phone:
        return SendResult(ok=False, error="prospect has no phone")

    number = _normalize_phone(to_phone)
    if len(number) < 10:
        return SendResult(ok=False, error=f"phone too short after normalization: {number!r}")

    payload = {
        "messaging_product": "whatsapp",
        "to": number,
        "type": "template",
        "template": {
            "name": s.whatsapp_template_name,
            "language": {"code": s.whatsapp_template_language},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": body}],
                }
            ],
        },
    }
    headers = {
        "Authorization": f"Bearer {s.whatsapp_access_token}",
        "Content-Type": "application/json",
    }
    url = f"{GRAPH_URL}/{s.whatsapp_phone_number_id}/messages"

    try:
        with httpx.Client(timeout=30.0) as http:
            r = http.post(url, headers=headers, json=payload)
        if r.status_code >= 400:
            return SendResult(ok=False, error=f"whatsapp {r.status_code}: {r.text[:300]}")
        data = r.json()
        wamid = (data.get("messages") or [{}])[0].get("id")
        return SendResult(ok=True, provider_message_id=wamid)
    except httpx.HTTPError as e:
        return SendResult(ok=False, error=f"http error: {type(e).__name__}: {e}")
