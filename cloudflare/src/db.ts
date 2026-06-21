import { createClient, SupabaseClient } from "@supabase/supabase-js";
import type { Env } from "./env";

export const sb = (env: Env): SupabaseClient =>
  createClient(env.SUPABASE_URL, env.SUPABASE_SERVICE_ROLE_KEY, {
    auth: { persistSession: false, autoRefreshToken: false },
  });

export type Prospect = {
  id: string;
  business_name: string;
  industry: string | null;
  location: string | null;
  website: string | null;
  phone: string | null;
  email: string | null;
  lead_score: number | null;
  stage: string | null;
  recommended_service: string | null;
  last_contacted: string | null;
  notes: string | null;
  google_rating: number | null;
  google_reviews: number | null;
  website_analyses?: Array<{
    id: string;
    final_score: number | null;
    issues: string[] | null;
    has_chat: boolean | null;
    has_booking: boolean | null;
    has_lead_form: boolean | null;
    has_analytics: boolean | null;
    has_viewport: boolean | null;
    response_ms: number | null;
    http_status: number | null;
    error: string | null;
    tech_detected: string[] | null;
  }>;
  opportunities?: Array<{
    id: string;
    primary_service: string;
    secondary_services: string[] | null;
    monthly_revenue_impact_inr: number | null;
    our_one_time_inr: number | null;
    our_monthly_inr: number | null;
    roi_months: number | null;
    confidence: number | null;
    pitch_summary: string | null;
    hours_saved_per_week: number | null;
  }>;
  outreach_messages?: Array<{
    id: string;
    channel: string;
    kind: string;
    subject: string | null;
    body: string;
    status: string;
    generated_by: string | null;
    sent_at: string | null;
    send_error: string | null;
    attempts: number | null;
  }>;
  prospect_replies?: Array<{
    id: string;
    channel: string;
    from_address: string | null;
    subject: string | null;
    body: string;
    intent: string;
    received_at: string | null;
  }>;
};

const SELECT_PROSPECT = `
  id, business_name, industry, location, website, phone, email,
  lead_score, stage, recommended_service, last_contacted, notes,
  google_rating, google_reviews,
  website_analyses(id, final_score, issues, has_chat, has_booking,
    has_lead_form, has_analytics, has_viewport, response_ms, http_status, error, tech_detected),
  opportunities(id, primary_service, secondary_services,
    monthly_revenue_impact_inr, our_one_time_inr, our_monthly_inr,
    roi_months, confidence, pitch_summary, hours_saved_per_week),
  outreach_messages(id, channel, kind, subject, body, status, generated_by, sent_at, send_error, attempts),
  prospect_replies(id, channel, from_address, subject, body, intent, received_at)
`;

export async function loadProspects(
  env: Env,
  filters: { stage?: string; industry?: string } = {}
): Promise<Prospect[]> {
  let q = sb(env).from("prospects").select(SELECT_PROSPECT);
  if (filters.stage) q = q.eq("stage", filters.stage);
  if (filters.industry) q = q.eq("industry", filters.industry);
  const { data, error } = await q;
  if (error) throw new Error(`supabase: ${error.message}`);
  return (data ?? []) as Prospect[];
}

export async function loadProspect(env: Env, id: string): Promise<Prospect | null> {
  const { data, error } = await sb(env)
    .from("prospects")
    .select(SELECT_PROSPECT)
    .eq("id", id)
    .limit(1)
    .maybeSingle();
  if (error) throw new Error(`supabase: ${error.message}`);
  return (data ?? null) as Prospect | null;
}

export type Decorated = Prospect & {
  analysis: Prospect["website_analyses"] extends Array<infer T> ? T | null : null;
  opportunity: Prospect["opportunities"] extends Array<infer T> ? T | null : null;
  bestRev: number;
  messagesByChannel: Record<string, Record<string, NonNullable<Prospect["outreach_messages"]>[number]>>;
  sortedReplies: NonNullable<Prospect["prospect_replies"]>;
};

export function decorate(p: Prospect): Decorated {
  const analysis = (p.website_analyses?.[0] ?? null) as any;
  const opps = p.opportunities ?? [];
  const opportunity = opps.length
    ? opps.reduce((best, o) =>
        (o.monthly_revenue_impact_inr ?? 0) > (best.monthly_revenue_impact_inr ?? 0) ? o : best
      , opps[0]!)
    : null;
  const bestRev = opportunity?.monthly_revenue_impact_inr ?? 0;

  const messagesByChannel: Decorated["messagesByChannel"] = {};
  for (const m of p.outreach_messages ?? []) {
    (messagesByChannel[m.channel] ??= {})[m.kind] = m;
  }

  const sortedReplies = [...(p.prospect_replies ?? [])].sort((a, b) =>
    (b.received_at ?? "").localeCompare(a.received_at ?? "")
  );

  return { ...p, analysis, opportunity, bestRev, messagesByChannel, sortedReplies };
}
