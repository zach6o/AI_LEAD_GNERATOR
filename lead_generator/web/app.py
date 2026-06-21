from __future__ import annotations

import sys
from pathlib import Path

import re
from urllib.parse import quote

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..db import client
from ..operator import approvals as approvals_mod

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Filter for NPR (Nepali rupee) currency formatting.
def _npr(value):
    if value is None or value == "":
        return "—"
    try:
        return f"रू{int(value):,}"
    except (TypeError, ValueError):
        return str(value)
templates.env.filters["npr"] = _npr
# Back-compat alias: existing templates may still reference `| inr`.
templates.env.filters["inr"] = _npr


def _wa_link(phone: str | None, body: str | None) -> str:
    """Build a wa.me click-to-chat URL with the message pre-filled.
    Returns '' if there's no phone, so templates can hide the button."""
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 10:
        return ""
    text = quote(body or "", safe="")
    return f"https://wa.me/{digits}?text={text}"
templates.env.filters["wa_link"] = _wa_link

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
templates.env.globals["STAGE_COLORS"] = STAGE_COLORS
templates.env.globals["PIPELINE_STAGES"] = PIPELINE_STAGES
templates.env.globals["INTENT_COLORS"] = INTENT_COLORS


app = FastAPI(title="Lead Generator Dashboard")


@app.get("/health")
def health_check():
    """Health check endpoint for Fly.io and other orchestration platforms."""
    return {"status": "ok", "service": "lead-generator"}


def _load_prospects(stage: str | None, industry: str | None) -> list[dict]:
    q = (
        client()
        .table("prospects")
        .select(
            "id, business_name, industry, location, website, phone, email, "
            "lead_score, stage, recommended_service, last_contacted, notes, "
            "google_rating, google_reviews, "
            "website_analyses(id, final_score, issues, has_chat, has_booking, "
            "has_lead_form, has_analytics, has_viewport, response_ms, http_status, error, tech_detected), "
            "opportunities(id, primary_service, secondary_services, "
            "monthly_revenue_impact_inr, our_one_time_inr, our_monthly_inr, "
            "roi_months, confidence, pitch_summary, hours_saved_per_week), "
            "outreach_messages(id, channel, kind, subject, body, status, generated_by, sent_at, send_error, attempts), "
            "prospect_replies(id, channel, from_address, subject, body, intent, received_at)"
        )
    )
    if stage:
        q = q.eq("stage", stage)
    if industry:
        q = q.eq("industry", industry)
    rows = q.execute().data

    out: list[dict] = []
    for r in rows:
        wa = (r.get("website_analyses") or [None])[0]
        opps = r.get("opportunities") or []
        best = max(opps, key=lambda o: (o.get("monthly_revenue_impact_inr") or 0), default=None)
        rev = (best or {}).get("monthly_revenue_impact_inr") or 0
        msgs = r.get("outreach_messages") or []
        # Group messages: { channel: { kind: msg } }
        msg_map: dict[str, dict[str, dict]] = {}
        for m in msgs:
            msg_map.setdefault(m["channel"], {})[m["kind"]] = m
        replies = sorted(
            r.get("prospect_replies") or [],
            key=lambda x: x.get("received_at") or "",
            reverse=True,
        )
        out.append({
            **r,
            "analysis": wa,
            "opportunity": best,
            "_rev": rev,
            "messages": msg_map,
            "replies": replies,
        })
    out.sort(key=lambda x: x["_rev"], reverse=True)
    return out


def _summary(rows: list[dict]) -> dict:
    total_rev = sum((r["_rev"] or 0) for r in rows)
    stage_counts: dict[str, int] = {s: 0 for s in PIPELINE_STAGES}
    for r in rows:
        stage_counts[r.get("stage") or "new"] = stage_counts.get(r.get("stage") or "new", 0) + 1
    return {
        "total_prospects": len(rows),
        "total_monthly_rev_inr": total_rev,
        "stage_counts": stage_counts,
        "won_count": stage_counts.get("won", 0),
        "lost_count": stage_counts.get("lost", 0),
    }


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, stage: str | None = None, industry: str | None = None, msg: str | None = None):
    rows = _load_prospects(stage, industry)
    summary = _summary(rows)
    industries = sorted({r.get("industry") or "other" for r in rows})
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "rows": rows,
            "summary": summary,
            "industries": industries,
            "current_stage": stage,
            "current_industry": industry,
            "flash": msg,
        },
    )


