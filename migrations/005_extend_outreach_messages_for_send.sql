-- Agent 7: track actual send attempts on each message.
-- sent_message_id is the provider's identifier (SMTP Message-Id, WhatsApp
-- wamid, etc.) and lets us correlate later replies (Agent 8).

alter table public.outreach_messages
  add column if not exists sent_message_id text,
  add column if not exists send_error      text,
  add column if not exists attempts        integer not null default 0,
  add column if not exists last_attempt_at timestamptz;

create index if not exists outreach_messages_attempts_idx
  on public.outreach_messages (last_attempt_at desc);
