from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging

from app.config import get_settings
from app.database import init_db, engine
from app.services.telegram import telegram_service  # noqa: F401  (kept for startup-side warmup of module)
from app.services.queue import queue_worker, recover_stuck_jobs
from app.services.warmup import warmup_worker
from app.services.onboarding_state import onboarding_cleanup_worker
from app.routers import folders, health, onboarding, senders, workspace

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

    yield

    # Shutdown
    logger.info("Shutting down...")
    await onboarding_cleanup_worker.stop()
    await queue_worker.stop()
    await warmup_worker.stop()
    await engine.dispose()
    logger.info("Shutdown complete")


app = FastAPI(
    title="Outreach Platform API",
    description="Multi-tenant Telegram outreach SaaS (Phase 1: workspace foundation)",
    version="2.0.0-phase1",
    lifespan=lifespan
)

# CORS middleware — Phase 1 lockdown (D-14): explicit origins, no wildcard.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "HEAD", "POST", "PATCH", "DELETE", "OPTIONS"],  # W-2: HEAD для healthcheck preflight
    allow_headers=["Authorization", "X-Workspace-Key", "Content-Type"],
)

# Include routers.
#   Phase 1: health, workspace (D-14 lockdown)
#   Phase 2: senders, folders (workspace-scoped, replaces legacy routers)
app.include_router(health.router)
app.include_router(workspace.router)
app.include_router(senders.router)
app.include_router(folders.router)
app.include_router(onboarding.router)


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "Outreach Platform API",
        "version": "2.0.0-phase1",
        "docs": "/docs",
        "health": "/api/v1/health",
        "endpoints": {
            "auth_me": "POST /api/v1/auth/me",
            "workspace": "/api/v1/workspace",
            "api_keys": "/api/v1/workspace/api-keys",
        }
    }
