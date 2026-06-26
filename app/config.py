from pydantic import ConfigDict, Field
from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import Optional


class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env", case_sensitive=False)

    # Database
    database_url: str

    # Telegram
    telegram_api_id: int
    telegram_api_hash: str

    # Security
    encryption_key: str

    # Supabase (Phase 1; Phase 05.1-DEBUG 2026-05-23 — ES256/JWKS support added)
    # `supabase_jwt_secret` is now optional. It is only used as a fallback when a
    # token's header alg=HS256 (legacy projects pinned to symmetric signing).
    # The primary verification path is ES256 against the project's JWKS endpoint
    # (`${supabase_url}/auth/v1/.well-known/jwks.json`), which is Supabase's
    # default since Oct 2025.
    supabase_jwt_secret: Optional[str] = Field(
        default=None,
        description="LEGACY HS256 secret (Supabase → Settings → API → JWT Settings). "
                    "Optional — only used when token header alg=HS256. ES256 path uses JWKS.",
    )
    supabase_url: str = Field(
        ...,
        description="Supabase Project URL — used for JWKS endpoint and frontend. Required.",
    )

    # CORS (Phase 1)
    cors_allowed_origins: str = "http://localhost:5173"

    # CORS (Phase 05.1): regex for Lovable preview deployments.
    # Pitfall 7 — Starlette CORSMiddleware does NOT honor wildcards in
    # allow_origins; allow_origin_regex is the only safe way to accept
    # auto-generated Lovable subdomains (lowercase + digits + hyphens).
    cors_allowed_origin_regex: str = Field(
        default=r"^https://[a-z0-9-]+\.(lovableproject\.com|lovable\.app)$",
        validation_alias="CORS_ALLOWED_ORIGIN_REGEX",
        description="Regex (single string) for allow_origin_regex on CORSMiddleware — Lovable preview + prod subdomains (both .lovableproject.com legacy and .lovable.app current).",
    )

    # App settings
    log_level: str = "INFO"
    max_pool_size: int = 10

    # OpenAI
    openai_model: str = Field(
        default="gpt-5-mini-2025-08-07",
        validation_alias="OPENAI_MODEL",
        description="OpenAI chat model used by ai_engine + warmup. Override via env without redeploy.",
    )

    # Decodo proxy pool (optional)
    decodo_host: Optional[str] = None
    decodo_username: Optional[str] = None
    decodo_password: Optional[str] = None
    decodo_ports: Optional[str] = None  # comma-separated: "10001,10002,...,10010"

    # Phase 4 D-17: CampaignEnqueueWorker tick (background generator queue items per running campaign).
    campaign_enqueue_tick_seconds: int = Field(
        default=30,
        validation_alias="CAMPAIGN_ENQUEUE_TICK_SECONDS",
        description="Polling interval for CampaignEnqueueWorker tick (seconds).",
    )
    campaign_enqueue_batch_size: int = Field(
        default=500,
        validation_alias="CAMPAIGN_ENQUEUE_BATCH_SIZE",
        description="Max contacts processed per campaign per tick.",
    )

    # Migration 028: sender write-restriction reconcile (spam-limit / freeze).
    restriction_recheck_interval_seconds: int = Field(
        default=6 * 60 * 60,
        validation_alias="RESTRICTION_RECHECK_INTERVAL",
        description="How long after a spam_limited hit to re-check via SpamBot (seconds). "
                    "check_spambot does not expose limit_until, so this is a fixed delay.",
    )
    restriction_reconcile_interval_seconds: int = Field(
        default=15 * 60,
        validation_alias="RESTRICTION_RECONCILE_INTERVAL",
        description="Cadence of the listener background sweep that re-checks restricted senders (seconds).",
    )

    # Phase 14 (RESV-02 / D-10): contact-resolution rate knobs for the checker pool.
    # Conservative defaults keep resolve volume under the ~45–50 empirical shadow-ban
    # onset so a misconfigured deploy cannot uncap the worker. SEPARATE knob set from
    # the empirical send-queue constants in queue.py (CLAUDE.md guard — do not unify).
    contact_check_burst_cap: int = Field(
        default=30,
        validation_alias="CONTACT_CHECK_BURST_CAP",
        description="Max resolves a single checker performs per batch, under the "
                    "~45–50 empirical contacts-API throttle onset.",
    )
    contact_check_pace_low: float = Field(
        default=2.0,
        validation_alias="CONTACT_CHECK_PACE_LOW",
        description="Lower bound (seconds) of the polite delay between resolves — "
                    "matches random.uniform(2.0, 3.5) at checker.py:259 so the knob "
                    "and the hard-coded delay stay consistent.",
    )
    contact_check_pace_high: float = Field(
        default=3.5,
        validation_alias="CONTACT_CHECK_PACE_HIGH",
        description="Upper bound (seconds) of the polite delay between resolves — "
                    "matches random.uniform(2.0, 3.5) at checker.py:259.",
    )
    contact_check_cooldown_seconds: int = Field(
        default=900,
        validation_alias="CONTACT_CHECK_COOLDOWN_SECONDS",
        description="How long a degraded checker rests before a fresh control-probe "
                    "is attempted (D-04 checker recovery).",
    )
    contact_check_daily_cap: int = Field(
        default=400,
        validation_alias="CONTACT_CHECK_DAILY_CAP",
        description="Per-checker resolves/day ceiling (durable-counted from "
                    "contacts_cache writes today in Plan 02).",
    )

    @property
    def cors_origins_list(self) -> list[str]:
        """Парсит CORS_ALLOWED_ORIGINS в list для FastAPI CORSMiddleware."""
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]


@lru_cache()
def get_settings() -> Settings:
    return Settings()
