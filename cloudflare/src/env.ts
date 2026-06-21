export type Env = {
  SUPABASE_URL: string;
  SUPABASE_SERVICE_ROLE_KEY: string;
  FLY_TRIGGER_URL: string;
  FLY_TRIGGER_TOKEN: string;
  PIPELINE_STAGES: string;
};

export const PIPELINE_STAGES = [
  "new", "analyzed", "approved", "contacted", "replied",
  "demo_scheduled", "proposal_sent", "won", "lost",
] as const;

export type Stage = typeof PIPELINE_STAGES[number];

export const STAGE_COLORS: Record<string, string> = {
  new:            "bg-slate-100 text-slate-700",
  analyzed:       "bg-blue-100 text-blue-700",
  approved:       "bg-indigo-100 text-indigo-700",
  contacted:      "bg-purple-100 text-purple-700",
  replied:        "bg-pink-100 text-pink-700",
  demo_scheduled: "bg-amber-100 text-amber-700",
  proposal_sent:  "bg-orange-100 text-orange-700",
  won:            "bg-emerald-100 text-emerald-700",
  lost:           "bg-rose-100 text-rose-700",
};

export const npr = (n: number | null | undefined): string => {
  if (n == null || n === 0) return "—";
  return "रू" + Math.round(n).toLocaleString("en-IN");
};