# ---------- Action endpoints (long-running, run in background) ----------

def _bg_hunt(niche: str, location: str, limit: int, region: str | None) -> None:
    from ..agents.lead_hunter import hunt_google_places
    try:
        hunt_google_places(niche=niche, location=location, limit=limit, region_code=region or None)
    except Exception:
        # Errors are persisted on hunt_runs.error; swallow here so the bg task ends cleanly.
        pass


def _bg_analyze(limit: int) -> None:
    from ..agents.website_analyzer import analyze_prospect, fetch_pending_prospects, save_analysis
    for p in fetch_pending_prospects(limit):
        if p.get("website"):
            a = analyze_prospect(p["id"], p["website"])
            save_analysis(a)


def _bg_opportunities(limit: int) -> None:
    from ..agents.opportunity_finder import (
        fetch_pending_prospects as fp, find_opportunity, save_opportunity,
    )
    for p in fp(limit):
        analyses = p.get("website_analyses") or []
        analysis = analyses[0] if analyses else None
        save_opportunity(find_opportunity(p, analysis))


def _bg_write(limit: int) -> None:
    from ..agents.outreach_writer import (
        fetch_pending_prospects as fp, write_for_prospect,
    )
    for p in fp(limit):
        write_for_prospect(p)


def _bg_enrich(limit: int) -> None:
    from ..agents.email_enricher import enrich_all
    enrich_all(limit)


def _bg_send_emails(limit: int) -> None:
    from ..agents.outreach_sender import run_batch
    run_batch(channel="email", limit=limit, rate_seconds=2.0, dry_run=False)


def _bg_monitor(limit: int) -> None:
    from ..agents.reply_monitor import run_once
    try:
        run_once(max_messages=limit)
    except Exception:
        # Surfaced via notifier already; don't crash the bg task.
        pass


def _bg_demos(limit: int) -> None:
    from ..agents.demo_booker import run_once
    run_once(limit)


def _bg_queue(limit: int) -> None:
    from ..agents.outreach_sender import request_approval_for_drafts
    request_approval_for_drafts(limit)


@app.post("/actions/hunt")
def action_hunt(
    background: BackgroundTasks,
    niche: str = Form(...),
    location: str = Form(...),
    limit: int = Form(20),
    region: str = Form(""),
):
    background.add_task(_bg_hunt, niche, location, limit, region)
    return RedirectResponse(
        url=f"/?msg=Hunt+started+for+{niche}+in+{location}.+Refresh+in+~30s.", status_code=303
    )


@app.post("/actions/analyze")
def action_analyze(background: BackgroundTasks, limit: int = Form(20)):
    background.add_task(_bg_analyze, limit)
    return RedirectResponse(url=f"/?msg=Analyzing+up+to+{limit}+websites+in+background.", status_code=303)


@app.post("/actions/opportunities")
def action_opportunities(background: BackgroundTasks, limit: int = Form(50)):
    background.add_task(_bg_opportunities, limit)
    return RedirectResponse(url=f"/?msg=Scoring+opportunities+for+up+to+{limit}+prospects.", status_code=303)


@app.post("/actions/write")
def action_write(background: BackgroundTasks, limit: int = Form(50)):
    background.add_task(_bg_write, limit)
    return RedirectResponse(url=f"/?msg=Drafting+messages+for+up+to+{limit}+prospects.", status_code=303)


@app.post("/actions/enrich")
def action_enrich(background: BackgroundTasks, limit: int = Form(50)):
    background.add_task(_bg_enrich, limit)
    return RedirectResponse(url=f"/?msg=Scraping+websites+for+emails+(up+to+{limit}).", status_code=303)


