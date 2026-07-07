from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from contextlib import asynccontextmanager
import logging
import re

from app.config import get_settings
from app.database import init_db, engine
from app.services.telegram import telegram_service  # noqa: F401  (kept for startup-side warmup of module)
from app.services.queue import queue_worker, recover_stuck_jobs
from app.services.warmup import warmup_worker
from app.services.onboarding_state import onboarding_cleanup_worker
from app.services.contact_check_worker import contact_check_worker
from app.services.campaign_enqueue import campaign_enqueue_worker  # Phase 4 D-17
from app.services.kb_ingest_worker import kb_ingest_worker  # Phase 16 — KB ingest pipeline
from app.services.follow_up import follow_up_worker  # Phase 19 — no-reply follow-up + auto-finish
from app.routers import (
    account_import,  # Phase 21 — bulk Telegram account import
    agents,
    analytics,  # Phase 5 — new (4 read-only endpoints)
    campaigns,
    check_contacts,
    contacts,
    conversations,  # Phase 5 — re-register (was legacy, not previously wired)
    folders,
    health,
    knowledge_bases,  # Phase 16 — RAG knowledge bases
    llm_settings,  # Phase 18 — switchable LLM provider settings
    onboarding,
    send,
    senders,
    telemetry,  # Phase 05.1 — UI-SPEC §9 Core Value KPI ingest
    warmup,  # Phase 15 — workspace-scoped warmup tab (D-05)
    workspace,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    # Startup
    logger.info("Starting Outreach Platform API...")
    await init_db()
    logger.info("Database initialized")
    await recover_stuck_jobs()
    queue_worker.start()
    logger.info("Queue worker started")
    warmup_worker.start()
    logger.info("Warmup worker started")
    onboarding_cleanup_worker.start()
    logger.info("Onboarding cleanup worker started")
    contact_check_worker.start()
    logger.info("Contact check worker started")
    campaign_enqueue_worker.start()  # Phase 4 D-17
    logger.info("Campaign enqueue worker started")
    kb_ingest_worker.start()  # Phase 16 — KB ingest pipeline
    logger.info("Knowledge ingest worker started")
    follow_up_worker.start()  # Phase 19 — no-reply follow-up + auto-finish
    logger.info("Follow-up worker started")

    yield

    # Shutdown
    logger.info("Shutting down...")
    await follow_up_worker.stop()  # Phase 19 — no-reply follow-up + auto-finish
    await kb_ingest_worker.stop()  # Phase 16 — KB ingest pipeline
    await campaign_enqueue_worker.stop()  # Phase 4 D-17
    await contact_check_worker.stop()
    await onboarding_cleanup_worker.stop()
    await queue_worker.stop()
    await warmup_worker.stop()
    await engine.dispose()
    logger.info("Shutdown complete")


app = FastAPI(
    title="Outreach Platform API",
    description="Multi-tenant Telegram outreach SaaS (Phase 3: agents + send)",
    version="2.0.0-phase3",
    lifespan=lifespan
)

# CORS middleware — Phase 1 D-14 lockdown preserved (explicit allowlist) +
# Phase 05.1 widening: allow_origin_regex for Lovable preview deployments
# (Pitfall 7 — Starlette allow_origins does NOT honor wildcards; regex is
# the only safe path for auto-generated subdomains).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_origin_regex=settings.cors_allowed_origin_regex,
    allow_credentials=True,
    allow_methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],  # W-2: HEAD для healthcheck preflight; PUT — warmup/settings (единственный PUT-роут)
    allow_headers=["Authorization", "X-Workspace-Key", "Content-Type"],
)


# Defense-in-depth CORS for error responses (Phase 05.1-DEBUG
# agents-500-cors + contacts-import-cors-400). Browsers strip the body of any
# response missing Access-Control-Allow-Origin, so a 4xx/5xx that escapes
# CORSMiddleware surfaces on the frontend as a misleading "blocked by CORS"
# error and the real failure (e.g. multipart parse error, raw-SQL crash) is
# invisible. We explicitly echo the request Origin on every error path here,
# matching settings.cors_origins_list / cors_allowed_origin_regex so we don't
# widen the policy beyond what CORSMiddleware itself would allow.


def _allowed_origin(request: Request) -> str | None:
    origin = request.headers.get("origin")
    if not origin:
        return None
    if origin in settings.cors_origins_list:
        return origin
    pattern = settings.cors_allowed_origin_regex
    if pattern and re.fullmatch(pattern, origin):
        return origin
    return None


def _cors_headers(request: Request) -> dict[str, str]:
    origin = _allowed_origin(request)
    if not origin:
        return {}
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
        "Vary": "Origin",
    }


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.warning(
        "Validation error on %s %s: %s",
        request.method, request.url.path, exc.errors(),
    )
    return JSONResponse(
        status_code=422,
        content={
            "detail": {
                "code": "VALIDATION_ERROR",
                "message": "Request validation failed",
                # jsonable_encoder: validator-raised ValueError lands in ctx["error"] as a
                # raw exception object — serialize it (→ str) so JSONResponse doesn't crash
                # and fall through to the 500 handler (would return 500 instead of 422).
                "errors": jsonable_encoder(exc.errors()),
            }
        },
        headers=_cors_headers(request),
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    # Preserve FastAPI/Starlette HTTPException semantics but guarantee CORS
    # headers on the response.
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers={**(exc.headers or {}), **_cors_headers(request)},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error", "code": "INTERNAL_ERROR"},
        headers=_cors_headers(request),
    )


# Include routers.
#   Phase 1: health, workspace (D-14 lockdown)
#   Phase 2: senders, folders (workspace-scoped, replaces legacy routers)
#   Phase 3: agents (CRUD AI templates), send (rewrite under AuthDep)
app.include_router(health.router)
app.include_router(workspace.router)
app.include_router(senders.router)
app.include_router(folders.router)
app.include_router(contacts.router)
app.include_router(check_contacts.router)
app.include_router(onboarding.router)
app.include_router(agents.router)
app.include_router(send.router)
app.include_router(campaigns.router)  # Phase 4
app.include_router(conversations.router)  # Phase 5 — inbox + manager mode
app.include_router(analytics.router)  # Phase 5 — analytics (4 endpoints) + 05.1 funnel + llm
app.include_router(telemetry.router)  # Phase 05.1 — UI-SPEC §9 telemetry + core-value KPI
app.include_router(warmup.router)  # Phase 15 — warmup tab (workspace-scoped, D-05)
app.include_router(knowledge_bases.router)  # Phase 16 — RAG knowledge bases
app.include_router(llm_settings.router)  # Phase 18 — switchable LLM provider settings
app.include_router(account_import.router)  # Phase 21 — bulk account import


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "Outreach Platform API",
        "version": "2.0.0-phase4",
        "docs": "/docs",
        "health": "/api/v1/health",
        "endpoints": {
            "auth_me": "POST /api/v1/auth/me",
            "workspace": "/api/v1/workspace",
            "api_keys": "/api/v1/workspace/api-keys",
            "agents": "/api/v1/agents",  # Phase 3
            "send": "POST /api/v1/send",  # Phase 3
            "campaigns": "/api/v1/campaigns",  # Phase 4
        }
    }
