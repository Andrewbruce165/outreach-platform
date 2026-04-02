from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging

from app.config import get_settings
from app.database import init_db, engine
from app.services.telegram import telegram_service
from app.services.queue import queue_worker, recover_stuck_jobs
from app.services.warmup import warmup_worker
from app.routers import send, senders, health, conversations, contexts, onboarding, check_contacts
from app.routers import queue as queue_router
from app.routers import warmup as warmup_router
from app.routers import proxy_pool as proxy_pool_router

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
    logger.info("Starting Telegram Followup API...")
    await init_db()
    logger.info("Database initialized")
    await recover_stuck_jobs()
    queue_worker.start()
    logger.info("Queue worker started")
    warmup_worker.start()
    logger.info("Warmup worker started")

    yield

    # Shutdown
    logger.info("Shutting down...")
    await queue_worker.stop()
    await warmup_worker.stop()
    await engine.dispose()
    logger.info("Shutdown complete")


app = FastAPI(
    title="Telegram Followup API",
    description="API для отправки follow-up сообщений в Telegram и AI-ассистент",
    version="1.1.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(send.router)
app.include_router(senders.router)
app.include_router(health.router)
app.include_router(conversations.router)
app.include_router(contexts.router)
app.include_router(onboarding.router)
app.include_router(queue_router.router)
app.include_router(check_contacts.router)
app.include_router(warmup_router.router)
app.include_router(proxy_pool_router.router)


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "Telegram Followup API",
        "version": "1.1.0",
        "docs": "/docs",
        "health": "/api/v1/health",
        "endpoints": {
            "send": "/api/v1/send",
            "senders": "/api/v1/senders",
            "conversations": "/api/v1/conversations",
            "contexts": "/api/v1/contexts"
        }
    }
