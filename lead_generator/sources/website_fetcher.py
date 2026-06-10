from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup

DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=10.0)
USER_AGENT = (
    "Mozilla/5.0 (compatible; LeadGenAnalyzer/0.1; +https://example.com/bot)"
)

# Pattern -> tech label. Conservative; false positives are cheap, false
# negatives mean we miss a pitch angle.
CHAT_PATTERNS = {
    "tawk.to":         "Tawk.to",
    "intercom.io":     "Intercom",
    "widget.intercom": "Intercom",
    "crisp.chat":      "Crisp",
    "drift.com":       "Drift",
    "livechatinc.com": "LiveChat",
    "zopim.com":       "Zendesk Chat",
    "zendesk":         "Zendesk",
    "messenger.com":   "FB Messenger",
    "wa.me/":          "WhatsApp link",
    "api.whatsapp.com": "WhatsApp link",
    "freshchat":       "Freshchat",
    "hubspot":         "HubSpot",
}

BOOKING_PATTERNS = {
    "calendly.com":         "Calendly",
    "calendar.app.google":  "Google Calendar booking",
    "acuityscheduling.com": "Acuity",
    "setmore.com":          "Setmore",
    "simplybook":           "SimplyBook",
    "practo.com/book":      "Practo Book",
    "fresha.com":           "Fresha",
    "doctolib":             "Doctolib",
}

ANALYTICS_PATTERNS = {
    "google-analytics.com": "Google Analytics",
    "googletagmanager.com": "Google Tag Manager",
    "gtag(":                "gtag.js",
    "facebook.com/tr":      "Meta Pixel",
    "connect.facebook.net": "Meta Pixel",
    "hotjar.com":           "Hotjar",
    "clarity.ms":           "MS Clarity",
    "mixpanel":             "Mixpanel",
    "segment.com":          "Segment",
    "plausible":            "Plausible",
}

CMS_PATTERNS = {
    "wp-content":         "WordPress",
    "wp-includes":        "WordPress",
    "cdn.shopify.com":    "Shopify",
    "wixstatic.com":      "Wix",
    "squarespace":        "Squarespace",
    "webflow":            "Webflow",
    "duda.co":            "Duda",
    "cdn.shopify":        "Shopify",
}


@dataclass
class FetchResult:
    url: str
    final_url: str | None = None
    http_status: int | None = None
    ssl_valid: bool = False
    response_ms: int | None = None
    page_bytes: int | None = None
    html: str | None = None
    error: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


def _normalize_url(url: str) -> str:
    """Ensure the URL has a scheme; default to https. Exposed for reuse."""
    u = url.strip()
    if not re.match(r"^https?://", u, re.IGNORECASE):
        u = "https://" + u
    return u


# Public re-export (without the leading underscore) for code outside this module.
normalize_url = _normalize_url


def fetch_site(url: str) -> FetchResult:
    target = _normalize_url(url)
    out = FetchResult(url=target)
    start = time.perf_counter()
    try:
        with httpx.Client(
            timeout=DEFAULT_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"},
        ) as client:
            r = client.get(target)
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        out.final_url = str(r.url)
        out.http_status = r.status_code
        out.response_ms = elapsed_ms
        out.headers = {k.lower(): v for k, v in r.headers.items()}
        out.page_bytes = len(r.content)
        out.ssl_valid = str(r.url).lower().startswith("https://")
        # Only keep HTML bodies — skip binary/PDF garbage.
        ctype = out.headers.get("content-type", "")
        if "html" in ctype:
            out.html = r.text
    except httpx.HTTPError as e:
        out.error = f"{type(e).__name__}: {e}"
    except Exception as e:  # pragma: no cover — defensive
        out.error = f"{type(e).__name__}: {e}"
    return out


@dataclass
class Detected:
    has_viewport: bool = False
    has_title: bool = False
    has_meta_desc: bool = False
    has_og_tags: bool = False
    has_favicon: bool = False
    has_lead_form: bool = False
    has_chat: bool = False
    has_booking: bool = False
    has_analytics: bool = False
    copyright_year: int | None = None
    tech_detected: list[str] = field(default_factory=list)


def _add_unique(items: list[str], item: str) -> None:
    if item not in items:
        items.append(item)


def detect_signals(html: str) -> Detected:
    out = Detected()
    soup = BeautifulSoup(html, "lxml")
    lower_html = html.lower()

    # HTML hygiene
    out.has_viewport = bool(
        soup.find("meta", attrs={"name": lambda v: v and v.lower() == "viewport"})
    )
    title = soup.find("title")
    out.has_title = bool(title and title.get_text(strip=True))
    out.has_meta_desc = bool(
        soup.find("meta", attrs={"name": lambda v: v and v.lower() == "description"})
    )
    out.has_og_tags = bool(
        soup.find("meta", attrs={"property": lambda v: v and v.startswith("og:")})
    )
    out.has_favicon = bool(
        soup.find("link", rel=lambda v: v and "icon" in " ".join(v).lower() if isinstance(v, list) else (v and "icon" in v.lower()))
    )

    # Lead form: any <form> containing an email-ish input.
    for form in soup.find_all("form"):
        inputs = form.find_all("input")
        for inp in inputs:
            t = (inp.get("type") or "").lower()
            n = (inp.get("name") or "").lower()
            ph = (inp.get("placeholder") or "").lower()
            if t == "email" or "email" in n or "email" in ph:
                out.has_lead_form = True
                break
        if out.has_lead_form:
            break

    # Pattern-based feature detection.
    for pat, label in CHAT_PATTERNS.items():
        if pat in lower_html:
            out.has_chat = True
            _add_unique(out.tech_detected, label)
    for pat, label in BOOKING_PATTERNS.items():
        if pat in lower_html:
            out.has_booking = True
            _add_unique(out.tech_detected, label)
    for pat, label in ANALYTICS_PATTERNS.items():
        if pat in lower_html:
            out.has_analytics = True
            _add_unique(out.tech_detected, label)
    for pat, label in CMS_PATTERNS.items():
        if pat in lower_html:
            _add_unique(out.tech_detected, label)

    # Copyright year — look for "© 2024" / "Copyright 2023" patterns.
    years = [int(y) for y in re.findall(r"(?:©|copyright|&copy;)[^\d]{0,8}(20\d{2})", lower_html)]
    if years:
        out.copyright_year = max(years)
    else:
        # Fallback: any "20YY" near the footer is a rough signal.
        footer = soup.find("footer")
        if footer:
            ys = [int(y) for y in re.findall(r"20\d{2}", footer.get_text(" ", strip=True))]
            if ys:
                out.copyright_year = max(ys)

    return out


def current_year() -> int:
    return datetime.now(timezone.utc).year
