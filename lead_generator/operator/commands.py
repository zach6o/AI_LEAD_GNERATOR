"""Operator command router.

Resolves text commands ("/status", "/pending", "/approve abc") and inline
button taps (button_reply.id) into actions the agent performs on your behalf.

Every authenticated inbound message goes through dispatch() and gets a
human-readable string back. The webhook handler sends that string to the
operator's WhatsApp.
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Any

from ..db import client
from . import approvals, notifier


HELP_TEXT = (
    "🤖 Lead Generator commands\n\n"
    "/status          — pipeline summary\n"
    "/pending [N]     — list pending approvals\n"
    "/approve <id>    — approve a pending request (id prefix is OK)\n"
    "/reject  <id>    — reject a pending request\n"
    "/hunt <niche> <location> [limit]   — kick off a hunt\n"
    "/analyze [N]     — analyze N pending websites\n"
    "/opps    [N]     — score N opportunities\n"
    "/enrich  [N]     — scrape N emails\n"
    "/write   [N]     — draft messages for N prospects\n"
    "/queue   [N]     — promote N drafts to pending-approval\n"
    "/send    [N]     — send up to N approved messages (email+whatsapp)\n"
    "/monitor         — poll Gmail for new replies\n"
    "/demos   [N]     — draft demo invites for N interested replies\n"
    "/ping            — reopen the 24h WhatsApp window\n"
    "/help            — this message"
)


@dataclass
class CommandResult:
    text: str
    action_taken: str          # short log line for operator_actions.result
    approval_id: str | None = None


def _audit(from_phone: str, inbound_text: str | None, command: str | None,
           args: dict, result_text: str, approval_id: str | None = None,
           raw: dict | None = None) -> None:
    client().table("operator_actions").insert(
        {
            "from_phone": from_phone,
            "inbound_text": (inbound_text or "")[:2000],
            "command": command,
            "args": args,
            "result": result_text[:300],
            "approval_id": approval_id,
            "raw": raw or {},
        }
    ).execute()


# --------------------- decisions on approvals ---------------------

def _resolve_by_prefix(approval_id_prefix: str, decision: str, by: str) -> str:
    """Allow the operator to type just the first chunk of the UUID."""
    pending = approvals.list_pending(limit=200)
    matches = [a for a in pending if a.id.startswith(approval_id_prefix.lower())]
    if not matches:
        return f"❓ No pending approval matches '{approval_id_prefix}'."
    if len(matches) > 1:
        return f"❓ Ambiguous prefix — {len(matches)} matches. Type more chars."
    a = matches[0]
    approvals.resolve(a.id, decision, by=by)
    verb = {"approved": "✅ Approved", "rejected": "❌ Rejected"}.get(decision, decision)
    return f"{verb}: {a.kind} — {a.summary.splitlines()[0]}"


def _decide_button(button_id: str, by: str) -> CommandResult:
    parsed = approvals.parse_button_id(button_id)
    if not parsed:
        return CommandResult(text=f"Unknown button: {button_id}", action_taken="ignored_button")
    decision, aid = parsed
    a = approvals.resolve(aid, decision, by=by)
    if not a:
        return CommandResult(text=f"⚠️ Approval {aid[:8]} not found", action_taken="resolve_missing",
                             approval_id=aid)
    verb = {"approved": "✅ Approved", "rejected": "❌ Rejected"}.get(decision, decision)
    return CommandResult(
        text=f"{verb}: {a.kind} — {a.summary.splitlines()[0]}",
        action_taken=f"button_{decision}",
        approval_id=aid,
    )


# --------------------- pipeline reads ---------------------

PIPELINE_STAGES = [
    "new", "analyzed", "approved", "contacted", "replied",
    "demo_scheduled", "proposal_sent", "won", "lost",
]


def _cmd_status() -> str:
    rows = client().table("prospects").select("stage").execute().data
    counts = {s: 0 for s in PIPELINE_STAGES}
    for r in rows:
        counts[r.get("stage") or "new"] = counts.get(r.get("stage") or "new", 0) + 1
    pending = (
        client()
        .table("approval_requests")
        .select("id", count="exact")
        .eq("status", "pending")
        .execute()
    )
    pending_count = pending.count or 0

    lines = [
        f"📊 Pipeline ({len(rows)} prospects)",
        *[f"  {s:<16}{counts[s]:>4}" for s in PIPELINE_STAGES],
        "",
        f"⏳ Pending approvals: {pending_count}",
        f"Use /pending to see them.",
    ]
    return "\n".join(lines)


def _cmd_pending(limit: int = 10) -> str:
    items = approvals.list_pending(limit=limit)
    if not items:
        return "✅ Nothing pending."
    lines = [f"⏳ {len(items)} pending approval(s):"]
    for a in items:
        first_line = a.summary.splitlines()[0] if a.summary else "(no summary)"
        lines.append(f"  {a.id.split('-')[0]} · {a.kind} — {first_line[:60]}")
    lines.append("\nReply: /approve <id> or /reject <id> (prefix is enough).")
    return "\n".join(lines)


# --------------------- batch kick-offs ---------------------
# Each of these may itself require approval (REQUIRE_APPROVAL_BATCHES). We
# wrap the actual work in a batch approval if so; otherwise we run it.

def _request_or_run_batch(kind_label: str, runner) -> str:
    """If batch approvals are required, create an approval request describing
    what would run; otherwise execute `runner` (a 0-arg callable) right now."""
    from ..config import get_settings
    s = get_settings()
    if s.require_approval_batches:
        a = approvals.request_approval(
            kind="run_batch",
            summary=f"Run batch: {kind_label}",
            payload={"label": kind_label},
        )
        return f"⏳ Batch '{kind_label}' needs approval ({a.id.split('-')[0]})."
    try:
        runner()
    except Exception as e:
        return f"❌ {kind_label} failed: {type(e).__name__}: {e}"
    return f"✅ {kind_label} done."


def _cmd_hunt(args: list[str]) -> str:
    if len(args) < 2:
        return "Usage: /hunt <niche> <location> [limit]"
    niche = args[0]
    # Location may contain spaces — re-join everything between niche and an
    # optional trailing integer limit.
    limit = 20
    location_parts = args[1:]
    if location_parts and location_parts[-1].isdigit():
        limit = int(location_parts[-1])
        location_parts = location_parts[:-1]
    location = " ".join(location_parts)
    if not location:
        return "Usage: /hunt <niche> <location> [limit]"

    def _run():
        from ..agents.lead_hunter import hunt_google_places
        hunt_google_places(niche=niche, location=location, limit=limit)

    return _request_or_run_batch(f"hunt niche={niche} location={location} limit={limit}", _run)


def _cmd_analyze(args: list[str]) -> str:
    limit = int(args[0]) if args and args[0].isdigit() else 20

    def _run():
        from ..agents.website_analyzer import analyze_prospect, fetch_pending_prospects, save_analysis
        for p in fetch_pending_prospects(limit):
            if p.get("website"):
                save_analysis(analyze_prospect(p["id"], p["website"]))

    return _request_or_run_batch(f"analyze limit={limit}", _run)


def _cmd_opps(args: list[str]) -> str:
    limit = int(args[0]) if args and args[0].isdigit() else 50

    def _run():
        from ..agents.opportunity_finder import (
            fetch_pending_prospects as fp, find_opportunity, save_opportunity,
        )
        for p in fp(limit):
            analyses = p.get("website_analyses") or []
            save_opportunity(find_opportunity(p, analyses[0] if analyses else None))

    return _request_or_run_batch(f"opportunities limit={limit}", _run)


def _cmd_enrich(args: list[str]) -> str:
    limit = int(args[0]) if args and args[0].isdigit() else 25

    def _run():
        from ..agents.email_enricher import enrich_all
        enrich_all(limit)

    return _request_or_run_batch(f"enrich limit={limit}", _run)


def _cmd_write(args: list[str]) -> str:
    limit = int(args[0]) if args and args[0].isdigit() else 25

    def _run():
        from ..agents.outreach_writer import fetch_pending_prospects as fp, write_for_prospect
        for p in fp(limit):
            write_for_prospect(p)

    return _request_or_run_batch(f"write limit={limit}", _run)


def _cmd_queue(args: list[str]) -> str:
    """Promote drafts to pending-approval (creates per-message approvals)."""
    limit = int(args[0]) if args and args[0].isdigit() else 25
    from ..agents.outreach_sender import request_approval_for_drafts
    promoted, skipped = request_approval_for_drafts(limit)
    return f"📨 Queued {promoted} drafts for approval (skipped {skipped}). Tap buttons in upcoming messages."


def _cmd_send(args: list[str]) -> str:
    limit = int(args[0]) if args and args[0].isdigit() else 25

    def _run():
        from ..agents.outreach_sender import run_batch
        run_batch(limit=limit, rate_seconds=2.0, dry_run=False)

    return _request_or_run_batch(f"send limit={limit}", _run)


def _cmd_monitor() -> str:
    from ..agents.reply_monitor import run_once
    try:
        r = run_once()
    except Exception as e:
        return f"❌ monitor failed: {type(e).__name__}: {e}"
    intents = ", ".join(f"{k}:{v}" for k, v in (r.by_intent or {}).items()) or "—"
    return (f"📥 Fetched {r.fetched}, matched {r.matched}, dup {r.duplicates}\n"
            f"intents: {intents}")


def _cmd_demos(args: list[str]) -> str:
    limit = int(args[0]) if args and args[0].isdigit() else 10
    from ..agents.demo_booker import run_once
    drafts = run_once(limit)
    open_ = [d for d in drafts if d.approval_id]
    skipped = [d for d in drafts if d.skipped_reason]
    return (f"📅 Demo Booker: {len(open_)} draft(s) awaiting approval, "
            f"{len(skipped)} skipped.")


def _cmd_ping() -> str:
    return "👋 pong — window open for 24h."


# --------------------- dispatch ---------------------

def dispatch_text(text: str, *, from_phone: str) -> CommandResult:
    """Parse a text command and execute it. Always returns a reply string."""
    raw = (text or "").strip()
    if not raw:
        return CommandResult(text=HELP_TEXT, action_taken="empty->help")

    # Tolerate plain text without a leading slash for the common shortcuts.
    if not raw.startswith("/"):
        # Map a few natural-language nudges to commands.
        low = raw.lower()
        if low in {"status", "summary"}: raw = "/status"
        elif low in {"pending", "queue?"}: raw = "/pending"
        elif low in {"help", "?"}: raw = "/help"
        elif low in {"ping"}: raw = "/ping"
        elif low in {"monitor", "check email", "check replies"}: raw = "/monitor"
        else:
            return CommandResult(
                text=f"Got: {raw[:200]}\n\nThis bot only takes commands. Try /help.",
                action_taken="unrecognised",
            )

    try:
        tokens = shlex.split(raw)
    except ValueError:
        tokens = raw.split()
    cmd = tokens[0].lstrip("/").lower()
    args = tokens[1:]

    if cmd in {"help", "h"}:           return CommandResult(_cmd_help(), "help")
    if cmd == "status":                return CommandResult(_cmd_status(), "status")
    if cmd == "pending":
        n = int(args[0]) if args and args[0].isdigit() else 10
        return CommandResult(_cmd_pending(n), "pending")
    if cmd == "approve" and args:
        return CommandResult(_resolve_by_prefix(args[0], "approved", from_phone), "approve",
                             approval_id=args[0])
    if cmd == "reject" and args:
        return CommandResult(_resolve_by_prefix(args[0], "rejected", from_phone), "reject",
                             approval_id=args[0])
    if cmd == "hunt":                  return CommandResult(_cmd_hunt(args), "hunt")
    if cmd == "analyze":               return CommandResult(_cmd_analyze(args), "analyze")
    if cmd in {"opps", "opportunities"}: return CommandResult(_cmd_opps(args), "opportunities")
    if cmd == "enrich":                return CommandResult(_cmd_enrich(args), "enrich")
    if cmd == "write":                 return CommandResult(_cmd_write(args), "write")
    if cmd == "queue":                 return CommandResult(_cmd_queue(args), "queue")
    if cmd == "send":                  return CommandResult(_cmd_send(args), "send")
    if cmd == "monitor":               return CommandResult(_cmd_monitor(), "monitor")
    if cmd == "demos":                 return CommandResult(_cmd_demos(args), "demos")
    if cmd == "ping":                  return CommandResult(_cmd_ping(), "ping")

    return CommandResult(text=f"Unknown command: /{cmd}\n\n{HELP_TEXT}", action_taken="unknown")


def _cmd_help() -> str:
    return HELP_TEXT


def dispatch_button(button_id: str, *, from_phone: str) -> CommandResult:
    """Handle an inline button tap from a WhatsApp interactive message."""
    return _decide_button(button_id, by=from_phone)


# --------------------- public entry from webhook ---------------------

def handle_inbound(*, from_phone: str, text: str | None, button_id: str | None,
                   raw: dict | None = None) -> str:
    """Main entry called by the WhatsApp webhook for every inbound message.

    Returns the reply text to send back to the operator.
    """
    if button_id:
        res = dispatch_button(button_id, from_phone=from_phone)
        _audit(from_phone, button_id, "button", {"button_id": button_id},
               res.action_taken, approval_id=res.approval_id, raw=raw)
        return res.text

    res = dispatch_text(text or "", from_phone=from_phone)
    _audit(from_phone, text, res.action_taken.split("->")[0] if "->" in res.action_taken else res.action_taken,
           {"text": text}, res.action_taken, approval_id=res.approval_id, raw=raw)
    return res.text


def send_reply(text: str) -> None:
    """Convenience to ship a reply back to the operator via WhatsApp."""
    notifier.send_text(text)
