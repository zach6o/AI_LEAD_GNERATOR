"""Run the real FastAPI dashboard with a Supabase stub so every route works.

Why: the live `serve` command can't reach Supabase from this sandbox (DNS
blocked) and the static `.dashboard_preview.html` has no server behind it,
so clicking a row 404s. This script patches `lead_generator.db.client` and
`lead_generator.web.app._load_prospects` to return mock data, then runs
uvicorn — so dashboard rows, /prospects/<id>, and /approvals all render.

Usage:
    .venv\\Scripts\\python.exe scripts\\run_preview_server.py
Then open http://127.0.0.1:8000
"""
from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ---------- mock data ----------

def _mock_prospects() -> list[dict]:
    base = [
        ("Sagarmatha Dental Clinic",  "dentist",    "+977 9801-111111", None,                          85, 62, 45000, 1.8, "AI Chatbot",             "new",            "Thamel, Kathmandu",    3.9, 142),
        ("Bhojan Griha Restaurant",   "restaurant", "+977 9801-222222", "https://bhojangriha.example", 70, 48, 28000, 2.4, "Website Redesign",       "new",            "Dilli Bazar, Kathmandu",3.6, 312),
        ("Lotus Lake Hotel Pokhara",  "hotel",      "+977 9802-333333", "https://lotuslake.example",   78, 71, 62000, 1.4, "AI Appointment Booking", "analyzed",       "Lakeside, Pokhara",    4.1, 540),
        ("Pearl Beauty Salon",        "salon",      "+977 9801-444444", None,                          65, None,15000, 3.0, "Website Development",    "new",            "Pulchowk, Lalitpur",   4.4,  88),
        ("Himalayan Real Estate",     "real_estate","+977 9801-555555", "https://himalayanre.example", 80, 55, 88000, 1.1, "Lead Generation System", "contacted",      "Baluwatar, Kathmandu", 4.0,  67),
        ("Iron Fitness Gym Thamel",   "gym",        "+977 9801-666666", "https://irongym.example",     60, 39, 19000, 2.7, "AI Lead Qualification",  "approved",       "Thamel, Kathmandu",    4.2, 210),
        ("Sharma Law Associates",     "law_firm",   "+977 9801-777777", None,                          82, None,55000, 1.6, "Website Development",    "replied",        "New Baneshwor, Kathmandu",4.5, 41),
        ("Bluebird Clinic Bhaktapur", "clinic",     "+977 9802-888888", "https://bluebird.example",    72, 60, 34000, 2.0, "AI Appointment Booking", "demo_scheduled", "Suryabinayak, Bhaktapur",4.3, 178),
        ("Zen Yoga Studio Boudha",    "other",      "+977 9801-999999", None,                          55, None, 9000, 4.5, "Website Development",    "lost",           "Boudha, Kathmandu",    4.6,  22),
        ("Shrestha CA Office",        "other",      "+977 9801-101010", "https://shresthaca.example",  50, 45, 12000, 3.8, "Website Redesign",       "won",            "Putalisadak, Kathmandu",4.7,  95),
    ]

    rows: list[dict] = []
    for i, (name, ind, phone, web, lead, site_score, rev, roi, svc, stage, loc, rating, reviews) in enumerate(base, 1):
        pid = f"p{i:02d}-mock-id"
        analysis = (
            {
                "id": f"a{i:02d}",
                "final_score": site_score,
                "issues": [
                    "No mobile viewport meta tag",
                    "No HTTPS — browsers will warn",
                    "Slow first paint (>3s)",
                ][: (3 if site_score and site_score < 60 else 1)],
                "has_chat": False,
                "has_booking": ind in {"hotel", "clinic"},
                "has_lead_form": False,
                "has_analytics": True,
                "has_viewport": bool(web),
                "response_ms": 1800 + i * 120,
                "http_status": 200,
                "error": None,
                "tech_detected": ["WordPress", "jQuery"],
            }
            if site_score is not None
            else None
        )
        opp = {
            "id": f"o{i:02d}",
            "primary_service": svc,
            "secondary_services": ["AI Chatbot", "WhatsApp Automation"][: 2 if i % 2 == 0 else 1],
            "monthly_revenue_impact_inr": rev,
            "our_one_time_inr": 25000 if "Website" in svc else 15000,
            "our_monthly_inr": 0 if "Website" in svc and "Redesign" not in svc else 2000,
            "roi_months": roi,
            "confidence": 60 + (i % 4) * 8,
            "pitch_summary": (
                f"{name} is missing a booking widget and shows up below page-1 "
                f"for '{ind} {loc.split(',')[0]}' searches. Adding {svc.lower()} "
                f"and a clean lead path should recover ~रू{rev:,}/month in "
                f"bookings that currently leak to competitors."
            ),
            "hours_saved_per_week": 6 + (i % 5),
        }
        msgs = [
            {
                "id": f"m{i:02d}-email-init",
                "channel": "email", "kind": "initial",
                "subject": f"Quick idea for {name}",
                "body": (
                    f"Hi there,\n\nI noticed {name} doesn't have an online booking "
                    f"flow on the site. Most of your competitors in {loc.split(',')[0]} "
                    f"are taking 8-12 bookings/day through theirs.\n\nWe could ship a "
                    f"booking + chatbot setup in ~2 weeks for रू{opp['our_one_time_inr']:,}. "
                    f"Worth a 15-min call this week?\n\n— Your Agency"
                ),
                "status": "draft" if i % 3 else "approved",
                "generated_by": "agent",
                "sent_at": None, "send_error": None, "attempts": 0,
            },
            {
                "id": f"m{i:02d}-wa-init",
                "channel": "whatsapp", "kind": "initial",
                "subject": None,
                "body": (
                    f"Namaste! I work with clinics across Kathmandu — noticed "
                    f"{name}'s site is missing online booking. We can add it for "
                    f"रू{opp['our_one_time_inr']:,}. 15-min call?"
                ),
                "status": "draft", "generated_by": "agent",
                "sent_at": None, "send_error": None, "attempts": 0,
            },
        ]
        msg_map: dict[str, dict[str, dict]] = {}
        for m in msgs:
            msg_map.setdefault(m["channel"], {})[m["kind"]] = m

        replies = []
        if stage in {"replied", "demo_scheduled"}:
            replies = [{
                "id": f"r{i:02d}",
                "channel": "email",
                "from_address": f"owner@{(web or 'example.com').replace('https://','')}",
                "subject": f"Re: Quick idea for {name}",
                "body": "Hi, this sounds interesting. Can you share examples and pricing? Also, do you handle the WhatsApp piece?",
                "intent": "interested",
                "received_at": "2026-06-18T11:22:33",
            }]

        rows.append({
            "id": pid,
            "business_name": name,
            "industry": ind,
            "location": loc,
            "website": web,
            "phone": phone,
            "email": (f"info@{web.replace('https://','')}" if web else None),
            "lead_score": lead,
            "stage": stage,
            "recommended_service": svc,
            "last_contacted": "2026-06-15T09:00:00Z" if stage == "contacted" else None,
            "notes": "",
            "google_rating": rating,
            "google_reviews": reviews,
            "analysis": analysis,
            "opportunity": opp,
            "_rev": rev or 0,
            "messages": msg_map,
            "replies": replies,
        })

    rows.sort(key=lambda r: r["_rev"], reverse=True)
    return rows


