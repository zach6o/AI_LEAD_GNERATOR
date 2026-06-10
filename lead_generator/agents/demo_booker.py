"""Agent 9 — Demo Booker.

When Agent 8 flags a reply as 'interested' (or 'question'), Demo Booker:
  1. Drafts a short reply that offers the calendar booking link.
  2. Persists it as an outreach_messages row (channel='email', kind='demo_invite').
  3. Asks the operator for approval via WhatsApp.
  4. (Later) On 'approved', the sender ships it and the prospect stage bumps
     to 'demo_scheduled'.

We never send the booking link automatically. The operator decides whether
the reply is genuinely interested or just a casual question.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import get_settings
from ..db import client
from ..operator import approvals


@dataclass
class DemoDraft:
    reply_id: str
    prospect_id: str
    message_id: str | None
    approval_id: str | None
    skipped_reason: str | None = None


def _fetch_interested_replies(limit: int) -> list[dict]:
    return (
        client()
        .table("prospect_replies")
        .select(
            "id, prospect_id, body, subject, intent, "
            "prospects(id, business_name, email, stage, location, industry, "
            "opportunities(primary_service, monthly_revenue_impact_inr, pitch_summary))"
        )
        .in_("intent", ["interested", "question"])
        .order("received_at", desc=False)
        .limit(limit)
        .execute()
        .data
    )


def _already_has_demo_invite(prospect_id: str) -> bool:
    rows = (
        client()
        .table("outreach_messages")
        .select("id")
        .eq("prospect_id", prospect_id)
        .eq("kind", "demo_invite")
        .limit(1)
        .execute()
        .data
    )
    return bool(rows)


def _first_name_from_business(name: str) -> str:
    if not name:
        return "there"
    parts = name.split()
    for i, p in enumerate(parts):
        if p.lower().rstrip(".") in {"dr", "dr.", "mr", "mr.", "ms", "ms.", "mrs", "mrs."}:
            if i + 1 < len(parts):
                return parts[i + 1].strip(".,'")
    return parts[0].strip(".,'")


def draft_demo_reply(prospect: dict, intent: str) -> tuple[str, str]:
    """Return (subject, body) — short reply offering the calendar link."""
    s = get_settings()
    name = _first_name_from_business(prospect.get("business_name") or "")
    biz = prospect.get("business_name") or "your business"
    cal = s.sender_calendar_url or "[your calendar link]"

    opps = prospect.get("opportunities") or []
    service = (opps[0] or {}).get("primary_service", "what we discussed") if opps else "what we discussed"

    subject = f"Re: {biz} — quick call?"
    if intent == "question":
        body = (
            f"Hi {name},\n\n"
            f"Great question — happiest to walk through it on a quick call rather than "
            f"a wall of text. 15 minutes, no slides, just the answer to what you asked plus "
            f"what {service.lower()} would look like for {biz}.\n\n"
            f"Pick any slot that works: {cal}\n\n"
            f"— {s.sender_name}\n{s.sender_company}"
        )
    else:
        body = (
            f"Hi {name},\n\n"
            f"Great — let's get something on the calendar. 15 minutes, I'll walk "
            f"you through exactly what {service.lower()} for {biz} would look like.\n\n"
            f"Grab whichever slot fits: {cal}\n\n"
            f"— {s.sender_name}\n{s.sender_company}"
        )
    return subject, body


def _upsert_demo_message(prospect_id: str, subject: str, body: str) -> str:
    """Create or refresh the demo_invite draft (kind != existing ones, so it's distinct)."""
    existing = (
        client()
        .table("outreach_messages")
        .select("id, status")
        .eq("prospect_id", prospect_id)
        .eq("channel", "email")
        .eq("kind", "demo_invite")
        .limit(1)
        .execute()
        .data
    )
    payload = {
        "prospect_id": prospect_id,
        "channel": "email",
        "kind": "demo_invite",
        "subject": subject,
        "body": body,
        "generated_by": "template",
        "status": "pending_approval",
    }
    if existing:
        if existing[0]["status"] in {"approved", "sent"}:
            return existing[0]["id"]    # don't touch; respect operator decision
        client().table("outreach_messages").update(payload).eq("id", existing[0]["id"]).execute()
        return existing[0]["id"]
    res = client().table("outreach_messages").insert(payload).execute()
    return res.data[0]["id"]


def process_reply(reply_row: dict) -> DemoDraft:
    prospect = reply_row.get("prospects") or {}
    pid = reply_row["prospect_id"]
    intent = reply_row["intent"]

    if not pid or not prospect:
        return DemoDraft(reply_id=reply_row["id"], prospect_id=pid, message_id=None,
                         approval_id=None, skipped_reason="no prospect")
    if not prospect.get("email"):
        return DemoDraft(reply_id=reply_row["id"], prospect_id=pid, message_id=None,
                         approval_id=None, skipped_reason="no email on prospect")
    if _already_has_demo_invite(pid):
        return DemoDraft(reply_id=reply_row["id"], prospect_id=pid, message_id=None,
                         approval_id=None, skipped_reason="demo_invite already exists")

    subject, body = draft_demo_reply(prospect, intent)
    message_id = _upsert_demo_message(pid, subject, body)

    s = get_settings()
    approval_id: str | None = None
    if s.require_approval_demos:
        summary = (
            f"Send booking link to {prospect.get('business_name')} "
            f"({prospect.get('email')})?\n\n"
            f"They replied — intent: {intent}.\n\n"
            f"Reply body (truncated):\n{(body[:300])}"
        )
        approval = approvals.request_approval(
            kind="book_demo",
            summary=summary,
            prospect_id=pid,
            message_id=message_id,
            payload={"reply_id": reply_row["id"], "intent": intent},
        )
        approval_id = approval.id
    else:
        # No approval gate -> approve immediately for the sender to pick up.
        client().table("outreach_messages").update({"status": "approved"}).eq("id", message_id).execute()

    return DemoDraft(reply_id=reply_row["id"], prospect_id=pid, message_id=message_id,
                     approval_id=approval_id)


def run_once(limit: int = 25) -> list[DemoDraft]:
    out: list[DemoDraft] = []
    for r in _fetch_interested_replies(limit):
        out.append(process_reply(r))
    return out


def bump_to_demo_scheduled(prospect_id: str) -> None:
    """Called by the sender after a demo_invite message is successfully sent."""
    client().table("prospects").update({"stage": "demo_scheduled"}).eq("id", prospect_id).execute()
