"""Find a usable email address for a prospect by scraping their own website.

Strategy (in priority order):
  1. mailto: links on the homepage and common contact paths
  2. Plain email patterns in the same pages
  3. Tie-break by preferring info@/contact@/hello@ over personal-looking ones,
     and rejecting noise (noreply@, wordpress@, png/jpg false positives).

Most Indian SMB sites surface the email on /contact, /about, or in the footer.
We try a small fixed set of paths rather than crawling the whole site.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from ..db import client
from ..sources.website_fetcher import USER_AGENT, _normalize_url


CONTACT_PATHS = ["", "/contact", "/contact-us", "/contactus", "/contact_us",
                 "/about", "/about-us", "/reach-us", "/get-in-touch", "/book"]

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# Substrings that mean "definitely not a real contact email for this business".
JUNK_PATTERNS = {
    "noreply", "no-reply", "donotreply", "wordpress@", "example.com",
    "sentry.io", "wixpress.com", ".png", ".jpg", ".jpeg", ".gif", ".svg",
    ".webp", "u003e", "u003c", "sentry-next.io",
}

# Substrings on the *local part* (before @) that mean a generic business inbox.
PREFERRED_LOCALPARTS = ("info", "contact", "hello", "hi", "team", "support",
                        "admin", "reception", "appointments", "booking")


@dataclass
class EnrichmentResult:
    prospect_id: str
    email: str | None
    candidates: list[str]
    pages_tried: int
    error: str | None = None


def _is_junky(email: str) -> bool:
    e = email.lower()
    if any(p in e for p in JUNK_PATTERNS):
        return True
    # Reject things like "x.png@something" (image filenames misparsed)
    if e.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")):
        return True
    if len(e) > 80:
        return True
    return False


def _candidate_score(email: str, site_host: str) -> int:
    """Higher = better. Prefer info@<their-domain> over personal.gmail.com."""
    e = email.lower()
    local, _, host = e.partition("@")
    score = 0
    if site_host and host.endswith(site_host.lower()):
        score += 30          # email at the same domain as the website
    if local in PREFERRED_LOCALPARTS:
        score += 20
    if "gmail.com" in host or "yahoo." in host or "hotmail." in host:
        score += 5           # personal mailbox better than nothing
    # Penalize names that look like long noisy strings.
    score -= max(0, len(local) - 25)
    return score


def _extract_emails(html: str) -> set[str]:
    """Return distinct email candidates from a page (mailto + body matches)."""
    found: set[str] = set()
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().startswith("mailto:"):
            addr = href.split(":", 1)[1].split("?", 1)[0].strip()
            # Skip malformed mailto: that wrap a tel: link or other garbage.
            if addr and EMAIL_RE.fullmatch(addr):
                found.add(addr)
    for m in EMAIL_RE.findall(html):
        found.add(m)
    return found


def _fetch(url: str, http: httpx.Client) -> str | None:
    try:
        r = http.get(url)
        if r.status_code >= 400:
            return None
        ctype = r.headers.get("content-type", "")
        if "html" not in ctype.lower():
            return None
        return r.text
    except httpx.HTTPError:
        return None


def enrich_prospect(prospect: dict) -> EnrichmentResult:
    pid = prospect["id"]
    website = prospect.get("website") or ""
    if not website:
        return EnrichmentResult(prospect_id=pid, email=None, candidates=[], pages_tried=0,
                                error="no website")

    base = _normalize_url(website)
    host = urlparse(base).hostname or ""
    site_host = host.replace("www.", "")

    candidates: set[str] = set()
    pages_tried = 0
    error: str | None = None

    headers = {"User-Agent": USER_AGENT, "Accept": "text/html"}
    try:
        with httpx.Client(timeout=15.0, follow_redirects=True, headers=headers) as http:
            for path in CONTACT_PATHS:
                url = urljoin(base, path)
                html = _fetch(url, http)
                pages_tried += 1
                if not html:
                    continue
                for e in _extract_emails(html):
                    if not _is_junky(e):
                        candidates.add(e)
                if path == "" and not candidates:
                    # If homepage has nothing useful, check 1-2 contact paths.
                    continue
                if candidates and pages_tried >= 3:
                    break    # we have hits + tried enough pages
    except Exception as e:
        error = f"{type(e).__name__}: {e}"

    if not candidates:
        return EnrichmentResult(prospect_id=pid, email=None, candidates=[],
                                pages_tried=pages_tried, error=error)

    ranked = sorted(candidates, key=lambda c: _candidate_score(c, site_host), reverse=True)
    best = ranked[0]
    return EnrichmentResult(prospect_id=pid, email=best, candidates=ranked,
                            pages_tried=pages_tried, error=error)


def save_enrichment(res: EnrichmentResult) -> None:
    if res.email:
        client().table("prospects").update({"email": res.email}).eq("id", res.prospect_id).execute()


def fetch_pending_prospects(limit: int, *, overwrite: bool = False) -> list[dict]:
    """Prospects with a website but no email yet (or all, if overwrite)."""
    q = (
        client()
        .table("prospects")
        .select("id, business_name, website, email")
        .not_.is_("website", "null")
        .limit(limit * 3)
    )
    rows = q.execute().data
    if not overwrite:
        rows = [r for r in rows if not r.get("email")]
    return rows[:limit]


def enrich_all(limit: int, *, overwrite: bool = False) -> list[EnrichmentResult]:
    targets = fetch_pending_prospects(limit, overwrite=overwrite)
    out: list[EnrichmentResult] = []
    for p in targets:
        res = enrich_prospect(p)
        save_enrichment(res)
        out.append(res)
    return out
