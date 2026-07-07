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

    # Phase 16 (KB-02/KB-03/KB-05): RAG knowledge-base ingest + search knobs.
    # The embedding model is an env knob so it can be swapped without a redeploy;
    # text-embedding-3-small is 1536 dims (mirrored by KbChunk.embedding Vector(1536)).
    openai_embedding_model: str = Field(
        default="text-embedding-3-small",
        validation_alias="OPENAI_EMBEDDING_MODEL",
        description="OpenAI embedding model for KB ingest/search. 1536 dims. Override via env without redeploy.",
    )
    kb_ingest_poll_interval: int = Field(
        default=5,
        validation_alias="KB_INGEST_POLL_INTERVAL",
        description="Polling interval (seconds) for the KnowledgeIngestWorker loop.",
    )
    kb_search_max_distance: float = Field(
        default=0.8,
        validation_alias="KB_SEARCH_MAX_DISTANCE",
        # Calibrated against text-embedding-3-small on a real upload: relevant
        # matches (incl. VERBATIM keyword queries like "Radisson Marriott") land
        # at cosine distance ~0.6-0.7, and a natural-language question ~0.37. The
        # original 0.55 silently filtered verbatim hits ("finds nothing"). 0.8
        # keeps real matches while still dropping clearly-unrelated chunks (>0.85).
        description="Max cosine distance for a KB search hit (Pitfall 4 — distance, not similarity; lower = closer). Tuned for text-embedding-3-small.",
    )
    kb_search_top_k: int = Field(
        default=5,
        validation_alias="KB_SEARCH_TOP_K",
        description="Default top-K chunks returned by search_knowledge_base.",
    )
    kb_chunk_max_tokens: int = Field(
        default=250,
        validation_alias="KB_CHUNK_MAX_TOKENS",
        # 250 (was 800/D-06). Section-sized chunks so a keyword hit returns the
        # RELEVANT passage, not the whole document — a whole résumé was one
        # 800-token chunk, so a "education" keyword match dumped the entire CV.
        # Pairs with the hybrid keyword leg in kb_search (the keyword leg, not
        # chunk size, is what makes terse queries findable; small chunks make the
        # result focused). Balance: small enough to isolate a section, large
        # enough to keep a passage coherent for the agent.
        description="Max tokens per KB chunk (tiktoken cl100k_base). Section-sized so keyword/vector hits return a focused passage, not the whole doc.",
    )
    kb_chunk_overlap: int = Field(
        default=50,
        validation_alias="KB_CHUNK_OVERLAP",
        description="Sliding-window token overlap between adjacent KB chunks (~20% of chunk size).",
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

    # Phase 19 (D-02/D-08): FollowUpWorker tick — sweeps eligible conversations of
    # running follow-up-enabled campaigns and applies the auto-finish-first /
    # ping-else state machine. Interval bounds are in hours; a 5-min tick is fine.
    follow_up_tick_seconds: int = Field(
        default=300,
        validation_alias="FOLLOW_UP_TICK_SECONDS",
        description="Polling interval for FollowUpWorker tick (seconds). "
                    "Interval bounds are in hours; a 5-min tick is fine.",
    )

    # Migration 028: sender write-restriction reconcile (spam-limit / freeze).
    restriction_recheck_interval_seconds: int = Field(
        default=1 * 60 * 60,
        validation_alias="RESTRICTION_RECHECK_INTERVAL",
        description="How long after a spam_limited hit to re-check via SpamBot (seconds); "
                    "recheck via SpamBot after 1h. SpamBot's own quoted release date, when "
                    "parsed, takes precedence over this interval.",
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
    contact_check_rest_seconds: int = Field(
        default=300,
        validation_alias="CONTACT_CHECK_REST_SECONDS",
        description="Benign post-batch REST (Plan 14-07, Q3): after a checker finishes "
                    "a non-raising resolve batch it rests this many seconds before being "
                    "re-selected, so the worker cannot chain batch-after-batch on ONE "
                    "account past the ~45-50 burst onset (the existing rotation alternates "
                    "≥2 checkers meanwhile). 300s = 5 min start value (tune later). This is "
                    "NOT a restriction cooldown — it touches only senders.checker_rest_until.",
    )
    contact_check_probe_interval_seconds: int = Field(
        default=900,  # 15 min — see justification below
        validation_alias="CONTACT_CHECK_PROBE_INTERVAL_SECONDS",
        description="Minimum seconds between active control-probes of the SAME checker. The "
                    "probe ran every ~5s poll tick (~4267 probe-batches/account/day — the "
                    "dominant burn). The inline 14-05 anomaly detector already catches throttle "
                    "for FREE on every real batch, so the active probe is a rare backstop. 900s "
                    "(15min) is well under the >5min onset window (fresh tripped ~76 resolves, "
                    "rested controls ~47-49) — the probe verifies health periodically without "
                    "contributing meaningful load.",
    )
    contact_check_max_backoff_seconds: int = Field(
        default=6 * 60 * 60,  # 6h ceiling
        validation_alias="CONTACT_CHECK_MAX_BACKOFF_SECONDS",
        description="Ceiling for the escalating per-checker cooldown. A repeatedly-tripping "
                    "checker backs off cooldown_seconds * 2^(trip_count-1), capped here, so a "
                    "cycling account rests for hours not a fixed 15min and stops re-tripping.",
    )

    # Phase 21 (IMPT-01 / RESEARCH Pitfall 7): bulk account-import ZIP-safety caps.
    # Enforced by app/services/account_import.py::unpack_and_pair BEFORE extraction so
    # a ZIP-bomb or an oversized batch is rejected as a structured 413/422, not a 500.
    max_import_uncompressed_bytes: int = Field(
        default=52428800,  # 50 MB
        validation_alias="MAX_IMPORT_UNCOMPRESSED_BYTES",
        description="Max total UNCOMPRESSED size (bytes) of a bulk account-import ZIP. "
                    "Summed from ZipInfo.file_size before extraction (ZIP-bomb guard). "
                    "~28 KB/session × 500 accounts + JSONs ≈ 14 MB, so 50 MB is generous.",
    )
    max_import_accounts: int = Field(
        default=500,
        validation_alias="MAX_IMPORT_ACCOUNTS",
        description="Max distinct account basenames (.json/.session pairs) accepted in a "
                    "single bulk-import ZIP. Over the cap → 422 TOO_MANY_ACCOUNTS.",
    )

    @property
    def cors_origins_list(self) -> list[str]:
        """Парсит CORS_ALLOWED_ORIGINS в list для FastAPI CORSMiddleware."""
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]


@lru_cache()
def get_settings() -> Settings:
    return Settings()
