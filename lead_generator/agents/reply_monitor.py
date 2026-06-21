"""Agent 8 — Reply Monitor.

Polls Gmail via IMAP, matches new mail to prospects, and classifies intent.
Replies surface on the dashboard's prospect detail page. Prospect stage moves to:
  - 'replied'        for any genuine response (interested/question/unclear)
  - 'lost'           for explicit unsubscribe / not interested

We rely on three signals to tie an inbound email to a prospect:
  1. The In-Reply-To / References headers matching outreach_messages.sent_message_id
     (the most precise: it came in reply to something we sent).
  2. The From address matching prospects.email exactly.
  3. The From domain matching the prospect's website hostname.

Intent classification is intentionally rule-based for the MVP. It's cheap,
predictable, and easy to tune. We can swap in an LLM later by replacing
classify_intent() — the rest of the pipeline doesn't care.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlparse

from ..db import client
from ..sources.gmail_imap import ParsedMessage, poll_unread


# --------------------- intent classification ---------------------

POSITIVE_PATTERNS = [
    r"\binterest(ed|ing)?\b",
    r"\b(yes|sure|definitely|sounds good|let'?s)\b",
    r"\b(book|schedul(e|ing)|set up|set-up)\b",
    r"\b(call|chat|meet|demo|hop on)\b",
    r"\b(send|share)\s+(more|details|info)\b",
    r"\bwhen\b.*\b(free|available)\b",
    r"\btell me more\b",
]

NEGATIVE_PATTERNS = [
    r"\bnot interested\b",
    r"\bno thanks?\b",
    r"\bremove me\b",
    r"\bdon'?t (contact|email|message)\b",
    r"\bstop (emailing|contacting|messaging)\b",
    r"\bgo away\b",
]

UNSUBSCRIBE_PATTERNS = [
    r"\bunsubscribe\b",
    r"\bopt[- ]?out\b",
    r"\bremove\b.*\b(list|mailing)\b",
    r"\bnever contact\b",
]

QUESTION_PATTERNS = [
    r"\?$",
    r"\bhow much\b",
    r"\bwhat'?s\b.*\bcost\b",
    r"\bcan you\b",
    r"\bdo you\b",
    r"\bcould you\b",
    r"\bpric(e|ing)\b",
]

AUTO_REPLY_PATTERNS = [
    r"\bout of office\b",
    r"\bvacation\b",
    r"\bauto[- ]?(reply|responder)\b",
    r"\bautomatic reply\b",
    r"\bdelivery failure\b",
    r"\bundeliverable\b",
    r"\bmailer-daemon\b",
]


def _any(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text, re.IGNORECASE | re.MULTILINE) for p in patterns)


def classify_intent(subject: str, body: str) -> tuple[str, dict]:
    """Return (intent, signals_dict)."""
    haystack = f"{subject or ''}\n{body or ''}"
    signals = {
        "positive": _any(haystack, POSITIVE_PATTERNS),
        "negative": _any(haystack, NEGATIVE_PATTERNS),
        "unsubscribe": _any(haystack, UNSUBSCRIBE_PATTERNS),
        "question": _any(haystack, QUESTION_PATTERNS),
        "auto_reply": _any(haystack, AUTO_REPLY_PATTERNS),
    }
    if signals["auto_reply"]:
        return "auto_reply", signals
    if signals["unsubscribe"]:
        return "unsubscribe", signals
    if signals["negative"]:
        return "not_interested", signals
    if signals["positive"]:
        return "interested", signals
    if signals["question"]:
        return "question", signals
    return "unclear", signals


# --------------------- prospect matching ---------------------

@dataclass
class MatchResult:
    prospect_id: str | None = None
    outreach_message_id: str | None = None
    reason: str = ""


def _domain_of_email(addr: str) -> str:
    if "@" not in addr:
        return ""
    return addr.split("@", 1)[1].lower()


def _domain_of_url(url: str) -> str:
    if not url:
        return ""
    try:
        host = urlparse(url if "://" in url else f"https://{url}").hostname or ""
        return host.lower().lstrip("www.")
    except Exception:
        return ""


def _match_by_threading(msg: ParsedMessage) -> MatchResult:
    """Most precise: this email is a direct response to one we sent."""
    candidates: list[str] = []
    if msg.in_reply_to:
        candidates.append(msg.in_reply_to.strip("<>"))
    for r in msg.references:
        candidates.append(r.strip("<>"))
    if not candidates:
        return MatchResult()
    candidates = list(dict.fromkeys(candidates))

    rows = (
        client()
        .table("outreach_messages")
        .select("id, prospect_id, sent_message_id")
        .in_("sent_message_id", [f"<{c}>" for c in candidates] + candidates)
        .limit(1)
        .execute()
        .data
    )
    if not rows:
        return MatchResult()
    return MatchResult(
        prospect_id=rows[0]["prospect_id"],
        outreach_message_id=rows[0]["id"],
        reason="threading",
    )


def _match_by_from_address(addr: str) -> MatchResult:
    if not addr:
        return MatchResult()
    rows = (
        client()
        .table("prospects")
        .select("id")
        .eq("email", addr)
        .limit(1)
        .execute()
        .data
    )
    if not rows:
        return MatchResult()
    return MatchResult(prospect_id=rows[0]["id"], reason="from_email")


def _match_by_domain(addr: str) -> MatchResult:
    domain = _domain_of_email(addr)
    if not domain or domain in {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com"}:
        return MatchResult()
    rows = (
        client()
        .table("prospects")
        .select("id, website")
        .like("website", f"%{domain}%")
        .limit(1)
        .execute()
        .data
    )
    if not rows:
        return MatchResult()
    # Verify domain alignment to avoid false positives (e.g. "abc.com" matching "abcdef.com").
    site_domain = _domain_of_url(rows[0].get("website") or "")
    if site_domain and (site_domain == domain or domain.endswith("." + site_domain) or site_domain.endswith("." + domain)):
        return MatchResult(prospect_id=rows[0]["id"], reason="website_domain")
    return MatchResult()


def match_prospect(msg: ParsedMessage) -> MatchResult:
    finders = (
        lambda: _match_by_threading(msg),
        lambda: _match_by_from_address(msg.from_address),
        lambda: _match_by_domain(msg.from_address),
    )
    for finder in finders:
        r = finder()
        if r.prospect_id:
            return r
    return MatchResult()


# --------------------- persistence ---------------------

LOST_STAGE_INTENTS = {"unsubscribe", "not_interested"}
REPLIED_STAGE_INTENTS = {"interested", "question", "unclear"}


def _already_persisted(provider_message_id: str | None) -> bool:
    if not provider_message_id:
        return False
    rows = (
        client()
        .table("prospect_replies")
        .select("id")
        .eq("provider_message_id", provider_message_id)
        .limit(1)
        .execute()
        .data
    )
    return bool(rows)


def _bump_prospect(prospect_id: str, intent: str) -> None:
    """Advance the prospect stage based on the reply's intent."""
    cur = (
        client()
        .table("prospects")
        .select("stage")
        .eq("id", prospect_id)
        .limit(1)
        .execute()
        .data
    )
    if not cur:
        return
    stage = cur[0].get("stage")
    new_stage = stage

    if intent in LOST_STAGE_INTENTS:
        new_stage = "lost"
    elif intent in REPLIED_STAGE_INTENTS and stage in (None, "new", "analyzed", "approved", "contacted"):
        new_stage = "replied"

    if new_stage and new_stage != stage:
        client().table("prospects").update({"stage": new_stage}).eq("id", prospect_id).execute()


