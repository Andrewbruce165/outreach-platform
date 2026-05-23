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

    # Supabase (Phase 1)
    supabase_jwt_secret: str = Field(..., description="HS256 secret из Supabase project settings → API → JWT Settings")
    supabase_url: str = Field(..., description="Supabase Project URL — нужен фронту и для будущей JWKS-миграции (Pitfall 1)")

    # CORS (Phase 1)
    cors_allowed_origins: str = "http://localhost:5173"

    # CORS (Phase 05.1): regex for Lovable preview deployments.
    # Pitfall 7 — Starlette CORSMiddleware does NOT honor wildcards in
    # allow_origins; allow_origin_regex is the only safe way to accept
    # auto-generated Lovable subdomains (lowercase + digits + hyphens).
    cors_allowed_origin_regex: str = Field(
        default=r"https://[a-z0-9-]+\.lovableproject\.com$",
        validation_alias="CORS_ALLOWED_ORIGIN_REGEX",
        description="Regex (single string) for allow_origin_regex on CORSMiddleware — Lovable preview subdomains.",
    )

    # App settings
    log_level: str = "INFO"
    max_pool_size: int = 10

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
