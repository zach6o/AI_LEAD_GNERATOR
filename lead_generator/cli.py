from __future__ import annotations

import sys

import typer
from rich.console import Console
from rich.table import Table

# Windows console defaults to cp1252 which can't render रू and other UTF-8.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from .agents.lead_hunter import hunt_google_places
from .agents.website_analyzer import (
    analyze_prospect,
    fetch_pending_prospects,
    save_analysis,
)
from .agents.opportunity_finder import (
    fetch_pending_prospects as fetch_pending_for_opps,
    find_opportunity,
    save_opportunity,
)
from .agents.outreach_writer import (
    fetch_pending_prospects as fetch_pending_for_writes,
    write_for_prospect,
)
from .agents.outreach_sender import run_batch, request_approval_for_drafts
from .agents.email_enricher import enrich_all
from .agents.reply_monitor import run_once as run_reply_monitor
from .agents.demo_booker import run_once as run_demo_booker
from .operator import approvals

app = typer.Typer(add_completion=False, help="AI Lead Generator")
console = Console()


@app.callback()
def _root() -> None:
    """AI Lead Generator CLI. Use a subcommand, e.g. `hunt`."""


@app.command()
def hunt(
    niche: str = typer.Option(..., "--niche", "-n", help="PRD niche, e.g. 'restaurants'"),
    location: str = typer.Option(..., "--location", "-l", help="e.g. 'Kathmandu, Nepal'"),
    limit: int = typer.Option(20, "--limit", help="Max prospects to fetch (<=60)"),
    region_code: str = typer.Option(
        None, "--region", help="ISO country code, e.g. 'IN', 'US'. Optional."
    ),
) -> None:
    """Agent 1: hunt prospects from Google Places and store them in Supabase."""
    if limit > 60:
        console.print("[yellow]Google caps text search at ~60 results. Clamping.[/yellow]")
        limit = 60

    console.print(f"[bold]Hunting[/bold] niche=[cyan]{niche}[/cyan] location=[cyan]{location}[/cyan] limit={limit}")
    res = hunt_google_places(
        niche=niche, location=location, limit=limit, region_code=region_code
    )

    console.print(
        f"\n[green]Done.[/green] run_id={res.run_id}  found={res.found}  "
        f"new={res.inserted}  updated={res.updated}"
    )

    if res.samples:
        t = Table(title="Top samples")
        t.add_column("Score", justify="right", style="bold")
        t.add_column("Business")
        t.add_column("Rating", justify="right")
        t.add_column("Website")
        for s in sorted(res.samples, key=lambda x: x["score"], reverse=True):
            t.add_row(
                str(s["score"]),
                s["name"],
                str(s["rating"]) if s["rating"] is not None else "-",
                s["website"] or "[red]missing[/red]",
            )
        console.print(t)


@app.command()
def analyze(
    limit: int = typer.Option(20, "--limit", help="Max prospects to analyze in this run"),
    prospect_id: str = typer.Option(
        None, "--prospect-id", help="Analyze a specific prospect by id"
    ),
) -> None:
    """Agent 2: analyze prospect websites for issues + opportunities."""
    if prospect_id:
        from .db import client as _c
        row = (
            _c()
            .table("prospects")
            .select("id, business_name, website")
            .eq("id", prospect_id)
            .limit(1)
            .execute()
            .data
        )
        if not row:
            console.print(f"[red]No prospect found with id {prospect_id}[/red]")
            raise typer.Exit(code=1)
        targets = row
    else:
        targets = fetch_pending_prospects(limit)

    if not targets:
        console.print("[yellow]No pending prospects to analyze.[/yellow]")
        return

    console.print(f"[bold]Analyzing[/bold] {len(targets)} site(s)...")
    t = Table(title="Website Analyses")
    t.add_column("Score", justify="right", style="bold")
    t.add_column("Business")
    t.add_column("Pitch")
    t.add_column("Top issue")

    for p in targets:
        website = p.get("website") or ""
        if not website:
            continue
        a = analyze_prospect(p["id"], website)
        save_analysis(a)
        top = a.issues[0] if a.issues else "-"
        t.add_row(
            str(a.final_score),
            p["business_name"][:38],
            a.recommended_service or "-",
            top[:60],
        )

    console.print(t)