def _save_reply(msg: ParsedMessage, match: MatchResult, intent: str, signals: dict) -> str | None:
    row = {
        "prospect_id": match.prospect_id,
        "outreach_message_id": match.outreach_message_id,
        "channel": "email",
        "provider_message_id": msg.message_id,
        "in_reply_to": msg.in_reply_to,
        "from_address": msg.from_address,
        "subject": msg.subject,
        "body": (msg.body_text or "")[:20000],
        "intent": intent,
        "intent_signals": {**signals, "match_reason": match.reason},
        "received_at": msg.received_at.isoformat() if msg.received_at else None,
        "raw": {"references": msg.references, "uid": msg.uid, "size": msg.raw_size_bytes},
    }
    res = client().table("prospect_replies").insert(row).execute()
    return (res.data[0]["id"] if res.data else None)


# --------------------- notifications to operator ---------------------

INTENT_LABEL = {
    "interested":     "🟢 INTERESTED",
    "question":       "❓ QUESTION",
    "unclear":        "🟡 UNCLEAR",
    "not_interested": "🔴 NOT INTERESTED",
    "unsubscribe":    "🚫 UNSUBSCRIBE",
    "auto_reply":     "🤖 AUTO-REPLY",
}


def _summarise(msg: ParsedMessage, prospect_name: str | None, intent: str) -> str:
    body_preview = (msg.body_text or "").strip().splitlines()
    snippet = " ".join([line.strip() for line in body_preview if line.strip()][:3])[:300]
    name = prospect_name or msg.from_address or "(unknown)"
    return (
        f"{INTENT_LABEL.get(intent, intent.upper())}\n"
        f"From: {name}\n"
        f"Subject: {msg.subject or '(no subject)'}\n"
        f"\n{snippet}"
    )


# --------------------- orchestration ---------------------

@dataclass
class MonitorResult:
    fetched: int = 0
    matched: int = 0
    unmatched: int = 0
    duplicates: int = 0
    by_intent: dict = None     # type: ignore[assignment]


def run_once(*, max_messages: int | None = None) -> MonitorResult:
    """One full poll: fetch, match, classify, persist. Replies surface on the dashboard."""
    result = MonitorResult(by_intent={})
    messages = poll_unread(max_messages=max_messages)

    for m in messages:
        result.fetched += 1
        if _already_persisted(m.message_id):
            result.duplicates += 1
            continue

        intent, signals = classify_intent(m.subject, m.body_text)
        match = match_prospect(m)

        if match.prospect_id:
            result.matched += 1
        else:
            result.unmatched += 1

        # Persist even unmatched ones — the operator may want to triage manually.
        _save_reply(m, match, intent, signals)

        if match.prospect_id:
            _bump_prospect(match.prospect_id, intent)

        result.by_intent[intent] = result.by_intent.get(intent, 0) + 1

    return result