@app.post("/actions/send-emails")
def action_send_emails(background: BackgroundTasks, limit: int = Form(25)):
    background.add_task(_bg_send_emails, limit)
    return RedirectResponse(url=f"/?msg=Sending+up+to+{limit}+approved+emails+via+Gmail.", status_code=303)


@app.post("/actions/monitor")
def action_monitor(background: BackgroundTasks, limit: int = Form(50)):
    background.add_task(_bg_monitor, limit)
    return RedirectResponse(url=f"/?msg=Polling+Gmail+for+up+to+{limit}+new+replies.", status_code=303)


@app.post("/actions/demos")
def action_demos(background: BackgroundTasks, limit: int = Form(25)):
    background.add_task(_bg_demos, limit)
    return RedirectResponse(url=f"/?msg=Drafting+demo+invites+for+interested+replies.", status_code=303)


@app.post("/actions/queue")
def action_queue(background: BackgroundTasks, limit: int = Form(50)):
    background.add_task(_bg_queue, limit)
    return RedirectResponse(url=f"/?msg=Queuing+up+to+{limit}+drafts+for+operator+approval.", status_code=303)


@app.get("/approvals", response_class=HTMLResponse)
def approvals_page(request: Request, msg: str | None = None):
    pending = approvals_mod.list_pending(limit=100)
    # Decorate with prospect business names for context.
    prospect_ids = sorted({a.prospect_id for a in pending if a.prospect_id})
    name_by_id: dict[str, str] = {}
    if prospect_ids:
        rows = (
            client()
            .table("prospects")
            .select("id, business_name")
            .in_("id", prospect_ids)
            .execute()
            .data
        )
        name_by_id = {r["id"]: r["business_name"] for r in rows}
    items = [
        {
            "id": a.id,
            "short_id": a.id.split("-")[0],
            "kind": a.kind,
            "summary": a.summary,
            "created_at": a.created_at,
            "prospect_id": a.prospect_id,
            "business_name": name_by_id.get(a.prospect_id or ""),
            "message_id": a.message_id,
        }
        for a in pending
    ]
    return templates.TemplateResponse(
        request, "approvals.html", {"items": items, "flash": msg}
    )


@app.post("/approvals/{approval_id}/decide")
def approval_decide(approval_id: str, decision: str = Form(...)):
    if decision not in {"approved", "rejected"}:
        raise HTTPException(400, "Invalid decision")
    a = approvals_mod.resolve(approval_id, decision, by="dashboard")
    if not a:
        raise HTTPException(404, "Approval not found")
    # If it's a per-message send approval, sync the message row too.
    if a.message_id and a.kind == "send_message":
        new_status = "approved" if decision == "approved" else "rejected"
        update: dict = {"status": new_status}
        if decision == "approved":
            from datetime import datetime, timezone
            update["approved_at"] = datetime.now(timezone.utc).isoformat()
        client().table("outreach_messages").update(update).eq("id", a.message_id).execute()
    verb = "Approved" if decision == "approved" else "Rejected"
    return RedirectResponse(
        url=f"/approvals?msg={verb}+{a.kind}+{approval_id.split('-')[0]}", status_code=303
    )


@app.get("/prospects/{prospect_id}", response_class=HTMLResponse)
def prospect_detail(request: Request, prospect_id: str):
    rows = _load_prospects(stage=None, industry=None)
    row = next((r for r in rows if r["id"] == prospect_id), None)
    if not row:
        raise HTTPException(404, "Prospect not found")
    return templates.TemplateResponse(request, "prospect.html", {"row": row})


@app.post("/prospects/{prospect_id}/stage")
def update_stage(prospect_id: str, stage: str = Form(...)):
    if stage not in PIPELINE_STAGES:
        raise HTTPException(400, "Invalid stage")
    update = {"stage": stage}
    if stage == "contacted":
        from datetime import datetime, timezone
        update["last_contacted"] = datetime.now(timezone.utc).isoformat()
    client().table("prospects").update(update).eq("id", prospect_id).execute()
    return RedirectResponse(url=f"/prospects/{prospect_id}", status_code=303)


