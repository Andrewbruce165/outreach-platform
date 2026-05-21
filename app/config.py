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

    # App settings
    log_level: str = "INFO"
    max_pool_size: int = 10

    # Decodo proxy pool (optional)
    decodo_host: Optional[str] = None
    decodo_username: Optional[str] = None
    decodo_password: Optional[str] = None
    decodo_ports: Optional[str] = None  # comma-separated: "10001,10002,...,10010"

    @property
    def cors_origins_list(self) -> list[str]:
        """Парсит CORS_ALLOWED_ORIGINS в list для FastAPI CORSMiddleware."""
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]


@lru_cache()
def get_settings() -> Settings:
    return Settings()
