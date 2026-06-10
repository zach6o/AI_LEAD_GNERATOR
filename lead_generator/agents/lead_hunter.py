from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console

from ..db import finish_hunt_run, start_hunt_run, upsert_prospect
from ..scoring import score_prospect
from ..sources.google_places import map_place_to_prospect, resolve_niche, search_places

console = Console()


@dataclass
class HuntResult:
    run_id: str
    found: int
    inserted: int
    updated: int
    samples: list[dict]


def hunt_google_places(
    *,
    niche: str,
    location: str,
    limit: int = 20,
    region_code: str | None = None,
) -> HuntResult:
    """Run Agent 1 (Lead Hunter) against Google Places for one niche+location."""
    keyword, industry = resolve_niche(niche)
    text_query = f"{keyword} in {location}"

    run_id = start_hunt_run(
        source="google_places",
        niche=niche,
        location=location,
        params={"text_query": text_query, "limit": limit, "region_code": region_code},
    )

    found = 0
    inserted = 0
    updated = 0
    samples: list[dict] = []
    error: str | None = None

    try:
        for place in search_places(
            text_query=text_query, max_results=limit, region_code=region_code
        ):
            found += 1
            prospect = map_place_to_prospect(place, industry=industry, location=location)
            prospect["lead_score"] = score_prospect(prospect)

            _id, was_new = upsert_prospect(prospect)
            if was_new:
                inserted += 1
            else:
                updated += 1

            if len(samples) < 5:
                samples.append(
                    {
                        "name": prospect["business_name"],
                        "score": prospect["lead_score"],
                        "website": prospect.get("website"),
                        "rating": prospect.get("google_rating"),
                    }
                )
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        raise
    finally:
        finish_hunt_run(
            run_id,
            found_count=found,
            inserted_count=inserted,
            updated_count=updated,
            error=error,
        )

    return HuntResult(
        run_id=run_id,
        found=found,
        inserted=inserted,
        updated=updated,
        samples=samples,
    )
