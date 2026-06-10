"""Agent 7 — Outreach Sender.

Reads `outreach_messages` rows with status='approved' and dispatches each one
to the appropriate channel. NEVER sends a message that isn't approved (that
PRD rule is enforced at the query level: we never even look at draft rows).

When REQUIRE_APPROVAL_SENDS is true, the sender additionally refuses any
message that doesn't have a matching `approved` approval_request — the
operator must explicitly tap [Approve] in WhatsApp for each one.

Each attempt is recorded on the row: attempts++, last_attempt_at=now(). On
success we flip status to 'sent', store provider_message_id, and bump the
prospect's stage to 'contacted' (or 'demo_scheduled' for demo_invite kind)
/ last_contacted to now (first send only).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone

from ..config import get_settings
from ..db import client
from ..operator import approvals
from ..senders.base import SendResult
from ..senders.gmail_smtp import send_email
from ..senders.whatsapp_cloud import send_whatsapp


SUPPORTED_CHANNELS = ("email", "whatsapp")


@dataclass
class SenderStats:
    attempted: int = 0
    sent: int = 0
    failed: int = 0
    skipped: int = 0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fetch_approved(channel: str | None, limit: int) -> list[dict]:
    """Fetch messages whose row-level status is 'approved'.

    The approval-gate check (per-message approval_request) happens later in
    _gate_ok so we can surface a useful reason on rejection.
    """
    q = (
        client()
        .table("outreach_messages")
        .select(
            "id, prospect_id, channel, kind, subject, body, status, attempts, "
            "prospects(id, business_name, email, phone, stage)"
        )
        .eq("status", "approved")
        .order("created_at", desc=False)
        .limit(limit)
    )
    if channel:
        q = q.eq("channel", channel)
    return q.execute().data


def request_approval_for_drafts(limit: int = 50) -> tuple[int, int]:
    """Promote draft outreach_messages to status='pending_approval' and
    open an operator approval request for each. Returns (promoted, skipped).

    Skipped = already pending_approval, already approved/sent, or no
    deliverable contact (e.g. email kind with no prospect.email).
    """
    from ..operator import approvals  # local import to avoid cycles

    drafts = (
        client()
        .table("outreach_messages")
        .select(
            "id, prospect_id, channel, kind, subject, body, status, "
            "prospects(business_name, email, phone)"
        )
        .eq("status", "draft")
        .limit(limit)
        .execute()
        .data
    )

    promoted = 0
    skipped = 0
    for d in drafts:
        p = d.get("prospects") or {}
        # Skip unsendable channels/recipients upfront.
        if d["channel"] == "email" and not p.get("email"):
            skipped += 1
            continue
        if d["channel"] == "whatsapp" and not p.get("phone"):
            skipped += 1
            continue
        if d["channel"] == "linkedin":
            skipped += 1   # LinkedIn drafts are copy-paste only — no sender
            continue

        # If an approval is already pending for this message, don't open a duplicate.
        if approvals.find_pending_for_message(d["id"]) or approvals.find_approved_for_message(d["id"]):
            skipped += 1
            continue

        summary = (
            f"Send {d['kind']} {d['channel']} to {p.get('business_name','(unknown)')}\n"
            f"To: {p.get('email') or p.get('phone')}\n"
            f"Subject: {d.get('subject') or '-'}\n\n"
            f"{(d.get('body') or '')[:600]}"
        )
        approvals.request_approval(
            kind="send_message",
            summary=summary,
            prospect_id=d["prospect_id"],
            message_id=d["id"],
            payload={"channel": d["channel"], "kind": d["kind"]},
        )
        client().table("outreach_messages").update(
            {"status": "pending_approval"}
        ).eq("id", d["id"]).execute()
        promoted += 1

    return promoted, skipped


def _send_one(msg: dict) -> SendResult:
    prospect = msg.get("prospects") or {}
    channel = msg["channel"]

    if channel == "email":
        return send_email(
            to_email=prospect.get("email") or "",
            subject=msg.get("subject") or "(no subject)",
            body=msg["body"],
        )
    if channel == "whatsapp":
        return send_whatsapp(
            to_phone=prospect.get("phone") or "",
            body=msg["body"],
        )
    # We don't send LinkedIn — drafts stay as copy-paste material.
    return SendResult(ok=False, error=f"channel '{channel}' is not sendable (drafts only)")


def _record_attempt(msg_id: str, attempts: int, result: SendResult) -> None:
    update = {
        "attempts": attempts + 1,
        "last_attempt_at": _now_iso(),
    }
    if result.ok:
        update["status"] = "sent"
        update["sent_at"] = _now_iso()
        update["sent_message_id"] = result.provider_message_id
        update["send_error"] = None
    else:
        # Stays at status='approved' so we can retry. Error captured for review.
        update["send_error"] = result.error
    client().table("outreach_messages").update(update).eq("id", msg_id).execute()


def _bump_prospect_on_first_send(prospect: dict, msg_kind: str) -> None:
    stage = prospect.get("stage")
    # demo_invite -> we just sent the calendar link; lift the stage.
    if msg_kind == "demo_invite":
        if stage not in {"demo_scheduled", "proposal_sent", "won", "lost"}:
            client().table("prospects").update(
                {"stage": "demo_scheduled", "last_contacted": _now_iso()}
            ).eq("id", prospect["id"]).execute()
        return
    if stage in {"contacted", "replied", "demo_scheduled", "proposal_sent", "won", "lost"}:
        return  # already past 'contacted'
    client().table("prospects").update(
        {"stage": "contacted", "last_contacted": _now_iso()}
    ).eq("id", prospect["id"]).execute()


def _gate_ok(msg: dict) -> tuple[bool, str | None]:
    """Returns (ok_to_send, refusal_reason)."""
    s = get_settings()
    if not s.require_approval_sends:
        return True, None
    approved = approvals.find_approved_for_message(msg["id"])
    if approved:
        return True, None
    pending = approvals.find_pending_for_message(msg["id"])
    if pending:
        return False, f"awaiting operator approval ({pending.id.split('-')[0]})"
    return False, "no approval request on file"


def send_message(message_id: str, *, dry_run: bool = False) -> SendResult:
    """Send a single message by id. Used by both the CLI and the dashboard
    'Send now' button. Idempotent: if status isn't approved we refuse."""
    row = (
        client()
        .table("outreach_messages")
        .select(
            "id, prospect_id, channel, kind, subject, body, status, attempts, "
            "prospects(id, business_name, email, phone, stage)"
        )
        .eq("id", message_id)
        .limit(1)
        .execute()
        .data
    )
    if not row:
        return SendResult(ok=False, error="message not found")
    msg = row[0]
    if msg["status"] != "approved":
        return SendResult(ok=False, error=f"refusing to send: status is '{msg['status']}', not 'approved'")

    ok, reason = _gate_ok(msg)
    if not ok:
        return SendResult(ok=False, error=f"refusing: {reason}")

    if dry_run:
        return SendResult(ok=True, provider_message_id="(dry-run)", error=None)

    result = _send_one(msg)
    _record_attempt(msg["id"], msg.get("attempts") or 0, result)
    if result.ok:
        _bump_prospect_on_first_send(msg.get("prospects") or {}, msg.get("kind", ""))
    return result


