-- Agents 8 (Reply Monitor) + 9 (Demo Booker) and the operator approval system.
-- Apply this once in the Supabase SQL editor for project wkszgygiiowtygpkoobc.

-- ---------- Reply intent classification ----------
create type public.reply_intent as enum (
  'interested',
  'not_interested',
  'question',
  'unsubscribe',
  'auto_reply',
  'unclear'
);

-- ---------- Replies received from prospects ----------
create table public.prospect_replies (
  id                 uuid primary key default gen_random_uuid(),
  prospect_id        uuid not null references public.prospects(id) on delete cascade,
  outreach_message_id uuid references public.outreach_messages(id) on delete set null,

  channel            public.outreach_channel not null,
  -- Provider IDs for dedup. For email: Message-ID header. For WhatsApp: wamid.
  provider_message_id text,
  -- Threading: matches outreach_messages.sent_message_id when we can tie them.
  in_reply_to        text,

  from_address       text,            -- email address or phone number
  subject            text,
  body               text not null,

  intent             public.reply_intent not null default 'unclear',
  intent_signals     jsonb not null default '{}'::jsonb,

  received_at        timestamptz not null default now(),
  raw                jsonb not null default '{}'::jsonb,
  created_at         timestamptz not null default now()
);

-- Dedup: same provider message arriving twice from a re-poll is a no-op.
create unique index prospect_replies_provider_uidx
  on public.prospect_replies (channel, provider_message_id)
  where provider_message_id is not null;

create index prospect_replies_prospect_idx on public.prospect_replies (prospect_id);
create index prospect_replies_received_idx on public.prospect_replies (received_at desc);
create index prospect_replies_intent_idx   on public.prospect_replies (intent);

alter table public.prospect_replies enable row level security;


-- ---------- Approval requests (operator gate) ----------
-- One row per thing the operator must approve before it happens. Resolved
-- via WhatsApp button taps OR explicit /approve <id> commands.
create type public.approval_kind as enum (
  'send_message',     -- one drafted outreach_message
  'send_reply',       -- agent's drafted reply to a prospect reply
  'book_demo',        -- send the calendar link
  'run_batch'         -- hunt / analyze / write / enrich / send batch
);

create type public.approval_status as enum (
  'pending',
  'approved',
  'rejected',
  'expired',
  'cancelled'
);

create table public.approval_requests (
  id              uuid primary key default gen_random_uuid(),
  kind            public.approval_kind not null,
  status          public.approval_status not null default 'pending',

  -- What is being approved. payload is kind-specific (see operator/approvals.py).
  prospect_id     uuid references public.prospects(id) on delete cascade,
  message_id      uuid references public.outreach_messages(id) on delete cascade,
  payload         jsonb not null default '{}'::jsonb,

  -- Operator-facing copy for the WhatsApp prompt.
  summary         text not null,

  -- Outbound WhatsApp message id of the approval prompt (so we can edit/quote later).
  notify_message_id text,

  created_at      timestamptz not null default now(),
  decided_at      timestamptz,
  decided_by      text,            -- operator phone number, free-form note
  expires_at      timestamptz,

  raw             jsonb not null default '{}'::jsonb
);

create index approval_requests_status_idx     on public.approval_requests (status, created_at);
create index approval_requests_prospect_idx   on public.approval_requests (prospect_id);
create index approval_requests_message_idx    on public.approval_requests (message_id);

alter table public.approval_requests enable row level security;


-- ---------- Operator interactions audit ----------
-- Append-only log of every command/button the operator sent us. Helpful for
-- replaying a session when something goes sideways and for security audit.
create table public.operator_actions (
  id            uuid primary key default gen_random_uuid(),
  from_phone    text not null,
  inbound_text  text,                  -- raw message body or button payload id
  command       text,                  -- parsed command, e.g. 'approve'
  args          jsonb not null default '{}'::jsonb,
  result        text,                  -- what the bot did (one line)
  approval_id   uuid references public.approval_requests(id) on delete set null,
  raw           jsonb not null default '{}'::jsonb,
  created_at    timestamptz not null default now()
);

create index operator_actions_created_idx on public.operator_actions (created_at desc);

alter table public.operator_actions enable row level security;


-- ---------- Extend outreach_status with 'pending_approval' ----------
-- Adds a state for drafts that have been promoted to "waiting for operator
-- approval" but not yet approved. The existing 'approved' status is what the
-- sender actually picks up.
do $$
begin
  if not exists (
    select 1 from pg_enum
    where enumlabel = 'pending_approval'
      and enumtypid = 'public.outreach_status'::regtype
  ) then
    alter type public.outreach_status add value 'pending_approval' before 'approved';
  end if;
end$$;


-- ---------- Extend outreach_kind with 'demo_invite' ----------
-- Agent 9 (Demo Booker) drafts a separate message kind that carries the
-- calendar URL. Kept distinct from initial/followup so the approval flow
-- and analytics can tell them apart.
do $$
begin
  if not exists (
    select 1 from pg_enum
    where enumlabel = 'demo_invite'
      and enumtypid = 'public.outreach_kind'::regtype
  ) then
    alter type public.outreach_kind add value 'demo_invite';
  end if;
end$$;


-- ---------- IMAP polling state ----------
-- Single-row table that records the highest UID we've already pulled from
-- Gmail's [Gmail]/All Mail folder, so polls are O(new only).
create table if not exists public.imap_state (
  id              integer primary key default 1,
  last_uid        bigint not null default 0,
  last_polled_at  timestamptz,
  constraint imap_state_single check (id = 1)
);

insert into public.imap_state (id) values (1) on conflict do nothing;

alter table public.imap_state enable row level security;
