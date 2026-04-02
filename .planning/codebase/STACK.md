# Technology Stack

**Analysis Date:** 2026-04-02

## Languages

**Primary:**
- Python 3.11 - All application code (API, listener, services, models)

**Secondary:**
- SQL - Raw migration scripts in `migrations/` (no ORM-based migrations)

## Runtime

**Environment:**
- Python 3.11-slim (Docker base image, see `Dockerfile` and `Dockerfile.listener`)
- No browser runtime

**Package Manager:**
- pip (no version pinned)
- Lockfile: none — `requirements.txt` with pinned versions used directly

## Frameworks

**Core:**
- FastAPI 0.109.0 - HTTP API server (`app/main.py`)
- Uvicorn 0.27.0 (standard extras) - ASGI server, exposed on port 8000

**Testing:**
- None detected — no test framework configured

**Build/Dev:**
- Docker Compose - Multi-service local dev and production deployment (`docker-compose.yml`)
- python-dotenv 1.0.0 - `.env` loading in development

## Key Dependencies

**Critical:**
- `telethon==1.42.0` - Telegram MTProto client; core of all Telegram operations (send, listen, onboard)
- `sqlalchemy==2.0.25` - Async ORM; all DB models and queries (`app/models/__init__.py`, `app/database.py`)
- `asyncpg==0.29.0` - Async PostgreSQL driver used by SQLAlchemy (`postgresql+asyncpg://`)
- `openai>=1.40.0,<2.0.0` - GPT-4o-mini AI responder (`app/services/ai_engine.py`)
- `cryptography==42.0.0` - Fernet symmetric encryption for Telegram session strings (`app/services/encryption.py`)
- `pydantic>=2.8,<3.0` + `pydantic-settings>=2.3,<3.0` - Request/response validation and settings management (`app/config.py`)

**Infrastructure:**
- `PySocks==1.7.1` - SOCKS5 proxy support for Telethon (Decodo proxy pool)
- `python-jose[cryptography]==3.3.0` - JWT library (present but auth is currently API-key-only)
- `httpx==0.26.0` - Async HTTP client for outgoing webhooks and file downloads
- `qrcode[pil]==7.4.2` - QR code generation for Telegram QR login flow (`app/routers/onboarding.py`)
- `email-validator==2.1.0` - Pydantic email validation

## Configuration

**Environment:**
- All configuration via environment variables, loaded through `pydantic-settings` in `app/config.py`
- `.env` file supported for local development (loaded automatically)
- Required variables: `DATABASE_URL`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `ENCRYPTION_KEY`, `OPENAI_API_KEY`, `API_KEY`
- Optional variables: `DECODO_HOST`, `DECODO_USERNAME`, `DECODO_PASSWORD`, `DECODO_PORTS` (proxy pool)

**Build:**
- `Dockerfile` - API service (runs `uvicorn app.main:app`)
- `Dockerfile.listener` - Listener service (runs `python -m app.services.listener`)
- `docker-compose.yml` - Orchestrates `db`, `api`, `listener` services

## Platform Requirements

**Development:**
- Docker + Docker Compose (primary dev environment)
- Python 3.11+ if running outside Docker
- PostgreSQL 16 (provided via Docker service `db`)

**Production:**
- DigitalOcean VPS — deployed as Docker Compose stack
- Deploy path: `/root/apps/outreach-platform/`
- Deploy commands: `git pull && docker compose up -d --build api` / `--build listener`
- No CI/CD pipeline — manual deploy via SSH

---

*Stack analysis: 2026-04-02*
*Update after major dependency changes*
