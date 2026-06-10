"""Agent 5 — Outreach Writer.

Produces personalized cold messages (email subject+body, WhatsApp, LinkedIn DM)
plus two follow-ups for each prospect, using the data Agent 3 has already
distilled into a pitch.

Templates are intentionally short. Long cold messages don't convert better;
they convert worse. Every variant explicitly mentions:
  - the business name
  - the specific discovered issue (from website analysis)
  - the estimated revenue impact (anchor)
  - a single, low-friction CTA (15-min call)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from ..config import get_settings
from ..db import client


# ---------- helpers ----------

def _inr(n: int | None) -> str:
    if not n:
        return ""
    return f"₹{int(n):,}"


_GENERIC_OPENERS = {
    "smile", "dental", "the", "my", "best", "new", "first", "royal",
    "elite", "happy", "perfect", "premier", "global", "city", "modern",
    "advanced", "central", "clinic", "hotel", "salon", "gym", "studio",
    "cafe", "house", "world", "shop", "store",
}

def _first_name_from_business(name: str) -> str:
    """Try to extract an owner-ish handle from the business name.

    Many Indian SMBs are named after the owner ('Dr Idris Holy Dental Clinic'),
    in which case we want 'Idris'. Otherwise it's much safer to use 'there'
    than to risk "Hi Smile,".
    """
    if not name:
        return "there"
    parts = name.split()
    for i, p in enumerate(parts):
        if p.lower().rstrip(".") in {"dr", "dr.", "mr", "mr.", "ms", "ms.", "mrs", "mrs."}:
            if i + 1 < len(parts):
                return parts[i + 1].strip(".,'")
    first = parts[0].strip(".,'")
    if first.lower() in _GENERIC_OPENERS:
        return "there"
    return first


def _short_location(location: str | None) -> str:
    """Reduce a Google formatted address to something usable in a sentence.

    'Andheri West, Mumbai, Maharashtra 400053' -> 'Andheri West, Mumbai'.
    Returns 'your area' if we can't extract anything short and safe.
    """
    if not location:
        return "your area"
    # Strip street-level noise: drop segments containing digits, "opp.", etc.
    raw_segments = [s.strip() for s in location.split(",")]
    keep: list[str] = []
    for seg in raw_segments:
        if any(ch.isdigit() for ch in seg):
            continue
        if any(k in seg.lower() for k in ["opp.", "near", "behind", "road", " rd", "lane", "block"]):
            continue
        if len(seg) > 40:
            continue
        keep.append(seg)
    # Last 2 segments tend to be "Locality, City".
    short = ", ".join(keep[-2:]) if keep else ""
    return short or "your area"


# ---------- channel templates ----------

@dataclass
class Message:
    channel: str           # 'email' | 'whatsapp' | 'linkedin'
    kind: str              # 'initial' | 'followup_1' | 'followup_2'
    subject: str | None
    body: str


def _email_initial(p: dict, opp: dict, analysis: dict | None, s) -> Message:
    name = _first_name_from_business(p["business_name"])
    service = opp["primary_service"]
    one_time = _inr(opp.get("our_one_time_inr"))
    monthly = _inr(opp.get("our_monthly_inr"))
    reviews = p.get("google_reviews") or 0
    rating = p.get("google_rating") or ""
    location_short = _short_location(p.get("location"))
    industry_h = (p.get("industry") or "local").replace("_", " ")
    biz = p["business_name"]

    # Service-specific one-line opener (avoids repeating pitch_summary below).
    openers = {
        "Website Development":   f"I came across {biz} on Google — {reviews}+ reviews at {rating}★, no website.",
        "Website Redesign":      f"Took a quick look at {biz}'s site — solid business, dated site.",
        "AI Appointment Booking": f"Pulled up {biz} on Google ({reviews}+ reviews, {rating}★) — noticed there's no online booking.",
        "AI Chatbot":            f"Checked out {biz}'s site — clean overall, but no live chat for visitors.",
        "AI Customer Support":   f"Looking at {biz}'s reviews, it's clear you're getting volume — and probably a lot of repetitive support questions.",
        "Lead Generation System": f"Visited {biz}'s site — no real lead-capture loop.",
        "WhatsApp Automation":   f"Quick note for {biz} — given your volume, you'd benefit from automated WhatsApp reminders/confirmations.",
        "Analytics & CRM Integration": f"Noticed {biz}'s site has no analytics installed — flying blind on what's working.",
    }
    opener = openers.get(service, f"I came across {biz} while researching {industry_h}s in {location_short}.")

    # The pitch summary already contains the value claim — use it once, here.
    value_line = opp.get("pitch_summary") or f"For a {industry_h} of your size, that's measurable monthly upside."

    pricing_line = ""
    if one_time:
        pricing_line = f"Pricing is {one_time} one-time"
        if monthly:
            pricing_line += f" + {monthly}/month"
        pricing_line += ".\n\n"

    cta_url = s.sender_calendar_url or "[link to your calendar]"

    subjects = {
        "Website Development":      f"{biz} — Google reviews, no website?",
        "Website Redesign":         f"Quick idea for {biz}'s site",
        "AI Appointment Booking":   f"Recovering after-hours bookings at {biz}",
        "AI Chatbot":               f"Adding live chat to {biz}",
        "Lead Generation System":   f"Lead capture for {biz}",
        "WhatsApp Automation":      f"WhatsApp automation for {biz}",
    }
    subject = subjects.get(service, f"Quick idea for {biz}")

    body = (
        f"Hi {name},\n\n"
        f"{opener}\n\n"
        f"{value_line}\n\n"
        f"We can ship this in 1-3 weeks. {pricing_line}"
        f"Worth a 15-minute call this week? {cta_url}\n\n"
        f"— {s.sender_name}\n"
        f"{s.sender_company}"
    )
    return Message(channel="email", kind="initial", subject=subject, body=body)


def _email_followup_1(p: dict, opp: dict, s) -> Message:
    name = _first_name_from_business(p["business_name"])
    cta_url = s.sender_calendar_url or "[link to your calendar]"
    body = (
        f"Hi {name},\n\n"
        f"Following up on the note I sent last week about {opp['primary_service'].lower()} "
        f"for {p['business_name']}.\n\n"
        f"If the timing is off, no worries — happy to send over a one-page teardown of "
        f"your current setup with the specific gaps and fixes.\n\n"
        f"Want me to send that over, or grab 15 minutes here? {cta_url}\n\n"
        f"— {s.sender_name}"
    )
    return Message(channel="email", kind="followup_1", subject=f"Re: {p['business_name']}", body=body)


def _email_followup_2(p: dict, opp: dict, s) -> Message:
    name = _first_name_from_business(p["business_name"])
    body = (
        f"Hi {name},\n\n"
        f"Last note from me — I'll stop here so I'm not in your inbox.\n\n"
        f"If {opp['primary_service'].lower()} isn't a priority right now, totally fair. "
        f"If it becomes relevant later, my email's right here.\n\n"
        f"Wishing {p['business_name']} the best.\n\n"
        f"— {s.sender_name}"
    )
    return Message(channel="email", kind="followup_2", subject=f"Last note — {p['business_name']}", body=body)


def _whatsapp_initial(p: dict, opp: dict, s) -> Message:
    name = _first_name_from_business(p["business_name"])
    rev = _inr(opp.get("monthly_revenue_impact_inr"))
    service = opp["primary_service"]
    short = {
        "Website Development":       "build you a high-converting website",
        "Website Redesign":          "rebuild your site for better conversion",
        "AI Appointment Booking":    "add 24/7 online booking",
        "AI Chatbot":                "add an AI chat that answers in seconds",
        "AI Customer Support":       "offload L1 support to AI",
        "Lead Generation System":    "add a lead-capture + nurture loop",
        "WhatsApp Automation":       "automate your WhatsApp reminders & confirmations",
        "Analytics & CRM Integration": "set up analytics + CRM so you can measure",
    }.get(service, service.lower())
    rev_clause = f" (~{rev}/mo upside)" if rev else ""
    body = (
        f"Hi {name}, this is {s.sender_name} from {s.sender_company}. "
        f"Saw {p['business_name']} on Google — solid reviews. "
        f"We help {p.get('industry','local')} businesses {short}{rev_clause}. "
        f"Open to a quick 15-min call this week?"
    )
    return Message(channel="whatsapp", kind="initial", subject=None, body=body)


def _whatsapp_followup_1(p: dict, opp: dict, s) -> Message:
    name = _first_name_from_business(p["business_name"])
    body = (
        f"Hi {name}, just bumping the note above. Happy to send over a free 1-page "
        f"teardown of {p['business_name']}'s site if a call doesn't fit — your call."
    )
    return Message(channel="whatsapp", kind="followup_1", subject=None, body=body)


def _whatsapp_followup_2(p: dict, opp: dict, s) -> Message:
    name = _first_name_from_business(p["business_name"])
    body = (
        f"Hi {name}, last ping from me — if timing changes, reach out anytime. "
        f"Wishing {p['business_name']} the best!"
    )
    return Message(channel="whatsapp", kind="followup_2", subject=None, body=body)


def _linkedin_initial(p: dict, opp: dict, s) -> Message:
    name = _first_name_from_business(p["business_name"])
    rev = _inr(opp.get("monthly_revenue_impact_inr"))
    service_short = opp["primary_service"].lower()
    body = (
        f"Hi {name} — I help {p.get('industry','local')} businesses like "
        f"{p['business_name']} with {service_short}. Took a quick look at your site "
        f"and spotted a clear gap"
        + (f" worth ~{rev}/month if closed" if rev else "")
        + f". Worth a 15-minute call to walk through the specifics? — {s.sender_name}"
    )
    return Message(channel="linkedin", kind="initial", subject=None, body=body)


def _linkedin_followup_1(p: dict, opp: dict, s) -> Message:
    body = (
        f"Following up — happy to send a short teardown video of {p['business_name']}'s "
        f"site (no obligation) if a call's premature. Want me to record one?"
    )
    return Message(channel="linkedin", kind="followup_1", subject=None, body=body)


def _linkedin_followup_2(p: dict, opp: dict, s) -> Message:
    body = "Last note — closing the loop on my end. Good luck either way!"
    return Message(channel="linkedin", kind="followup_2", subject=None, body=body)


CHANNEL_BUILDERS = {
    ("email", "initial"):      _email_initial,
    ("email", "followup_1"):   _email_followup_1,
    ("email", "followup_2"):   _email_followup_2,
    ("whatsapp", "initial"):   _whatsapp_initial,
    ("whatsapp", "followup_1"): _whatsapp_followup_1,
    ("whatsapp", "followup_2"): _whatsapp_followup_2,
    ("linkedin", "initial"):   _linkedin_initial,
    ("linkedin", "followup_1"): _linkedin_followup_1,
    ("linkedin", "followup_2"): _linkedin_followup_2,
}


# ---------- orchestration ----------

def build_messages_for(prospect: dict, opportunity: dict, analysis: dict | None) -> list[Message]:
    s = get_settings()
    out: list[Message] = []
    for (channel, kind), builder in CHANNEL_BUILDERS.items():
        if (channel, kind) == ("email", "initial"):
            out.append(builder(prospect, opportunity, analysis, s))
        else:
            out.append(builder(prospect, opportunity, s))
    return out


def save_messages(prospect_id: str, opportunity_id: str | None, messages: list[Message]) -> tuple[int, int]:
    """Upsert by (prospect_id, channel, kind). Returns (inserted, updated)."""
    inserted = 0
    updated = 0
    for m in messages:
        existing = (
            client()
            .table("outreach_messages")
            .select("id, status")
            .eq("prospect_id", prospect_id)
            .eq("channel", m.channel)
            .eq("kind", m.kind)
            .limit(1)
            .execute()
            .data
        )
        payload = {
            "prospect_id": prospect_id,
            "opportunity_id": opportunity_id,
            "channel": m.channel,
            "kind": m.kind,
            "subject": m.subject,
            "body": m.body,
            "generated_by": "template",
        }
        if existing:
            # Don't overwrite an already approved or sent message.
            if existing[0]["status"] in {"approved", "sent"}:
                continue
            client().table("outreach_messages").update(payload).eq("id", existing[0]["id"]).execute()
            updated += 1
        else:
            client().table("outreach_messages").insert(payload).execute()
            inserted += 1
    return inserted, updated


def fetch_pending_prospects(limit: int) -> list[dict]:
    """Prospects with an opportunity but no draft messages yet."""
    rows = (
        client()
        .table("prospects")
        .select(
            "id, business_name, industry, location, phone, email, website, "
            "google_reviews, google_rating, "
            "opportunities(id, primary_service, monthly_revenue_impact_inr, "
            "our_one_time_inr, our_monthly_inr, roi_months, confidence, "
            "pitch_summary, hours_saved_per_week), "
            "website_analyses(error, http_status, has_viewport, has_booking, has_chat)"
        )
        .limit(limit * 5)
        .execute()
        .data
    )
    ids = [r["id"] for r in rows if r.get("opportunities")]
    if not ids:
        return []
    existing = (
        client()
        .table("outreach_messages")
        .select("prospect_id")
        .in_("prospect_id", ids)
        .execute()
        .data
    )
    done = {e["prospect_id"] for e in existing}
    pending = [r for r in rows if r.get("opportunities") and r["id"] not in done]
    return pending[:limit]


def write_for_prospect(prospect: dict) -> tuple[int, int]:
    opps = prospect.get("opportunities") or []
    if not opps:
        return (0, 0)
    opp = max(opps, key=lambda o: (o.get("monthly_revenue_impact_inr") or 0))
    analyses = prospect.get("website_analyses") or []
    analysis = analyses[0] if analyses else None
    messages = build_messages_for(prospect, opp, analysis)
    return save_messages(prospect["id"], opp.get("id"), messages)