def run_batch(
    *,
    channel: str | None = None,
    limit: int = 25,
    rate_seconds: float = 2.0,
    dry_run: bool = False,
) -> tuple[SenderStats, list[dict]]:
    """Send up to `limit` approved messages. Sleeps `rate_seconds` between."""
    if channel and channel not in SUPPORTED_CHANNELS:
        return SenderStats(skipped=1), [{"error": f"channel '{channel}' is drafts-only"}]

    queue = _fetch_approved(channel, limit)
    stats = SenderStats()
    log: list[dict] = []

    for i, msg in enumerate(queue):
        prospect = msg.get("prospects") or {}
        recipient = prospect.get("email") if msg["channel"] == "email" else prospect.get("phone")
        entry = {
            "business": prospect.get("business_name"),
            "channel": msg["channel"],
            "kind": msg["kind"],
            "recipient": recipient,
        }

        stats.attempted += 1

        ok, reason = _gate_ok(msg)
        if not ok:
            stats.skipped += 1
            entry["status"] = "SKIP"
            entry["error"] = reason
            log.append(entry)
            continue

        if dry_run:
            entry["status"] = "DRY-RUN"
            log.append(entry)
            continue

        result = _send_one(msg)
        _record_attempt(msg["id"], msg.get("attempts") or 0, result)
        if result.ok:
            stats.sent += 1
            entry["status"] = "sent"
            entry["provider_id"] = result.provider_message_id
            _bump_prospect_on_first_send(prospect, msg.get("kind", ""))
        else:
            stats.failed += 1
            entry["status"] = "FAIL"
            entry["error"] = result.error
        log.append(entry)

        if i < len(queue) - 1 and rate_seconds > 0:
            time.sleep(rate_seconds)

    return stats, log