@app.post("/prospects/{prospect_id}/notes")
def update_notes(prospect_id: str, notes: str = Form("")):
    client().table("prospects").update({"notes": notes}).eq("id", prospect_id).execute()
    return RedirectResponse(url=f"/prospects/{prospect_id}", status_code=303)


@app.post("/prospects/{prospect_id}/contact")
def update_contact(prospect_id: str, email: str = Form(""), phone: str = Form("")):
    update: dict = {}
    if email != "":
        update["email"] = email.strip() or None
    if phone != "":
        update["phone"] = phone.strip() or None
    if update:
        client().table("prospects").update(update).eq("id", prospect_id).execute()
    return RedirectResponse(url=f"/prospects/{prospect_id}", status_code=303)


@app.post("/messages/{message_id}/status")
def update_message_status(message_id: str, status: str = Form(...), prospect_id: str = Form(...)):
    if status not in {"draft", "approved", "rejected"}:
        raise HTTPException(400, "Invalid status")
    update: dict = {"status": status}
    if status == "approved":
        from datetime import datetime, timezone
        update["approved_at"] = datetime.now(timezone.utc).isoformat()
    client().table("outreach_messages").update(update).eq("id", message_id).execute()
    return RedirectResponse(url=f"/prospects/{prospect_id}", status_code=303)


@app.post("/messages/{message_id}/edit")
def edit_message(message_id: str, prospect_id: str = Form(...), subject: str = Form(""), body: str = Form(...)):
    update: dict = {"body": body, "generated_by": "human_edited"}
    if subject:
        update["subject"] = subject
    client().table("outreach_messages").update(update).eq("id", message_id).execute()
    return RedirectResponse(url=f"/prospects/{prospect_id}", status_code=303)


@app.post("/messages/{message_id}/send")
def send_one(message_id: str, prospect_id: str = Form(...)):
    """Send a single approved message right now (Gmail SMTP / WhatsApp Cloud)."""
    from ..agents.outreach_sender import send_message
    send_message(message_id, dry_run=False)
    return RedirectResponse(url=f"/prospects/{prospect_id}", status_code=303)


@app.post("/messages/{message_id}/mark-sent")
def mark_sent(message_id: str, prospect_id: str = Form(...)):
    """Manually mark a message as sent (used after click-to-chat WhatsApp).

    No actual delivery is performed — this records that the user opened the
    wa.me link and (presumably) clicked send themselves.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    client().table("outreach_messages").update(
        {
            "status": "sent",
            "sent_at": now,
            "sent_message_id": "wa-me:manual",
            "send_error": None,
        }
    ).eq("id", message_id).execute()

    # Bump prospect to 'contacted' on first send, same as the automated path.
    msg = (
        client()
        .table("outreach_messages")
        .select("prospect_id, prospects(stage)")
        .eq("id", message_id)
        .limit(1)
        .execute()
        .data
    )
    if msg:
        stage = (msg[0].get("prospects") or {}).get("stage")
        if stage in (None, "new", "analyzed", "approved"):
            client().table("prospects").update(
                {"stage": "contacted", "last_contacted": now}
            ).eq("id", prospect_id).execute()

    return RedirectResponse(url=f"/prospects/{prospect_id}", status_code=303)


@app.post("/prospects/{prospect_id}/mark-replied")
def mark_replied(prospect_id: str):
    """One-click 'I got a reply' — bumps the prospect into the 'replied' stage."""
    client().table("prospects").update({"stage": "replied"}).eq("id", prospect_id).execute()
    return RedirectResponse(url=f"/prospects/{prospect_id}?msg=Marked+as+replied", status_code=303)


@app.post("/prospects/{prospect_id}/draft")
def draft_messages(prospect_id: str):
    """Generate messages on demand from the detail page."""
    from ..agents.outreach_writer import write_for_prospect
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
        .eq("id", prospect_id)
        .limit(1)
        .execute()
        .data
    )
    if not rows:
        raise HTTPException(404, "Prospect not found")
    write_for_prospect(rows[0])
    return RedirectResponse(url=f"/prospects/{prospect_id}", status_code=303)
