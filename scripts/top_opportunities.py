"""Print the best prospects to actually pitch first.

Uses Agent 3 output (`opportunities`) when available — that's the most
useful ranking because it includes INR revenue impact and confidence.
Falls back to the Agent 1 + Agent 2 composite when an opportunity row
hasn't been scored yet.
"""
from __future__ import annotations

import sys

from rich.console import Console
from rich.table import Table

from lead_generator.db import client

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

console = Console()


def main(limit: int = 15) -> None:
    rows = (
        client()
        .table("prospects")
        .select(
            "id, business_name, lead_score, phone, "
            "opportunities(primary_service, monthly_revenue_impact_inr, "
            "roi_months, confidence, pitch_summary), "
            "website_analyses(final_score)"
        )
        .execute()
        .data
    )

    ranked = []
    for r in rows:
        opps = r.get("opportunities") or []
        wa = (r.get("website_analyses") or [{}])[0]
        # Pick the single best opportunity per prospect.
        opp = max(opps, key=lambda o: o.get("monthly_revenue_impact_inr") or 0, default=None)
        rev = (opp or {}).get("monthly_revenue_impact_inr") or 0
        ranked.append(
            {
                "lead":   r["lead_score"],
                "site":   wa.get("final_score") if wa else None,
                "rev":    rev,
                "roi":    (opp or {}).get("roi_months"),
                "conf":   (opp or {}).get("confidence") or 0,
                "pitch":  (opp or {}).get("primary_service") or "-",
                "name":   r["business_name"],
                "phone":  r.get("phone") or "—",
                "summary": (opp or {}).get("pitch_summary") or "",
            }
        )

    ranked.sort(key=lambda x: x["rev"], reverse=True)
    ranked = ranked[:limit]

    t = Table(title="Top opportunities (sorted by est. monthly revenue impact, INR)")
    t.add_column("Rev /mo", justify="right", style="bold green")
    t.add_column("ROI mo", justify="right")
    t.add_column("Conf", justify="right")
    t.add_column("Pitch")
    t.add_column("Business")
    t.add_column("Phone")

    for r in ranked:
        rev = f"₹{r['rev']:,}" if r["rev"] else "-"
        roi = f"{r['roi']}" if r["roi"] is not None else "-"
        t.add_row(rev, roi, str(r["conf"]), r["pitch"], r["name"][:36], r["phone"])

    console.print(t)
    console.print()
    console.print("[bold]Top 3 pitches in full:[/bold]")
    for r in ranked[:3]:
        if not r["summary"]:
            continue
        console.print(f"\n[cyan]• {r['name']}[/cyan]  ({r['phone']})")
        console.print(f"  {r['summary']}")


if __name__ == "__main__":
    main()
