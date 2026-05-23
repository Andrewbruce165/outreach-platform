"""CORS preflight tests for Phase 05.1 — Lovable preview subdomain support (UI-CORS).

References:
  - RESEARCH §"Common Pitfalls" Pitfall 7 — allow_origin_regex required
    (Starlette allow_origins wildcards do not work as a substring match).
  - app/main.py CORSMiddleware widened in 05.1-02.
  - app/config.py cors_allowed_origin_regex setting added in 05.1-02.

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
    """In-process httpx client with ASGI transport — Phase 05.1-02 CORS tests."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_options_preflight_from_lovable_subdomain_allowed(cors_client):
    """Lovable preview origin https://abc-123.lovableproject.com is accepted."""
    resp = await cors_client.options(
        "/api/v1/auth/me",
        headers={
            "Origin": "https://abc-123.lovableproject.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert resp.status_code in (200, 204)
    assert resp.headers.get("access-control-allow-origin") == "https://abc-123.lovableproject.com"


async def test_options_preflight_from_other_lovable_subdomain_allowed(cors_client):
    """Different Lovable subdomain pattern also accepted."""
    resp = await cors_client.options(
        "/api/v1/auth/me",
        headers={
            "Origin": "https://my-app-xyz-9.lovableproject.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp.status_code in (200, 204)
    assert resp.headers.get("access-control-allow-origin") == "https://my-app-xyz-9.lovableproject.com"


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


async def test_options_preflight_from_bare_lovableproject_no_subdomain_rejected(cors_client):
    """Origin https://lovableproject.com (no subdomain) MUST NOT match.

    The regex requires a `[a-z0-9-]+\\.` subdomain prefix; the bare domain
    must not satisfy it.
    """
    resp = await cors_client.options(
        "/api/v1/auth/me",
        headers={
            "Origin": "https://lovableproject.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp.headers.get("access-control-allow-origin") != "https://lovableproject.com"


async def test_options_preflight_from_explicit_allowlist_still_works(cors_client):
    """Phase 1 D-14 regression — explicit origin from CORS_ALLOWED_ORIGINS still works.

    Default settings.cors_allowed_origins = "http://localhost:5173" (see app/config.py
    line 25). Widening via allow_origin_regex must NOT break the explicit allowlist.
    """
    resp = await cors_client.options(
        "/api/v1/auth/me",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp.status_code in (200, 204)
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"