# Global mutable store so POST handlers can persist changes during the session.
STORE = {"prospects": _mock_prospects()}


# ---------- supabase stub ----------

class _NoopExec:
    def __init__(self, data=None):
        self.data = data or []

class _FakeQuery:
    def __init__(self, table: str, store: dict):
        self.table_name = table
        self.store = store
        self._filters: list[tuple[str, str, object]] = []
        self._select_cols: str = ""
        self._update: dict | None = None
        self._insert: dict | None = None

    def select(self, cols: str = "*"):
        self._select_cols = cols
        return self

    def update(self, payload: dict):
        self._update = payload
        return self

    def insert(self, payload: dict):
        self._insert = payload
        return self

    def eq(self, col, val):
        self._filters.append((col, "eq", val))
        return self

    def in_(self, col, vals):
        self._filters.append((col, "in", list(vals)))
        return self

    def limit(self, n):
        self._filters.append(("__limit__", "limit", n))
        return self

    def _matches(self, row: dict) -> bool:
        for col, op, val in self._filters:
            if col == "__limit__":
                continue
            if op == "eq" and row.get(col) != val:
                return False
            if op == "in" and row.get(col) not in val:
                return False
        return True

    def execute(self):
        rows = self.store.get(self.table_name, [])

        if self._update is not None:
            for r in rows:
                if self._matches(r):
                    r.update(self._update)
            return _NoopExec([{}])

        if self._insert is not None:
            new_id = f"new-{len(rows)+1}"
            row = {"id": new_id, **self._insert}
            rows.append(row)
            return _NoopExec([row])

        matched = [r for r in rows if self._matches(r)]
        limit = next((v for c, _, v in self._filters if c == "__limit__"), None)
        if limit:
            matched = matched[:limit]
        return _NoopExec(matched)

class _FakeClient:
    def __init__(self, store: dict):
        self.store = store
    def table(self, name: str):
        return _FakeQuery(name, self.store)


def patched_client():
    return _FakeClient(STORE)


# ---------- monkey-patch + run ----------

def main() -> None:
    import lead_generator.db as db_mod
    db_mod.client = patched_client  # type: ignore[assignment]

    # Replace the heavy nested-select function with a direct mock reader, so
    # filtering works regardless of stub completeness.
    import lead_generator.web.app as web_app

    def fake_load_prospects(stage, industry):
        rows = deepcopy(STORE["prospects"])
        if stage:
            rows = [r for r in rows if (r.get("stage") or "new") == stage]
        if industry:
            rows = [r for r in rows if r.get("industry") == industry]
        return rows

    web_app._load_prospects = fake_load_prospects  # type: ignore[assignment]

    # Stub approvals.list_pending so /approvals doesn't 500.
    from lead_generator.operator import approvals as approvals_mod
    approvals_mod.list_pending = lambda limit=100: []  # type: ignore[assignment]

    import uvicorn
    print("Preview server (mock data) → http://127.0.0.1:8000")
    uvicorn.run(web_app.app, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
