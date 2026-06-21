import type { FC } from "hono/jsx";
import { Layout } from "./layout";
import { PIPELINE_STAGES, STAGE_COLORS, npr } from "../env";
import type { Decorated } from "../db";

type Props = {
  rows: Decorated[];
  summary: {
    total_prospects: number;
    total_monthly_rev_inr: number;
    stage_counts: Record<string, number>;
    won_count: number;
    lost_count: number;
  };
  industries: string[];
  current_stage?: string | null;
  current_industry?: string | null;
  flash?: string | null;
};

export const Dashboard: FC<Props> = ({ rows, summary, industries, current_stage, current_industry, flash }) => (
  <Layout title="Pipeline — Lead Generator">
    {flash && (
      <div class="mb-4 rounded-md border border-emerald-200 bg-emerald-50 text-emerald-800 px-4 py-2 text-sm">
        {flash}
      </div>
    )}

    <div class="card p-4 mb-6">
      <div class="text-xs uppercase tracking-wide text-slate-500 mb-3">Actions</div>
      <form method="post" action="/actions/hunt" class="flex flex-wrap items-end gap-2 mb-3">
        <label class="flex-1 min-w-[150px]">
          <span class="text-xs text-slate-500">Niche</span>
          <input name="niche" required placeholder="dentists / restaurants / salons / ..."
                 class="w-full rounded-md border border-slate-200 px-2 py-1 mt-0.5 text-sm" />
        </label>
        <label class="flex-1 min-w-[150px]">
          <span class="text-xs text-slate-500">Location</span>
          <input name="location" required placeholder="Kathmandu, Nepal"
                 class="w-full rounded-md border border-slate-200 px-2 py-1 mt-0.5 text-sm" />
        </label>
        <label class="w-20">
          <span class="text-xs text-slate-500">Limit</span>
          <input name="limit" type="number" value="20" min="1" max="60"
                 class="w-full rounded-md border border-slate-200 px-2 py-1 mt-0.5 text-sm" />
        </label>
        <label class="w-20">
          <span class="text-xs text-slate-500">Region</span>
          <input name="region" placeholder="NP" maxLength={2}
                 class="w-full rounded-md border border-slate-200 px-2 py-1 mt-0.5 text-sm uppercase" />
        </label>
        <button class="px-3 py-2 rounded-md bg-slate-900 text-white text-sm font-medium hover:bg-slate-800">Hunt</button>
      </form>

      <div class="flex flex-wrap gap-2">
        {[
          ["analyze", "Analyze pending", "bg-blue-600 hover:bg-blue-500"],
          ["opportunities", "Score opportunities", "bg-indigo-600 hover:bg-indigo-500"],
          ["enrich", "Enrich emails", "bg-amber-500 hover:bg-amber-400"],
          ["write", "Draft messages", "bg-purple-600 hover:bg-purple-500"],
          ["queue", "Queue for approval", "bg-fuchsia-600 hover:bg-fuchsia-500"],
          ["send-emails", "Send approved emails", "bg-emerald-600 hover:bg-emerald-500"],
          ["monitor", "Check replies", "bg-pink-600 hover:bg-pink-500"],
          ["demos", "Draft demo invites", "bg-orange-600 hover:bg-orange-500"],
        ].map(([action, label, color]) => (
          <form method="post" action={`/actions/${action}`} class="inline">
            <input type="hidden" name="limit" value="50" />
            <button class={`px-3 py-1.5 rounded text-white text-xs font-medium ${color}`}>{label}</button>
          </form>
        ))}
      </div>
    </div>

    <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
      <div class="card p-4">
        <div class="text-xs uppercase tracking-wide text-slate-500">Prospects</div>
        <div class="text-3xl font-semibold mt-1">{summary.total_prospects}</div>
      </div>
      <div class="card p-4">
        <div class="text-xs uppercase tracking-wide text-slate-500">Est. Monthly Pipeline</div>
        <div class="text-3xl font-semibold mt-1 text-emerald-600">{npr(summary.total_monthly_rev_inr)}</div>
        <div class="text-xs text-slate-400 mt-1">sum of best opportunity per prospect</div>
      </div>
      <div class="card p-4">
        <div class="text-xs uppercase tracking-wide text-slate-500">Won</div>
        <div class="text-3xl font-semibold mt-1 text-emerald-700">{summary.won_count}</div>
      </div>
      <div class="card p-4">
        <div class="text-xs uppercase tracking-wide text-slate-500">Lost</div>
        <div class="text-3xl font-semibold mt-1 text-rose-600">{summary.lost_count}</div>
      </div>
    </div>

    <div class="card p-4 mb-4">
      <div class="text-xs uppercase tracking-wide text-slate-500 mb-2">Pipeline</div>
      <div class="flex flex-wrap gap-2">
        {PIPELINE_STAGES.map((s) => (
          <a
            href={`/?${current_industry ? `industry=${current_industry}&` : ""}stage=${s}`}
            class={`badge ${STAGE_COLORS[s]} ${current_stage === s ? "ring-2 ring-offset-1 ring-slate-400" : ""}`}
          >
            {s} · {summary.stage_counts[s] ?? 0}
          </a>
        ))}
        {(current_stage || current_industry) && (
          <a href="/" class="badge bg-slate-50 text-slate-500 hover:bg-slate-100">clear filters</a>
        )}
      </div>
    </div>

    <div class="card p-4 mb-4 flex flex-wrap items-center gap-3">
      <div class="text-xs uppercase tracking-wide text-slate-500">Industry</div>
      <a
        href={`/?${current_stage ? `stage=${current_stage}` : ""}`}
        class={`badge bg-slate-100 text-slate-700 ${!current_industry ? "ring-2 ring-slate-300" : ""}`}
      >all</a>
      {industries.map((ind) => (
        <a
          href={`/?${current_stage ? `stage=${current_stage}&` : ""}industry=${ind}`}
          class={`badge bg-slate-100 text-slate-700 ${current_industry === ind ? "ring-2 ring-slate-400" : ""}`}
        >{ind}</a>
      ))}
    </div>

    <div class="card overflow-hidden">
      <table class="w-full text-sm">
        <thead class="bg-slate-50 text-slate-500 text-xs uppercase tracking-wide">
          <tr>
            <th class="text-left px-4 py-2 font-medium">Business</th>
            <th class="text-left px-4 py-2 font-medium">Industry</th>
            <th class="text-right px-4 py-2 font-medium">Lead</th>
            <th class="text-right px-4 py-2 font-medium">Site</th>
            <th class="text-right px-4 py-2 font-medium">Rev /mo</th>
            <th class="text-right px-4 py-2 font-medium">ROI mo</th>
            <th class="text-left px-4 py-2 font-medium">Pitch</th>
            <th class="text-left px-4 py-2 font-medium">Stage</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-slate-100">
          {rows.map((r) => (
            <tr class="table-row cursor-pointer" onclick={`window.location='/prospects/${r.id}'`}>
              <td class="px-4 py-3">
                <div class="font-medium">{r.business_name}</div>
                <div class="text-xs text-slate-500">{r.phone || "—"}</div>
              </td>
              <td class="px-4 py-3 text-slate-600">{r.industry}</td>
              <td class="px-4 py-3 text-right font-semibold">{r.lead_score}</td>
              <td class="px-4 py-3 text-right text-slate-600">{r.analysis?.final_score ?? "—"}</td>
              <td class="px-4 py-3 text-right text-emerald-700 font-semibold">
                {r.opportunity ? npr(r.opportunity.monthly_revenue_impact_inr) : "—"}
              </td>
              <td class="px-4 py-3 text-right text-slate-600">
                {r.opportunity?.roi_months != null ? r.opportunity.roi_months.toFixed(2) : "—"}
              </td>
              <td class="px-4 py-3 text-slate-700">
                {r.opportunity?.primary_service ?? r.recommended_service ?? "—"}
              </td>
              <td class="px-4 py-3">
                <span class={`badge ${STAGE_COLORS[r.stage ?? "new"]}`}>{r.stage ?? "new"}</span>
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td colspan={8} class="px-4 py-8 text-center text-slate-500">
                No prospects match these filters.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  </Layout>
);
