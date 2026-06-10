-- Agent 2: Website Analyzer output.
-- One row per (prospect, analysis run). Latest row per prospect is the
-- "current" view — older rows are retained for change tracking.

create table public.website_analyses (
  id              uuid primary key default gen_random_uuid(),
  prospect_id     uuid not null references public.prospects(id) on delete cascade,
  url             text not null,

  -- Transport
  http_status     integer,
  ssl_valid       boolean,
  response_ms     integer,
  page_bytes      integer,

  -- HTML hygiene
  has_viewport    boolean default false,   -- mobile responsive meta tag
  has_title       boolean default false,
  has_meta_desc   boolean default false,
  has_og_tags     boolean default false,
  has_favicon     boolean default false,

  -- Conversion features
  has_lead_form   boolean default false,
  has_chat        boolean default false,
  has_booking     boolean default false,
  has_analytics   boolean default false,

  -- Freshness signal
  copyright_year  integer,

  -- Detected tech (CMS, builders, frameworks)
  tech_detected   text[] default '{}',

  -- Findings + classification
  issues               text[] default '{}',
  recommended_service  text,

  -- 0-100; higher = healthier site (LOWER = bigger opportunity for us).
  final_score   integer not null default 0 check (final_score between 0 and 100),

  raw           jsonb not null default '{}'::jsonb,
  error         text,

  analyzed_at   timestamptz not null default now()
);

create index website_analyses_prospect_idx   on public.website_analyses (prospect_id);
create index website_analyses_score_idx      on public.website_analyses (final_score asc);
create index website_analyses_analyzed_idx   on public.website_analyses (analyzed_at desc);

alter table public.website_analyses enable row level security;
