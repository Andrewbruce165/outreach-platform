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

    @property
    def cors_origins_list(self) -> list[str]:
        """Парсит CORS_ALLOWED_ORIGINS в list для FastAPI CORSMiddleware."""
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]


@lru_cache()
def get_settings() -> Settings:
    return Settings()
