from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from supabase import Client, create_client

from .config import get_settings


@lru_cache(maxsize=1)
def client() -> Client:
    s = get_settings()
    return create_client(s.supabase_url, s.supabase_service_role_key)


def start_hunt_run(*, source: str, niche: str, location: str, params: dict[str, Any]) -> str:
    row = (
        client()
        .table("hunt_runs")
        .insert(
            {
                "source": source,
                "niche": niche,
                "location": location,
                "params": params,
            }
        )
        .execute()
    )
    return row.data[0]["id"]


def finish_hunt_run(
    run_id: str,
    *,
    found_count: int,
    inserted_count: int,
    updated_count: int,
    error: str | None = None,
) -> None:
    client().table("hunt_runs").update(
        {
            "found_count": found_count,
            "inserted_count": inserted_count,
            "updated_count": updated_count,
            "error": error,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
    ).eq("id", run_id).execute()


def upsert_prospect(prospect: dict[str, Any]) -> tuple[str, bool]:
    """Upsert a prospect by (source, source_ref).

    Returns (id, inserted) — inserted is True if it was a new row.
    """
    source = prospect["source"]
    source_ref = prospect.get("source_ref")

    existing_id: str | None = None
    if source_ref:
        existing = (
            client()
            .table("prospects")
            .select("id")
            .eq("source", source)
            .eq("source_ref", source_ref)
            .limit(1)
            .execute()
        )
        if existing.data:
            existing_id = existing.data[0]["id"]

    if existing_id:
        client().table("prospects").update(prospect).eq("id", existing_id).execute()
        return existing_id, False

    res = client().table("prospects").insert(prospect).execute()
    return res.data[0]["id"], True
