-- Agent 3: Opportunity Finder.
-- One row = one pitched opportunity for a prospect (most prospects get one
-- primary opportunity; re-running upserts based on (prospect_id, primary_service)).

create table public.opportunities (
  id                    uuid primary key default gen_random_uuid(),
  prospect_id           uuid not null references public.prospects(id) on delete cascade,
  website_analysis_id   uuid references public.website_analyses(id) on delete set null,

  primary_service       text not null,
  secondary_services    text[] default '{}',

  -- Estimated impact on the *prospect's* business.
  monthly_revenue_impact_inr   integer,
  hours_saved_per_week         integer,

  -- What WE will charge them.
  our_one_time_inr      integer,
  our_monthly_inr       integer,

  -- Payback period in months. Lower = easier sell.
  roi_months            numeric(6,2),

  -- 0-100 how confident the rules are in these numbers.
  confidence            integer not null default 0 check (confidence between 0 and 100),

  -- A one/two-sentence hook for outreach. Editable later by humans.
  pitch_summary         text,

  -- Transparent breakdown of how the numbers were computed.
  reasoning             jsonb not null default '{}'::jsonb,

  created_at            timestamptz not null default now()
);

create unique index opportunities_prospect_service_uidx
  on public.opportunities (prospect_id, primary_service);

create index opportunities_confidence_idx on public.opportunities (confidence desc);
create index opportunities_revenue_idx    on public.opportunities (monthly_revenue_impact_inr desc nulls last);
create index opportunities_prospect_idx   on public.opportunities (prospect_id);

alter table public.opportunities enable row level security;
