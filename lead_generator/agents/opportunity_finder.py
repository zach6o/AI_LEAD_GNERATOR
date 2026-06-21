"""Agent 3 — Opportunity Finder.

Takes a prospect + their website analysis and produces a concrete pitch with
NPR revenue impact, ROI months, and a confidence score the sales rep can trust.

The estimates are rule-based and intentionally conservative. The goal is to
give the outreach a number to anchor on ("we could add ~रू40k/month in
recovered bookings"), not to predict the future. All assumptions are stored
in the `reasoning` jsonb so a human can challenge them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ..db import client


# ---------- industry economics (NPR) ----------
# AOV = average order/visit value. "Missed factor" is the fraction of online
# discoverable demand we estimate the prospect is leaving on the table when
# the relevant feature is missing.
INDUSTRY: dict[str, dict[str, Any]] = {
    "dentist":        {"aov": 3000,    "missed_no_site": 0.08, "missed_no_booking": 0.05, "missed_no_chat": 0.02},
    "clinic":         {"aov": 1500,    "missed_no_site": 0.08, "missed_no_booking": 0.05, "missed_no_chat": 0.02},
    "hotel":          {"aov": 4000,    "missed_no_site": 0.12, "missed_no_booking": 0.08, "missed_no_chat": 0.02},
    "salon":          {"aov": 1500,    "missed_no_site": 0.06, "missed_no_booking": 0.04, "missed_no_chat": 0.02},
    "gym":            {"aov": 2000,    "missed_no_site": 0.06, "missed_no_booking": 0.04, "missed_no_chat": 0.02},
    "restaurant":     {"aov": 500,     "missed_no_site": 0.05, "missed_no_booking": 0.02, "missed_no_chat": 0.01},
    "law_firm":       {"aov": 10000,   "missed_no_site": 0.10, "missed_no_booking": 0.06, "missed_no_chat": 0.04},
    "real_estate":    {"aov": 200000,  "missed_no_site": 0.04, "missed_no_booking": 0.02, "missed_no_chat": 0.03},
    "construction":   {"aov": 200000,  "missed_no_site": 0.02, "missed_no_booking": 0.01, "missed_no_chat": 0.02},
    "education":      {"aov": 15000,   "missed_no_site": 0.06, "missed_no_booking": 0.02, "missed_no_chat": 0.02},
    "ecommerce":      {"aov": 1500,    "missed_no_site": 0.12, "missed_no_booking": 0.00, "missed_no_chat": 0.04},
    "local_business": {"aov": 2000,    "missed_no_site": 0.06, "missed_no_booking": 0.02, "missed_no_chat": 0.02},
    "other":          {"aov": 2000,    "missed_no_site": 0.06, "missed_no_booking": 0.02, "missed_no_chat": 0.02},
}

# Realistic monthly throughput ceilings per SMB type. Even very busy clinics
# rarely cross these.
MAX_MONTHLY_CUSTOMERS: dict[str, int] = {
    "dentist":        500,
    "clinic":         800,
    "hotel":          400,
    "salon":          800,
    "gym":            500,
    "restaurant":     3000,
    "law_firm":       150,
    "real_estate":    30,
    "construction":   20,
    "education":      300,
    "ecommerce":      2000,
    "local_business": 600,
    "other":          400,
}

# ---------- our pricing (NPR) ----------
PRICING: dict[str, dict[str, int]] = {
    "Website Development":         {"one_time": 40000, "monthly": 0},
    "Website Redesign":            {"one_time": 25000, "monthly": 0},
    "AI Chatbot":                  {"one_time": 15000, "monthly": 2000},
    "AI Customer Support":         {"one_time": 10000, "monthly": 3000},
    "AI Appointment Booking":      {"one_time": 10000, "monthly": 1500},
    "AI Lead Qualification":       {"one_time": 15000, "monthly": 2500},
    "AI Automation":               {"one_time": 20000, "monthly": 3000},
    "CRM Integration":             {"one_time": 15000, "monthly": 1000},
    "WhatsApp Automation":         {"one_time": 12000, "monthly": 2000},
    "Business Process Automation": {"one_time": 25000, "monthly": 3000},
    "Lead Generation System":      {"one_time": 20000, "monthly": 2500},
    "Analytics & CRM Integration": {"one_time": 15000, "monthly": 1000},
}


@dataclass
class Opportunity:
    prospect_id: str
    website_analysis_id: str | None
    primary_service: str
    secondary_services: list[str]
    monthly_revenue_impact_inr: int | None
    hours_saved_per_week: int | None
    our_one_time_inr: int
    our_monthly_inr: int
    roi_months: float | None
    confidence: int
    pitch_summary: str
    reasoning: dict[str, Any] = field(default_factory=dict)


def _estimated_monthly_customers(reviews: int | None, industry: str) -> int:
    """Reviews are a coarse, lifetime-accumulated signal — not annual.

    Assumptions:
      - ~7% of customers leave a Google review (1/0.07 ~= 14 customers per review)
      - reviews accumulated over ~4 years of operation (48 months)
      - capped at industry-specific physical throughput

    Solo SMB rarely exceeds ~200 customers/month; a busy multi-chair clinic
    can hit ~500. We never project past those ceilings just because the
    business has been around for a long time.
    """
    cap = MAX_MONTHLY_CUSTOMERS.get(industry, MAX_MONTHLY_CUSTOMERS["other"])
    if not reviews or reviews <= 0:
        return min(40, cap)
    lifetime_customers = reviews * 14
    monthly = lifetime_customers // 48
    return max(40, min(cap, monthly))


def _pick_primary_service(prospect: dict, analysis: dict | None) -> tuple[str, list[str]]:
    """Choose the lead pitch + a couple of supporting up-sells."""
    has_site = bool(prospect.get("website"))
    if not has_site:
        return ("Website Development", ["AI Chatbot", "AI Appointment Booking"])

    if analysis is None:
        return ("Website Redesign", ["AI Chatbot"])

    # Site dead / 4xx / 5xx
    if analysis.get("error") or (analysis.get("http_status") and analysis["http_status"] >= 400):
        return ("Website Development", ["AI Chatbot", "AI Appointment Booking"])

    # Stale or unresponsive design
    not_responsive = not analysis.get("has_viewport")
    stale_year = (analysis.get("copyright_year") or 9999) < 2024
    if not_responsive or stale_year:
        return ("Website Redesign", ["AI Chatbot", "AI Appointment Booking"])

    # Existing site but missing conversion features
    if not analysis.get("has_booking"):
        return ("AI Appointment Booking", ["AI Chatbot", "WhatsApp Automation"])
    if not analysis.get("has_chat"):
        return ("AI Chatbot", ["WhatsApp Automation", "AI Customer Support"])
    if not analysis.get("has_lead_form"):
        return ("Lead Generation System", ["AI Chatbot"])
    if not analysis.get("has_analytics"):
        return ("Analytics & CRM Integration", ["AI Chatbot"])

    # Healthy site — soft up-sell on automation.
    return ("WhatsApp Automation", ["AI Customer Support", "CRM Integration"])


def _estimate(prospect: dict, analysis: dict | None, service: str) -> dict[str, Any]:
    industry = prospect.get("industry") or "other"
    econ = INDUSTRY.get(industry, INDUSTRY["other"])
    aov = econ["aov"]
    monthly_customers = _estimated_monthly_customers(prospect.get("google_reviews"), industry)

    monthly_revenue_impact = 0
    hours_saved = 0
    reasoning_extra: dict[str, Any] = {}

    if service == "Website Development":
        # No site -> recover discoverable demand.
        factor = econ["missed_no_site"]
        monthly_revenue_impact = int(monthly_customers * aov * factor)
        reasoning_extra["assumption"] = (
            f"Without a working website the prospect loses ~{int(factor*100)}% "
            f"of online-discoverable demand."
        )

    elif service == "Website Redesign":
        # Conversion uplift on existing site.
        uplift = 0.10
        monthly_revenue_impact = int(monthly_customers * aov * uplift)
        reasoning_extra["assumption"] = f"Modern redesign typically lifts conversion by ~{int(uplift*100)}%."

    elif service == "AI Appointment Booking":
        factor = econ["missed_no_booking"]
        monthly_revenue_impact = int(monthly_customers * aov * factor)
        hours_saved = 8
        reasoning_extra["assumption"] = (
            f"24/7 self-serve booking recovers ~{int(factor*100)}% of inquiries that "
            f"currently drop off after hours, and saves ~8 hrs/week of phone time."
        )

    elif service == "AI Chatbot":
        factor = econ["missed_no_chat"]
        monthly_revenue_impact = int(monthly_customers * aov * factor)
        hours_saved = 6
        reasoning_extra["assumption"] = (
            f"Instant answers convert ~{int(factor*100)}% of cold visitors who "
            f"otherwise bounce, and offload ~6 hrs/week of repetitive Qs."
        )

    elif service == "AI Customer Support":
        hours_saved = 20
        # Cost saved ~= 20 hrs/wk × रू200/hr × 4.3 weeks = ~रू17k/mo
        monthly_revenue_impact = 17000
        reasoning_extra["assumption"] = (
            "Replaces ~20 hrs/week of L1 support load; framed here as cost saved."
        )

    elif service == "Lead Generation System":
        # Capture forms + nurture; estimate as small fraction of monthly revenue.
        monthly_revenue_impact = int(monthly_customers * aov * 0.10)
        reasoning_extra["assumption"] = "Lead capture + nurture typically adds ~10% pipeline."

    elif service == "WhatsApp Automation":
        hours_saved = 10
        monthly_revenue_impact = int(monthly_customers * aov * 0.05)
        reasoning_extra["assumption"] = (
            "WhatsApp templates for reminders, confirmations, re-engagement "
            "recover ~5% revenue and save ~10 hrs/week."
        )

    elif service == "Analytics & CRM Integration":
        monthly_revenue_impact = int(monthly_customers * aov * 0.05)
        reasoning_extra["assumption"] = (
            "Measurement unlocks ~5% revenue via targeted retention."
        )

    pricing = PRICING.get(service, {"one_time": 20000, "monthly": 2000})
    one_time = pricing["one_time"]
    monthly = pricing["monthly"]

    roi_months: float | None = None
    if monthly_revenue_impact and monthly_revenue_impact > 0:
        net_first_month = monthly_revenue_impact - monthly
        if net_first_month > 0:
            roi_months = round(one_time / net_first_month, 2)

    return {
        "industry": industry,
        "aov_inr": aov,
        "estimated_monthly_customers": monthly_customers,
        "monthly_revenue_impact_inr": monthly_revenue_impact,
        "hours_saved_per_week": hours_saved,
        "our_one_time_inr": one_time,
        "our_monthly_inr": monthly,
        "roi_months": roi_months,
        **reasoning_extra,
    }


def _confidence(prospect: dict, analysis: dict | None, est: dict) -> int:
    score = 40
    reviews = prospect.get("google_reviews") or 0
    if reviews >= 500:    score += 25
    elif reviews >= 100:  score += 15
    elif reviews >= 20:   score += 5

    if analysis is not None:
        score += 15           # we actually fetched their site
        if not analysis.get("error"):
            score += 5
    if prospect.get("phone"):
        score += 5
    if est.get("monthly_revenue_impact_inr", 0) >= 10000:
        score += 10
    return max(0, min(100, score))


def _pitch_summary(prospect: dict, analysis: dict | None, service: str, est: dict) -> str:
    name = prospect.get("business_name") or "your business"
    impact = est.get("monthly_revenue_impact_inr") or 0
    rev_str = f"रू{impact:,}/month" if impact else "measurable upside"
    industry = (prospect.get("industry") or "other").replace("_", " ")

    if service == "Website Development":
        return (
            f"{name} doesn't have a working website. For a typical {industry} "
            f"of your size that's around {rev_str} in missed online demand. "
            f"We can build a high-converting site in 2-3 weeks."
        )
    if service == "Website Redesign":
        return (
            f"{name}'s current site isn't pulling its weight — a modern "
            f"redesign typically lifts conversions ~10%, worth around {rev_str} "
            f"for a {industry} of your scale."
        )
    if service == "AI Appointment Booking":
        return (
            f"{name} doesn't have online booking. 24/7 self-serve booking "
            f"recovers after-hours inquiries (~{rev_str}) and frees ~"
            f"{est.get('hours_saved_per_week', 0)} hrs/week of staff time."
        )
    if service == "AI Chatbot":
        return (
            f"Adding an AI chatbot to {name} captures ~{rev_str} from "
            f"visitors who currently bounce, and handles ~"
            f"{est.get('hours_saved_per_week', 0)} hrs/week of repetitive Qs."
        )
    if service == "AI Customer Support":
        return (
            f"AI handles your L1 support 24/7 — saves ~"
            f"{est.get('hours_saved_per_week', 0)} hrs/week (~{rev_str}) "
            f"and your team focuses on real cases."
        )
    if service == "Lead Generation System":
        return (
            f"{name} has no lead-capture loop. Adding forms + nurture "
            f"sequences typically adds ~{rev_str} in pipeline."
        )
    if service == "WhatsApp Automation":
        return (
            f"Automated WhatsApp reminders, confirmations and re-engagement "
            f"for {name} — ~{rev_str} recovered and ~"
            f"{est.get('hours_saved_per_week', 0)} hrs/week saved."
        )
    if service == "Analytics & CRM Integration":
        return (
            f"{name} has no analytics installed. Once we can measure, we can "
            f"target — typically ~{rev_str} from better retention."
        )
    return f"We can help {name} with {service} — estimated upside {rev_str}."


def find_opportunity(prospect: dict, analysis: dict | None) -> Opportunity:
    primary, secondary = _pick_primary_service(prospect, analysis)
    est = _estimate(prospect, analysis, primary)
    confidence = _confidence(prospect, analysis, est)
    summary = _pitch_summary(prospect, analysis, primary, est)

    reasoning = {
        **est,
        "secondary_services": secondary,
        "rule_set_version": 1,
    }

    return Opportunity(
        prospect_id=prospect["id"],
        website_analysis_id=(analysis or {}).get("id"),
        primary_service=primary,
        secondary_services=secondary,
        monthly_revenue_impact_inr=est["monthly_revenue_impact_inr"] or None,
        hours_saved_per_week=est["hours_saved_per_week"] or None,
        our_one_time_inr=est["our_one_time_inr"],
        our_monthly_inr=est["our_monthly_inr"],
        roi_months=est["roi_months"],
        confidence=confidence,
        pitch_summary=summary,
        reasoning=reasoning,
    )


# ---------- persistence ----------

def save_opportunity(opp: Opportunity) -> tuple[str, bool]:
    payload = asdict(opp)
    # Upsert on (prospect_id, primary_service) so re-runs replace.
    existing = (
        client()
        .table("opportunities")
        .select("id")
        .eq("prospect_id", opp.prospect_id)
        .eq("primary_service", opp.primary_service)
        .limit(1)
        .execute()
        .data
    )
    if existing:
        oid = existing[0]["id"]
        client().table("opportunities").update(payload).eq("id", oid).execute()
        return oid, False
    res = client().table("opportunities").insert(payload).execute()
    return res.data[0]["id"], True


def fetch_pending_prospects(limit: int) -> list[dict]:
    """Prospects that have at least one analysis but no opportunity yet,
    OR prospects with no website (they can still be pitched Website Dev)."""
    prospects = (
        client()
        .table("prospects")
        .select(
            "id, business_name, industry, website, phone, google_reviews, "
            "google_rating, website_analyses(id, error, http_status, has_viewport, "
            "has_title, has_meta_desc, has_chat, has_booking, has_lead_form, "
            "has_analytics, copyright_year)"
        )
        .limit(limit * 5)
        .execute()
        .data
    )
    ids = [p["id"] for p in prospects]
    if not ids:
        return []
    existing_opps = (
        client()
        .table("opportunities")
        .select("prospect_id")
        .in_("prospect_id", ids)
        .execute()
        .data
    )
    done = {o["prospect_id"] for o in existing_opps}
    pending = [p for p in prospects if p["id"] not in done]
    return pending[:limit]
