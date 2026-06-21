# Cloudflare Pages — Dashboard (Advanced Mode)

Hybrid deploy: this Pages project hosts the dashboard and approvals page.
All long-running agent work (Google Places hunt, website analyzer, IMAP
reply monitor, Gmail SMTP sender) stays on **Fly.io**, where the existing
Python app already runs.

WhatsApp is **draft-only**. The dashboard generates message bodies; you
copy them and send manually from your own WhatsApp. There is no Cloud API
or Meta webhook integration.

## Why Pages and not Workers?

You don't need a Cloudflare API token, wrangler CLI, or paid plan. Pages
connects directly to your GitHub repo. Push → auto-deploy.

This project uses **Pages Advanced Mode**: `src/index.ts` is bundled by
esbuild into a single `dist/_worker.js`. Pages discovers that file and
routes every request through the Hono app.

## One-time setup (5 minutes, all in the browser)

1. Push this repo to GitHub (already done).
2. Sign in at <https://dash.cloudflare.com/> with a free account.
3. **Workers & Pages → Create → Pages → Connect to Git**.
4. Select the repo `zach6o/AI_LEAD_GNERATOR`.
5. Configure the build:
   - **Framework preset:** None
   - **Build command:** `npm install && npm run build`
   - **Build output directory:** `dist`
   - **Root directory (advanced):** `cloudflare`
   - **Environment variables (production):**
     | Name | Value | Encrypt? |
     |---|---|---|
     | `SUPABASE_URL` | `https://hbbkhcafmdxprepzpqtt.supabase.co` | no |
     | `SUPABASE_SERVICE_ROLE_KEY` | *(your service-role key)* | ✅ yes |
     | `FLY_TRIGGER_URL` | `https://YOUR_APP.fly.dev/api/trigger` | no |
     | `FLY_TRIGGER_TOKEN` | *(random string, also set on Fly.io)* | ✅ yes |
6. **Save and deploy.** First build takes ~1 minute. The URL will be
   `https://ai-lead-generator-dashboard.pages.dev` (or similar).

Every subsequent `git push origin main` auto-deploys.

## Local development (optional)

```sh
cd cloudflare
npm install
cp .dev.vars.example .dev.vars      # fill in the same values as above
npm run build                       # generates dist/_worker.js
npm run dev                         # local at http://127.0.0.1:8788
```

## What's NOT in this Pages project

- **IMAP polling** — stays on Fly.io. Workers/Pages cannot do raw TCP+TLS
  reliably, and the JS `connect()` API doesn't implement IMAP framing.
- **Gmail SMTP sending** — same reason, stays on Fly.io.
- **Long agent runs** (hunt/analyze/write) — Pages Functions have a CPU
  time limit (default 30s); these are POSTed to Fly.io which runs them in
  the background.

## What you still need on the Fly.io side

Add a thin HTTP trigger endpoint to the existing FastAPI app:

```python
# lead_generator/web/app.py
@app.post("/api/trigger/{agent}")
async def trigger(agent: str, request: Request, background: BackgroundTasks):
    if request.headers.get("authorization") != f"Bearer {settings.fly_trigger_token}":
        raise HTTPException(401)
    payload = await request.json()
    AGENTS = {"hunt": _bg_hunt, "analyze": _bg_analyze, ...}  # already exist
    if agent not in AGENTS:
        raise HTTPException(404)
    background.add_task(AGENTS[agent], **payload)
    return {"ok": True}
```

Then add `FLY_TRIGGER_TOKEN` to your Fly secrets (`fly secrets set FLY_TRIGGER_TOKEN=...`).

## Outstanding work

- `src/views/prospect.tsx` — prospect detail page (currently returns JSON).
- `src/views/approvals.tsx` — approvals page (currently returns JSON).
- POST handlers for message edit/approve/reject/send-now (small Supabase
  writes; can stay in the Worker).