@app.command()
def write(
    limit: int = typer.Option(50, "--limit", help="Max prospects to draft messages for"),
) -> None:
    """Agent 5: draft personalized email/WhatsApp/LinkedIn messages + follow-ups."""
    targets = fetch_pending_for_writes(limit)
    if not targets:
        console.print("[yellow]No pending prospects need messages drafted.[/yellow]")
        return

    console.print(f"[bold]Drafting messages for[/bold] {len(targets)} prospect(s)...")
    total_new = 0
    total_upd = 0
    t = Table(title="Outreach drafts")
    t.add_column("New", justify="right")
    t.add_column("Upd", justify="right")
    t.add_column("Business")

    for p in targets:
        ins, upd = write_for_prospect(p)
        total_new += ins
        total_upd += upd
        t.add_row(str(ins), str(upd), p["business_name"][:50])

    console.print(t)
    console.print(f"[green]Done.[/green] new={total_new}  updated={total_upd}  (9 messages/prospect)")


@app.command()
def enrich(
    limit: int = typer.Option(50, "--limit", help="Max prospects to enrich"),
    overwrite: bool = typer.Option(False, "--overwrite", help="Re-enrich even prospects that already have an email"),
) -> None:
    """Find emails by scraping each prospect's own website."""
    results = enrich_all(limit, overwrite=overwrite)
    if not results:
        console.print("[yellow]No pending prospects to enrich.[/yellow]")
        return

    found = sum(1 for r in results if r.email)
    t = Table(title=f"Email enrichment ({found}/{len(results)} found)")
    t.add_column("Email")
    t.add_column("Pages")
    t.add_column("Prospect")
    for r in results:
        email = r.email or ("[dim]—[/dim]" + (f"  {r.error[:40]}" if r.error else ""))
        t.add_row(email, str(r.pages_tried), r.prospect_id[:8])
    console.print(t)
    console.print(f"[green]Done.[/green] found_emails={found}  attempted={len(results)}")


@app.command()
def send(
    limit: int = typer.Option(25, "--limit", help="Max approved messages to send"),
    rate: float = typer.Option(2.0, "--rate", help="Seconds to wait between sends"),
    dry_run: bool = typer.Option(False, "--dry-run", help="List what would be sent; touch nothing"),
) -> None:
    """Agent 7: send approved outreach emails via Gmail SMTP.

    WhatsApp + LinkedIn drafts are copy-paste only — open the dashboard, copy
    the body, send manually, then tap 'Mark sent'.
    """
    stats, log = run_batch(channel="email", limit=limit, rate_seconds=rate, dry_run=dry_run)

    t = Table(title="Send results" + (" (DRY-RUN)" if dry_run else ""))
    t.add_column("Channel")
    t.add_column("Kind")
    t.add_column("Recipient")
    t.add_column("Business")
    t.add_column("Status")
    for e in log:
        status = e.get("status", "?")
        style = "green" if status == "sent" else ("yellow" if status == "DRY-RUN" else "red")
        t.add_row(
            e.get("channel", "-"),
            e.get("kind", "-"),
            (e.get("recipient") or "-")[:30],
            (e.get("business") or "-")[:35],
            f"[{style}]{status}[/{style}]" + (f" {e.get('error','')[:60]}" if e.get("error") else ""),
        )
    console.print(t)
    console.print(
        f"[bold]attempted={stats.attempted}  sent={stats.sent}  failed={stats.failed}[/bold]"
    )


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes"),
) -> None:
    """Run the local web dashboard (Agent 4: CRM Manager)."""
    import uvicorn
    console.print(f"[bold]Lead Generator dashboard[/bold] → http://{host}:{port}")
    uvicorn.run("lead_generator.web.app:app", host=host, port=port, reload=reload)


