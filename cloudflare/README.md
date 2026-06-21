# Cloudflare Worker — Dashboard + WhatsApp webhook

Hybrid deploy: this Worker hosts the dashboard and approvals page. All
long-running agent work (Google Places hunt, website analyzer, IMAP reply
monitor, Gmail SMTP sender) stays on **Fly.io**, where the existing Python
app already runs.

WhatsApp is **draft-only**. The dashboard generates message bodies; you copy
them and send manually from your own WhatsApp. There is no Cloud API or Meta
webhook integration.

## Architecture

```
Browser ─┐
         ├──► Cloudflare Worker (this) ──► Supabase  (reads/writes)
Meta ────┘                              └─► Fly.io   (POST /api/trigger)
                                            ▲
                                            └── Python CLI: hunt/analyze/send/imap
```

## Setup

```sh
cd cloudflare
npm install
cp .dev.vars.example .dev.vars
# fill in SUPABASE_*, WHATSAPP_*, FLY_TRIGGER_*
npm run dev          # local at http://127.0.0.1:8787
```

## Deploy

```sh
wrangler login
wrangler secret put SUPABASE_URL
wrangler secret put SUPABASE_SERVICE_ROLE_KEY
wrangler secret put FLY_TRIGGER_URL
wrangler secret put FLY_TRIGGER_TOKEN
npm run deploy
```

## What's NOT in this Worker

- **IMAP polling** — stays on Fly.io. Workers cannot do raw TCP+TLS from Python
  and the JS `connect()` API doesn't implement IMAP framing. The Python
  reply monitor keeps running there.
- **Gmail SMTP sending** — same reason, stays on Fly.io.
- **Long agent runs** (hunt/analyze/write) — Workers have a CPU time limit;
  these are POSTed to Fly.io which executes them in the background.

## What you still need on the Fly.io side

Add a thin HTTP trigger endpoint to the existing FastAPI app (or a separate
Python service) that:

1. Accepts `POST /api/trigger/<agent>` with `Authorization: Bearer <FLY_TRIGGER_TOKEN>`.
2. Schedules the matching agent (`hunt_google_places`, `analyze_prospect`, etc.)
   in a background task, same shape as `lead_generator/web/app.py:_bg_*`.
3. Returns `{ "ok": true }` immediately.

That endpoint replaces the `/actions/*` handlers in the FastAPI dashboard.
The Worker is the new public face; Fly.io is the internal worker pool.

## Outstanding work

- `src/views/prospect.tsx` — prospect detail page (currently returns JSON).
- `src/views/approvals.tsx` — approvals page (currently returns JSON).
- POST handlers for message edit/approve/reject/send-now (small Supabase
  writes; can stay in the Worker).
- Cron Trigger to nudge the Fly.io reply monitor every 10 minutes (commented
  out in `wrangler.toml`).
