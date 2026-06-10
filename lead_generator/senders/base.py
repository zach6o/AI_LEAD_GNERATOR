from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SendResult:
    ok: bool
    provider_message_id: str | None = None
    error: str | None = None
