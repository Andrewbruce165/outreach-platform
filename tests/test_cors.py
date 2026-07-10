"""CORS preflight tests — Phase 1 D-14 explicit-allowlist lockdown.

Notes:
  - pytest-asyncio auto-mode is enabled (pyproject.toml: asyncio_mode = "auto").
    No per-test decorators required.
  - Uses in-process ASGITransport — no real network or server needed.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def cors_client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_options_preflight_from_unrelated_origin_rejected(cors_client):
    """Unrelated origin does NOT receive a CORS allow header.

    Starlette CORSMiddleware either returns 400 for un-allowed origins on a
    real preflight or simply omits the Access-Control-Allow-Origin header.
    Either way: assert no allow-origin echo for the evil origin.
    """
    resp = await cors_client.options(
        "/api/v1/auth/me",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp.headers.get("access-control-allow-origin") != "https://evil.example.com"


async def test_options_preflight_from_explicit_allowlist_still_works(cors_client):
    """Default settings.cors_allowed_origins = "http://localhost:5173" (see app/config.py)
    is honored by CORSMiddleware."""
    resp = await cors_client.options(
        "/api/v1/auth/me",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp.status_code in (200, 204)
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"
