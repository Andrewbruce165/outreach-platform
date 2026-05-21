"""Phase 02.1 (CR-07): public /api/v1/health не раскрывает sender-counts.

Гарантии:
- Anonymous GET /api/v1/health → 200, payload содержит только {status, database, version, uptime_seconds}.
- Ключ 'senders' отсутствует в response (information disclosure across tenants).
- SELECT по таблице senders НЕ выполняется при публичном вызове.
"""

from unittest.mock import patch

import pytest

pytestmark = pytest.mark.asyncio


async def test_public_health_no_senders_field(async_client):
    """CR-07: GET /api/v1/health без auth — НЕ должно быть ключа 'senders'."""
    resp = await async_client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert "senders" not in body, f"CR-07 regression! senders leaked: {body}"
    # Sanity: тех-статус полный
    assert set(body.keys()) >= {"status", "database", "version", "uptime_seconds"}
    assert body["status"] in {"healthy", "unhealthy"}
    assert body["database"] in {"connected", "disconnected"}
    assert isinstance(body["uptime_seconds"], int)
    assert isinstance(body["version"], str)


async def test_health_no_sender_table_scan(async_client, monkeypatch):
    """CR-07: при GET /health НЕ должен выполняться SELECT по таблице senders.

    Мониторим SQL через monkeypatch на AsyncSession.execute — фиксируем все
    переданные statements; ни один не должен ссылаться на таблицу senders.
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    executed_sql: list[str] = []
    original_execute = AsyncSession.execute

    async def spy_execute(self, statement, *args, **kwargs):
        try:
            executed_sql.append(
                str(statement.compile(compile_kwargs={"literal_binds": True}))
            )
        except Exception:
            executed_sql.append(str(statement))
        return await original_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "execute", spy_execute)

    resp = await async_client.get("/api/v1/health")
    assert resp.status_code == 200

    senders_queries = [
        q for q in executed_sql if "senders" in q.lower() or "sender " in q.lower()
    ]
    assert len(senders_queries) == 0, (
        f"CR-07 regression: SELECT involving senders performed in public /health: "
        f"{senders_queries}"
    )


async def test_health_does_not_require_auth(async_client):
    """Health endpoint остаётся публичным (для load balancer / monitoring)."""
    # Без любых заголовков
    resp = await async_client.get("/api/v1/health")
    assert resp.status_code == 200
