from __future__ import annotations

from dataclasses import asdict, dataclass

from ..db import client
from ..sources.website_fetcher import (
    Detected,
    FetchResult,
    current_year,
    detect_signals,
    fetch_site,
)


# Each tuple: (weight, predicate) — weights sum to <=100.
# Higher final_score = healthier site = LOWER opportunity for us.
SCORE_WEIGHTS = {
    "ssl_valid":     15,
    "has_viewport":  10,
    "has_title":      5,
    "has_meta_desc":  5,
    "has_og_tags":    5,
    "has_favicon":    3,
    "has_analytics":  7,
    "has_lead_form": 10,
    "has_chat":       8,
    "has_booking":   10,
    "fast":           7,   # response_ms < 1500
    "small":          5,   # page_bytes < 2 MB
    "fresh":          5,   # copyright_year within last 1 year
    "ok_status":      5,   # 200 <= http_status < 300
}


@dataclass
class Analysis:
    prospect_id: str
    url: str
    http_status: int | None
    ssl_valid: bool
    response_ms: int | None
    page_bytes: int | None

    has_viewport: bool
    has_title: bool
    has_meta_desc: bool
    has_og_tags: bool
    has_favicon: bool

    has_lead_form: bool
    has_chat: bool
    has_booking: bool
    has_analytics: bool

    copyright_year: int | None
    tech_detected: list[str]

    issues: list[str]
    recommended_service: str | None
    final_score: int
    error: str | None


def _score(fetch: FetchResult, det: Detected) -> int:
    score = 0
    if fetch.ssl_valid:                                       score += SCORE_WEIGHTS["ssl_valid"]
    if det.has_viewport:                                      score += SCORE_WEIGHTS["has_viewport"]
    if det.has_title:                                         score += SCORE_WEIGHTS["has_title"]
    if det.has_meta_desc:                                     score += SCORE_WEIGHTS["has_meta_desc"]
    if det.has_og_tags:                                       score += SCORE_WEIGHTS["has_og_tags"]
    if det.has_favicon:                                       score += SCORE_WEIGHTS["has_favicon"]
    if det.has_analytics:                                     score += SCORE_WEIGHTS["has_analytics"]
    if det.has_lead_form:                                     score += SCORE_WEIGHTS["has_lead_form"]
    if det.has_chat:                                          score += SCORE_WEIGHTS["has_chat"]
    if det.has_booking:                                       score += SCORE_WEIGHTS["has_booking"]
    if fetch.response_ms is not None and fetch.response_ms < 1500: score += SCORE_WEIGHTS["fast"]
    if fetch.page_bytes is not None and fetch.page_bytes < 2_000_000: score += SCORE_WEIGHTS["small"]
    if det.copyright_year and det.copyright_year >= current_year() - 1: score += SCORE_WEIGHTS["fresh"]
    if fetch.http_status and 200 <= fetch.http_status < 300:  score += SCORE_WEIGHTS["ok_status"]
    return max(0, min(100, score))


def _issues_and_pitch(fetch: FetchResult, det: Detected) -> tuple[list[str], str | None]:
    """Translate flags into human-readable issues + a first-pass pitch."""
    issues: list[str] = []

    if fetch.error:
        issues.append(f"Site unreachable ({fetch.error[:80]})")
    if fetch.http_status and fetch.http_status >= 400:
        issues.append(f"HTTP {fetch.http_status} on homepage")
    if not fetch.ssl_valid:
        issues.append("No SSL (site served over http)")
    if not det.has_viewport:
        issues.append("Not mobile-responsive (no viewport meta)")
    if not det.has_title:
        issues.append("Missing <title> tag")
    if not det.has_meta_desc:
        issues.append("Missing meta description (bad SEO)")
    if not det.has_og_tags:
        issues.append("No Open Graph tags (poor social sharing)")
    if not det.has_analytics:
        issues.append("No analytics installed")
    if not det.has_lead_form:
        issues.append("No lead capture form on homepage")
    if not det.has_chat:
        issues.append("No live chat / messaging widget")
    if not det.has_booking:
        issues.append("No online booking system")
    if fetch.response_ms is not None and fetch.response_ms >= 3000:
        issues.append(f"Slow homepage ({fetch.response_ms} ms)")
    if fetch.page_bytes is not None and fetch.page_bytes > 3_000_000:
        kb = fetch.page_bytes // 1024
        issues.append(f"Heavy homepage ({kb} KB)")
    if det.copyright_year and det.copyright_year < current_year() - 2:
        issues.append(f"Copyright year is {det.copyright_year} — site looks abandoned")

    # First-pass pitch: pick the most lucrative angle present.
    pitch: str | None = None
    if fetch.error or (fetch.http_status and fetch.http_status >= 400):
        pitch = "Website Development"
    elif det.copyright_year and det.copyright_year < current_year() - 2:
        pitch = "Website Redesign"
    elif not det.has_viewport:
        pitch = "Website Redesign"
    elif not det.has_booking:
        pitch = "AI Appointment Booking"
    elif not det.has_chat:
        pitch = "AI Chatbot"
    elif not det.has_lead_form:
        pitch = "Lead Generation System"
    elif not det.has_analytics:
        pitch = "Analytics & CRM Integration"

    return issues, pitch


def analyze_prospect(prospect_id: str, website: str) -> Analysis:
    fetch = fetch_site(website)
    det = Detected()
    if fetch.html:
        det = detect_signals(fetch.html)

    issues, pitch = _issues_and_pitch(fetch, det)
    final = _score(fetch, det)

    analysis = Analysis(
        prospect_id=prospect_id,
        url=fetch.url,
        http_status=fetch.http_status,
        ssl_valid=fetch.ssl_valid,
        response_ms=fetch.response_ms,
        page_bytes=fetch.page_bytes,
        has_viewport=det.has_viewport,
        has_title=det.has_title,
        has_meta_desc=det.has_meta_desc,
        has_og_tags=det.has_og_tags,
        has_favicon=det.has_favicon,
        has_lead_form=det.has_lead_form,
        has_chat=det.has_chat,
        has_booking=det.has_booking,
        has_analytics=det.has_analytics,
        copyright_year=det.copyright_year,
        tech_detected=det.tech_detected,
        issues=issues,
        recommended_service=pitch,
        final_score=final,
        error=fetch.error,
    )
    return analysis


def save_analysis(a: Analysis) -> None:
    row = asdict(a)
    # `raw` carries the raw structured findings for debugging.
    raw = {
        "final_url": None,
        "tech_detected": a.tech_detected,
    }
    payload = {**row, "raw": raw}
    client().table("website_analyses").insert(payload).execute()

    # Reflect findings back onto the prospect.
    client().table("prospects").update(
        {
            "stage": "analyzed",
            "issues_found": a.issues,
            "recommended_service": a.recommended_service,
        }
    ).eq("id", a.prospect_id).execute()


def fetch_pending_prospects(limit: int) -> list[dict]:
    """Prospects that have a website but no analysis yet."""
    # Get prospects with websites.
    candidates = (
        client()
        .table("prospects")
        .select("id, business_name, website")
        .not_.is_("website", "null")
        .limit(limit * 5)        # over-fetch; some will already be analyzed
        .execute()
        .data
    )
    if not candidates:
        return []

    ids = [c["id"] for c in candidates]
    analyzed = (
        client()
        .table("website_analyses")
        .select("prospect_id")
        .in_("prospect_id", ids)
        .execute()
        .data
    )
    done = {a["prospect_id"] for a in analyzed}
    pending = [c for c in candidates if c["id"] not in done]
    return pending[:limit]