@app.command()
def opportunities(
    limit: int = typer.Option(50, "--limit", help="Max prospects to score"),
) -> None:
    """Agent 3: turn analyses into concrete pitches with NPR revenue + ROI."""
    targets = fetch_pending_for_opps(limit)
    if not targets:
        console.print("[yellow]No pending prospects for opportunity scoring.[/yellow]")
        return

    console.print(f"[bold]Scoring opportunities for[/bold] {len(targets)} prospect(s)...")
    t = Table(title="Opportunities")
    t.add_column("Conf", justify="right", style="bold")
    t.add_column("Rev /mo", justify="right", style="green")
    t.add_column("ROI mo", justify="right")
    t.add_column("Service")
    t.add_column("Business")

    for p in targets:
        analyses = p.get("website_analyses") or []
        analysis = analyses[0] if analyses else None
        opp = find_opportunity(p, analysis)
        save_opportunity(opp)
        rev = f"रू{opp.monthly_revenue_impact_inr:,}" if opp.monthly_revenue_impact_inr else "-"
        roi = f"{opp.roi_months}" if opp.roi_months is not None else "-"
        t.add_row(
            str(opp.confidence),
            rev,
            roi,
            opp.primary_service,
            p["business_name"][:36],
        )

    console.print(t)


@app.command()
def monitor(
    limit: int = typer.Option(50, "--limit", help="Max IMAP messages to fetch this poll"),
) -> None:
    """Agent 8: poll Gmail IMAP for prospect replies and classify. Replies surface on the dashboard."""
    try:
        res = run_reply_monitor(max_messages=limit)
    except Exception as e:
        console.print(f"[red]Monitor failed:[/red] {type(e).__name__}: {e}")
        raise typer.Exit(code=1)

    console.print(
        f"[bold]Replies:[/bold] fetched={res.fetched}  matched={res.matched}  "
        f"unmatched={res.unmatched}  dup={res.duplicates}"
    )
    if res.by_intent:
        t = Table(title="Intents")
        t.add_column("Intent")
        t.add_column("Count", justify="right")
        for k, v in sorted(res.by_intent.items(), key=lambda kv: -kv[1]):
            t.add_row(k, str(v))
        console.print(t)


@app.command()
def demos(
    limit: int = typer.Option(25, "--limit", help="Max interested replies to draft for"),
) -> None:
    """Agent 9: turn interested replies into demo-invite drafts (approval-gated)."""
    drafts = run_demo_booker(limit)
    if not drafts:
        console.print("[yellow]No interested replies waiting.[/yellow]")
        return

    t = Table(title="Demo Booker drafts")
    t.add_column("Status")
    t.add_column("Prospect", overflow="fold")
    t.add_column("Note", overflow="fold")
    for d in drafts:
        if d.skipped_reason:
            t.add_row("[dim]skipped[/dim]", d.prospect_id[:8], d.skipped_reason)
        elif d.approval_id:
            t.add_row("[yellow]pending[/yellow]", d.prospect_id[:8], f"approval={d.approval_id.split('-')[0]}")
        else:
            t.add_row("[green]queued[/green]", d.prospect_id[:8], "auto-approved (gate off)")
    console.print(t)


@app.command()
def queue(
    limit: int = typer.Option(50, "--limit", help="Max drafts to promote"),
) -> None:
    """Promote draft outreach messages to 'pending_approval' and notify operator."""
    promoted, skipped = request_approval_for_drafts(limit)
    console.print(
        f"[green]Queued {promoted} drafts.[/green] Skipped {skipped} "
        f"(no recipient / LinkedIn / already approved)."
    )


@app.command()
def pending(
    limit: int = typer.Option(20, "--limit"),
) -> None:
    """List currently pending operator approvals."""
    items = approvals.list_pending(limit=limit)
    if not items:
        console.print("[green]Nothing pending.[/green]")
        return
    t = Table(title=f"{len(items)} pending approval(s)")
    t.add_column("Short id")
    t.add_column("Kind")
    t.add_column("Summary", overflow="fold")
    for a in items:
        first_line = a.summary.splitlines()[0] if a.summary else "(no summary)"
        t.add_row(a.id.split("-")[0], a.kind, first_line[:80])
    console.print(t)


if __name__ == "__main__":
    app()
