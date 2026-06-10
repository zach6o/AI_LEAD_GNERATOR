-- Agent 5: Outreach Writer output.
-- One row per (prospect, channel, kind). Agent 6 (Approval) updates status;
-- Agent 7 (Sender) sets sent_at when delivery succeeds.

create type public.outreach_channel as enum ('email', 'whatsapp', 'linkedin');
create type public.outreach_kind    as enum ('initial', 'followup_1', 'followup_2');
create type public.outreach_status  as enum ('draft', 'approved', 'rejected', 'sent');

create table public.outreach_messages (
  id              uuid primary key default gen_random_uuid(),
  prospect_id     uuid not null references public.prospects(id) on delete cascade,
  opportunity_id  uuid references public.opportunities(id) on delete set null,

  channel         public.outreach_channel not null,
  kind            public.outreach_kind not null default 'initial',

  -- Email only.
  subject         text,
  -- Required for all channels.
  body            text not null,

  generated_by    text not null default 'template',     -- 'template' | 'claude_sonnet_4_6' | ...
  status          public.outreach_status not null default 'draft',

  approved_at     timestamptz,
  approved_by     text,
  sent_at         timestamptz,

  meta            jsonb not null default '{}'::jsonb,

  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

create unique index outreach_messages_uidx
  on public.outreach_messages (prospect_id, channel, kind);

create index outreach_messages_status_idx   on public.outreach_messages (status);
create index outreach_messages_prospect_idx on public.outreach_messages (prospect_id);

create trigger outreach_messages_set_updated_at
  before update on public.outreach_messages
  for each row execute function public.set_updated_at();

alter table public.outreach_messages enable row level security;
