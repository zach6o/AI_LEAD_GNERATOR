# AI Lead Generator

Outbound lead-generation + sales-automation agent for a web/AI agency.

Built per the PRD in this repo. Currently shipping **Agent 1: Lead Hunter** (MVP).

## Stack
- Python 3.11+, Typer CLI
- Supabase (Postgres) — project `AI_AGENT-01`
- Google Places API (New) as the first lead source

## Quickstart

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# Fill in SUPABASE_SERVICE_ROLE_KEY and GOOGLE_PLACES_API_KEY
python -m lead_generator.cli hunt --niche restaurants --location "Mumbai, India" --limit 25
```

## Layout

```
lead_generator/
  config.py         # Settings loaded from .env
  db.py             # Supabase client + prospect upsert
  scoring.py        # Lead scoring rules
  sources/
    google_places.py  # Agent 1 source: Google Maps/Places
  agents/
    lead_hunter.py    # Orchestrates a hunt run
  cli.py            # `python -m lead_generator.cli ...`
```

## Schema

The outbound system uses its own `prospects` table (separate from the existing
inbound chat `leads` table). See `migrations/` and Supabase project for the
canonical schema.
