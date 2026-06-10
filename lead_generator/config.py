from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    supabase_url: str = Field(..., alias="SUPABASE_URL")
    supabase_service_role_key: str = Field(..., alias="SUPABASE_SERVICE_ROLE_KEY")

    google_places_api_key: str = Field("", alias="GOOGLE_PLACES_API_KEY")

    sender_name: str = Field("Your Name", alias="SENDER_NAME")
    sender_company: str = Field("Your Agency", alias="SENDER_COMPANY")
    sender_email: str = Field("", alias="SENDER_EMAIL")
    sender_calendar_url: str = Field("", alias="SENDER_CALENDAR_URL")

    gmail_app_password: str = Field("", alias="GMAIL_APP_PASSWORD")

    # ---- Gmail IMAP (Agent 8 reply monitor) ----
    # Reuses the SMTP app password by default — Gmail accepts the same one
    # for IMAP. Override IMAP_PASSWORD only if you want a different mailbox.
    imap_host: str = Field("imap.gmail.com", alias="IMAP_HOST")
    imap_port: int = Field(993, alias="IMAP_PORT")
    imap_user: str = Field("", alias="IMAP_USER")            # defaults to SENDER_EMAIL
    imap_password: str = Field("", alias="IMAP_PASSWORD")    # defaults to GMAIL_APP_PASSWORD
    imap_folder: str = Field("INBOX", alias="IMAP_FOLDER")
    imap_max_per_poll: int = Field(50, alias="IMAP_MAX_PER_POLL")

    whatsapp_access_token: str = Field("", alias="WHATSAPP_ACCESS_TOKEN")
    whatsapp_phone_number_id: str = Field("", alias="WHATSAPP_PHONE_NUMBER_ID")
    whatsapp_template_name: str = Field("cold_outreach_v1", alias="WHATSAPP_TEMPLATE_NAME")
    whatsapp_template_language: str = Field("en_US", alias="WHATSAPP_TEMPLATE_LANGUAGE")

    # ---- Operator-side WhatsApp (talk to the agent) ----
    # Your personal number, digits only, e.g. 919876543210 (no '+').
    operator_whatsapp_number: str = Field("", alias="OPERATOR_WHATSAPP_NUMBER")
    # Anything you choose; Meta echoes it back during webhook verification.
    whatsapp_webhook_verify_token: str = Field("", alias="WHATSAPP_WEBHOOK_VERIFY_TOKEN")
    # From your Meta App dashboard. Used to verify X-Hub-Signature-256.
    whatsapp_app_secret: str = Field("", alias="WHATSAPP_APP_SECRET")

    # ---- Approval gates ----
    # Each flag, when true, forces the agent to ask before doing it.
    require_approval_sends:   bool = Field(True,  alias="REQUIRE_APPROVAL_SENDS")
    require_approval_replies: bool = Field(True,  alias="REQUIRE_APPROVAL_REPLIES")
    require_approval_demos:   bool = Field(True,  alias="REQUIRE_APPROVAL_DEMOS")
    require_approval_batches: bool = Field(True,  alias="REQUIRE_APPROVAL_BATCHES")

    log_level: str = Field("INFO", alias="LOG_LEVEL")

    # --- derived helpers ---
    @property
    def effective_imap_user(self) -> str:
        return self.imap_user or self.sender_email

    @property
    def effective_imap_password(self) -> str:
        return self.imap_password or self.gmail_app_password


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
