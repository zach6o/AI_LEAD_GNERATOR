-- Outbound prospecting system (Agents 1-9 from PRD).
-- Run this once in the Supabase SQL editor for the target project.

-- Pipeline stages per PRD Agent 4.
create type public.prospect_stage as enum (
  'new',
  'analyzed',
  'approved',
  'contacted',
  'replied',
  'demo_scheduled',
  'proposal_sent',
  'won',
  'lost'
);

-- Industries we target. Open ended via 'other' fallback.
create type public.prospect_industry as enum (
  'restaurant',
  'real_estate',
  'dentist',
  'clinic',
  'hotel',
  'salon',
  'gym',
  'law_firm',
  'construction',
  'education',
  'ecommerce',
  'local_business',
  'other'
);

create table public.prospects (
  id              uuid primary key default gen_random_uuid(),

  -- Identity
  business_name   text not null,
  industry        public.prospect_industry not null default 'other',
  location        text,
  country         text,
  source          text not null,
  source_ref      text,

  -- Contact
  website         text,
  email           text,
  phone           text,
  whatsapp        text,
  owner_name      text,
  linkedin_url    text,
  facebook_url    text,
  instagram_url   text,

  -- Signals from the source
  google_rating       numeric(3,2),
  google_reviews      integer,
  employee_count      integer,
  technology_used     text[] default '{}',

  -- Filled by Agents 2/3 later
  issues_found         text[] default '{}',
  recommended_service  text,

  -- Scoring + pipeline
  lead_score    integer not null default 0 check (lead_score between 0 and 100),
  stage         public.prospect_stage not null default 'new',
  notes         text,

  raw           jsonb not null default '{}'::jsonb,

  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  last_contacted  timestamptz
);

create unique index prospects_source_ref_uidx
  on public.prospects (source, source_ref)
  where source_ref is not null;

create index prospects_stage_idx       on public.prospects (stage);
create index prospects_industry_idx    on public.prospects (industry);
create index prospects_lead_score_idx  on public.prospects (lead_score desc);
create index prospects_created_at_idx  on public.prospects (created_at desc);

create or replace function public.set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

create trigger prospects_set_updated_at
  before update on public.prospects
  for each row execute function public.set_updated_at();

create table public.hunt_runs (
  id           uuid primary key default gen_random_uuid(),
  source       text not null,
  niche        text,
  location     text,
  params       jsonb not null default '{}'::jsonb,
  found_count  integer not null default 0,
  inserted_count integer not null default 0,
  updated_count  integer not null default 0,
  error        text,
  started_at   timestamptz not null default now(),
  finished_at  timestamptz
);

create index hunt_runs_started_at_idx on public.hunt_runs (started_at desc);

alter table public.prospects  enable row level security;
alter table public.hunt_runs  enable row level security;
