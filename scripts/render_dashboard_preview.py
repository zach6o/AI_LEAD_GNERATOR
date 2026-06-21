"""Render the dashboard with mock data into a static HTML file.

This bypasses Supabase so we can preview the panel offline.
Output: E:/PROJECTS/AI_LEAD_GENERATOR/.dashboard_preview.html
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import quote

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "lead_generator" / "web" / "templates"


def _npr(value):
    if value is None or value == "":
        return "—"
    try:
        return f"रू{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def _wa_link(phone, body):
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 10:
        return ""
    return f"https://wa.me/{digits}?text={quote(body or '', safe='')}"


PIPELINE_STAGES = [
    "new", "analyzed", "approved", "contacted", "replied",
    "demo_scheduled", "proposal_sent", "won", "lost",
]
STAGE_COLORS = {
    "new":            "bg-slate-100 text-slate-700",
    "analyzed":       "bg-blue-100 text-blue-700",
    "approved":       "bg-indigo-100 text-indigo-700",
    "contacted":      "bg-purple-100 text-purple-700",
    "replied":        "bg-pink-100 text-pink-700",
    "demo_scheduled": "bg-amber-100 text-amber-700",
    "proposal_sent":  "bg-orange-100 text-orange-700",
    "won":            "bg-emerald-100 text-emerald-700",
    "lost":           "bg-rose-100 text-rose-700",
}
INTENT_COLORS = {
    "interested":     "bg-emerald-100 text-emerald-700",
    "question":       "bg-amber-100 text-amber-700",
    "unclear":        "bg-slate-100 text-slate-600",
    "not_interested": "bg-rose-100 text-rose-700",
    "unsubscribe":    "bg-rose-200 text-rose-800",
    "auto_reply":     "bg-slate-100 text-slate-500",
}


def make_row(name, industry, phone, website, score, site_score, rev, roi, pitch, stage, location="Kathmandu, Nepal", rating=4.2, reviews=120):
    return {
        "id": f"id-{name.lower().replace(' ','-')[:12]}",
        "business_name": name,
        "industry": industry,
        "location": location,
        "website": website,
        "phone": phone,
        "email": None,
        "lead_score": score,
        "stage": stage,
        "recommended_service": pitch,
        "last_contacted": None,
        "notes": "",
        "google_rating": rating,
        "google_reviews": reviews,
        "analysis": {"final_score": site_score} if site_score else None,
        "opportunity": (
            {
                "primary_service": pitch,
                "monthly_revenue_impact_inr": rev,
                "roi_months": roi,
            }
            if rev
            else None
        ),
        "_rev": rev or 0,
        "messages": {},
        "replies": [],
    }


def main() -> None:
    rows = [
        make_row("Sagarmatha Dental Clinic",  "dentist",    "+977 9801-111111", None,                            85, 62, 45000, 1.8, "chatbot",          "new",            "Thamel, Kathmandu",    3.9, 142),
        make_row("Bhojan Griha Restaurant",   "restaurant", "+977 9801-222222", "https://bhojangriha.example",   70, 48, 28000, 2.4, "redesign",         "new",            "Dilli Bazar, Kathmandu",3.6, 312),
        make_row("Lotus Lake Hotel Pokhara",  "hotel",      "+977 9802-333333", "https://lotuslake.example",     78, 71, 62000, 1.4, "booking-widget",   "analyzed",       "Lakeside, Pokhara",    4.1, 540),
        make_row("Pearl Beauty Salon",        "salon",      "+977 9801-444444", None,                            65, None, 15000, 3.0, "website + chat",  "new",            "Pulchowk, Lalitpur",   4.4,  88),
        make_row("Himalayan Real Estate",     "real_estate","+977 9801-555555", "https://himalayanre.example",   80, 55, 88000, 1.1, "lead-form CRM",    "contacted",      "Baluwatar, Kathmandu", 4.0,  67),
        make_row("Iron Fitness Gym Thamel",   "gym",        "+977 9801-666666", "https://irongym.example",       60, 39, 19000, 2.7, "trial-signup form","approved",       "Thamel, Kathmandu",    4.2, 210),
        make_row("Sharma Law Associates",     "law_firm",   "+977 9801-777777", None,                            82, None, 55000, 1.6, "site + intake",   "replied",        "New Baneshwor, Kathmandu",4.5, 41),
        make_row("Bluebird Clinic Bhaktapur", "clinic",     "+977 9802-888888", "https://bluebird.example",      72, 60, 34000, 2.0, "booking",          "demo_scheduled", "Suryabinayak, Bhaktapur",4.3, 178),
        make_row("Zen Yoga Studio Boudha",    "other",      "+977 9801-999999", None,                            55, None,  9000, 4.5, "website",         "lost",           "Boudha, Kathmandu",    4.6,  22),
        make_row("Shrestha CA Office",        "other",      "+977 9801-101010", "https://shresthaca.example",    50, 45, 12000, 3.8, "redesign + chat", "won",            "Putalisadak, Kathmandu",4.7, 95),
    ]
    rows.sort(key=lambda r: r["_rev"], reverse=True)

    stage_counts = {s: 0 for s in PIPELINE_STAGES}
    for r in rows:
        stage_counts[r["stage"]] = stage_counts.get(r["stage"], 0) + 1

    summary = {
        "total_prospects": len(rows),
        "total_monthly_rev_inr": sum(r["_rev"] for r in rows),
        "stage_counts": stage_counts,
        "won_count": stage_counts.get("won", 0),
        "lost_count": stage_counts.get("lost", 0),
    }
    industries = sorted({r["industry"] for r in rows})

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["npr"] = _npr
    # Back-compat: dashboard.html still uses `| inr` until templates are updated.
    env.filters["inr"] = _npr
    env.filters["wa_link"] = _wa_link
    env.globals["STAGE_COLORS"] = STAGE_COLORS
    env.globals["PIPELINE_STAGES"] = PIPELINE_STAGES
    env.globals["INTENT_COLORS"] = INTENT_COLORS

    # The template uses {{ request.url_for(...) }} in some macros — provide a stub
    # to keep rendering happy. Our dashboard.html doesn't actually call it.
    class _ReqStub:
        def url_for(self, *_a, **_k):
            return "#"

    html = env.get_template("dashboard.html").render(
        request=_ReqStub(),
        rows=rows,
        summary=summary,
        industries=industries,
        current_stage=None,
        current_industry=None,
        flash="Preview mode — mock data (Supabase calls bypassed).",
    )

    out = ROOT / ".dashboard_preview.html"
    out.write_text(html, encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
