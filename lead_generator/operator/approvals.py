"""Operator approval gate.

Every action that requires your permission goes through here:
  - sending an outreach message (draft -> approved -> sent)
  - replying to a prospect reply
  - sending a calendar booking link
  - kicking off a batch (hunt/analyze/write/enrich/send)

The flow:
  1. Code calls request_approval(kind, summary, payload, prospect_id?, message_id?).
  2. A row is written to approval_requests with status='pending'.
  3. We push a WhatsApp interactive message to the operator with
     [Approve] [Reject] buttons. The button IDs encode the approval id.
  4. The operator taps; the webhook routes the button_reply payload to
     resolve(approval_id, 'approved'|'rejected').
  5. The waiting code (sender/etc) sees status='approved' and proceeds.

There is no in-process blocking. Callers either:
  - request now and act later in a separate run (CLI pattern), or
  - poll `wait_for(approval_id, timeout)` (used for interactive flows).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ..db import client


@dataclass
class Approval:
    id: str
    kind: str
    status: str
    summary: str
    prospect_id: str | None
    message_id: str | None
    payload: dict
    created_at: str
    decided_at: str | None
    decided_by: str | None
    notify_message_id: str | None


def _row_to_approval(row: dict) -> Approval:
    return Approval(
        id=row["id"],
        kind=row["kind"],
        status=row["status"],
        summary=row["summary"],
        prospect_id=row.get("prospect_id"),
        message_id=row.get("message_id"),
        payload=row.get("payload") or {},
        created_at=row["created_at"],
        decided_at=row.get("decided_at"),
        decided_by=row.get("decided_by"),
        notify_message_id=row.get("notify_message_id"),
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------- create + notify ---------------------

def request_approval(
    *,
    kind: str,
    summary: str,
    payload: dict | None = None,
    prospect_id: str | None = None,
    message_id: str | None = None,
    expires_in_hours: int = 48,
    notify: bool = True,
) -> Approval:
    """Create a pending approval and (optionally) push a prompt to operator WhatsApp."""
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=expires_in_hours)).isoformat()
    inserted = (
        client()
        .table("approval_requests")
        .insert(
            {
                "kind": kind,
                "summary": summary[:1024],
                "payload": payload or {},
                "prospect_id": prospect_id,
                "message_id": message_id,
                "expires_at": expires_at,
            }
        )
        .execute()
    )
    row = inserted.data[0]
    approval = _row_to_approval(row)

    if notify:
        _notify(approval)
    return approval


def _notify(approval: Approval) -> None:
    """No-op: approvals now surface on the dashboard /approvals page.

    Kept as a stub so `request_approval(..., notify=True)` callers don't break.
    """
    return


# --------------------- read ---------------------

def get(approval_id: str) -> Approval | None:
    rows = (
        client()
        .table("approval_requests")
        .select("*")
        .eq("id", approval_id)
        .limit(1)
        .execute()
        .data
    )
    return _row_to_approval(rows[0]) if rows else None


def list_pending(limit: int = 25) -> list[Approval]:
    rows = (
        client()
        .table("approval_requests")
        .select("*")
        .eq("status", "pending")
        .order("created_at", desc=False)
        .limit(limit)
        .execute()
        .data
    )
    return [_row_to_approval(r) for r in rows]


# --------------------- resolve ---------------------

def resolve(approval_id: str, decision: str, *, by: str | None = None) -> Approval | None:
    """Mark an approval approved/rejected/cancelled. Idempotent."""
    if decision not in {"approved", "rejected", "cancelled"}:
        raise ValueError(f"bad decision: {decision!r}")
    existing = get(approval_id)
    if not existing:
        return None
    if existing.status != "pending":
        return existing       # already decided; do nothing

    client().table("approval_requests").update(
        {"status": decision, "decided_at": _now_iso(), "decided_by": by}
    ).eq("id", approval_id).execute()
    return get(approval_id)


def cancel_for_message(message_id: str) -> int:
    """Cancel any pending approvals for a message (e.g. on reject/edit)."""
    res = (
        client()
        .table("approval_requests")
        .update({"status": "cancelled", "decided_at": _now_iso()})
        .eq("message_id", message_id)
        .eq("status", "pending")
        .execute()
    )
    return len(res.data or [])


def find_pending_for_message(message_id: str) -> Approval | None:
    rows = (
        client()
        .table("approval_requests")
        .select("*")
        .eq("message_id", message_id)
        .eq("status", "pending")
        .limit(1)
        .execute()
        .data
    )
    return _row_to_approval(rows[0]) if rows else None


def find_approved_for_message(message_id: str) -> Approval | None:
    rows = (
        client()
        .table("approval_requests")
        .select("*")
        .eq("message_id", message_id)
        .eq("status", "approved")
        .limit(1)
        .execute()
        .data
    )
    return _row_to_approval(rows[0]) if rows else None


def wait_for(approval_id: str, *, timeout_seconds: float = 60.0, poll_seconds: float = 2.0) -> Approval | None:
    """Poll until the approval leaves 'pending' or the timeout fires."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        a = get(approval_id)
        if a is None or a.status != "pending":
            return a
        time.sleep(poll_seconds)
    return get(approval_id)


