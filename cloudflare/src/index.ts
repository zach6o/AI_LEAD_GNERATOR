import { Hono } from "hono";
import { decorate, loadProspect, loadProspects, sb, type Decorated } from "./db";
import { PIPELINE_STAGES, type Env } from "./env";
import { Dashboard } from "./views/dashboard";

const app = new Hono<{ Bindings: Env }>();

app.get("/health", (c) => c.json({ status: "ok", service: "lead-generator" }));

app.get("/", async (c) => {
  const stage = c.req.query("stage") ?? null;
  const industry = c.req.query("industry") ?? null;
  const msg = c.req.query("msg") ?? null;

  const raw = await loadProspects(c.env, {
    stage: stage ?? undefined,
    industry: industry ?? undefined,
  });
  const rows: Decorated[] = raw.map(decorate).sort((a, b) => b.bestRev - a.bestRev);

  const stage_counts: Record<string, number> = Object.fromEntries(PIPELINE_STAGES.map((s) => [s, 0]));
  for (const r of rows) stage_counts[r.stage ?? "new"] = (stage_counts[r.stage ?? "new"] ?? 0) + 1;

  const summary = {
    total_prospects: rows.length,
    total_monthly_rev_inr: rows.reduce((s, r) => s + (r.bestRev || 0), 0),
    stage_counts,
    won_count: stage_counts.won ?? 0,
    lost_count: stage_counts.lost ?? 0,
  };

  const industries = [...new Set(rows.map((r) => r.industry ?? "other"))].sort();

  return c.html(
    <Dashboard
      rows={rows}
      summary={summary}
      industries={industries}
      current_stage={stage}
      current_industry={industry}
      flash={msg}
    />,
  );
});

// Prospect detail — minimal version returns JSON for now. Build a JSX page next.
app.get("/prospects/:id", async (c) => {
  const id = c.req.param("id");
  const p = await loadProspect(c.env, id);
  if (!p) return c.text("Prospect not found", 404);
  return c.json(decorate(p));
});

// Approvals — list pending (uses a hypothetical operator_approvals table; adapt to your schema).
app.get("/approvals", async (c) => {
  const { data, error } = await sb(c.env)
    .from("operator_approvals")
    .select("id, kind, summary, created_at, prospect_id, message_id")
    .eq("status", "pending")
    .order("created_at", { ascending: false })
    .limit(100);
  if (error) return c.text(`supabase: ${error.message}`, 500);
  return c.json(data ?? []);
});

// All /actions/* forward to Fly.io, which still owns the agent runtime.
app.post("/actions/:agent", async (c) => {
  const agent = c.req.param("agent");
  const form = await c.req.formData();
  const payload = Object.fromEntries([...form.entries()]);

  const res = await fetch(`${c.env.FLY_TRIGGER_URL}/${agent}`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "authorization": `Bearer ${c.env.FLY_TRIGGER_TOKEN}`,
    },
    body: JSON.stringify(payload),
  });

  const ok = res.ok;
  const text = ok ? "queued" : `Fly trigger failed: ${res.status}`;
  const url = new URL(c.req.url);
  url.pathname = "/";
  url.searchParams.set("msg", text);
  return c.redirect(url.toString(), 303);
});

// Mark prospect as replied — one-click from the dashboard when a manual
// WhatsApp/email reply lands in your own inbox.
app.post("/prospects/:id/mark-replied", async (c) => {
  const id = c.req.param("id");
  const { error } = await sb(c.env).from("prospects").update({ stage: "replied" }).eq("id", id);
  if (error) return c.text(`supabase: ${error.message}`, 500);
  return c.redirect(`/prospects/${id}?msg=Marked+as+replied`, 303);
});

export default app;
