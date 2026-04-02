"""
Warmup Router
CRUD для управления пулом прогрева аккаунтов + статистика.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.routers.auth import verify_api_key
from app.services.warmup import LEVEL_CONFIG

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/warmup", tags=["warmup"])


def _get_level(enrolled_days: int) -> int:
    """Уровень прогрева по дням."""
    for i, (start, end, *_) in enumerate(LEVEL_CONFIG, 1):
        if start <= enrolled_days < end:
            return i
    return 5


# ─── Pool management ──────────────────────────────────────────────────────────

@router.get("/pool")
async def list_pool(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    """
    Все sender'ы с их статусом в пуле прогрева.
    Возвращает как участников пула, так и тех, кто в него не добавлен.
    """
    result = await db.execute(text("""
        SELECT
            s.id,
            s.slug,
            s.name,
            s.phone,
            s.is_active,
            wp.id           AS pool_id,
            wp.is_active    AS warmup_active,
            wp.enrolled_at,
            COALESCE(
                EXTRACT(DAY FROM (NOW() - wp.enrolled_at))::int, 0
            )               AS enrolled_days,
            (
                SELECT COUNT(*) FROM warmup_messages wm
                WHERE wm.from_sender_id = s.id
                  AND wm.sent_at >= CURRENT_DATE
            )               AS sent_today
        FROM senders s
        LEFT JOIN warmup_pool wp ON wp.sender_id = s.id
        WHERE s.role = 'sender'
        ORDER BY s.name
    """))
    rows = result.fetchall()

    return {
        "senders": [
            {
                "id":             str(r[0]),
                "slug":           r[1],
                "name":           r[2],
                "phone":          r[3],
                "is_active":      r[4],
                "in_pool":        r[5] is not None,
                "warmup_active":  bool(r[6]) if r[6] is not None else False,
                "enrolled_at":    r[7].isoformat() if r[7] else None,
                "enrolled_days":  int(r[8]),
                "level":          _get_level(int(r[8])) if r[5] is not None else None,
                "sent_today":     int(r[9]),
            }
            for r in rows
        ]
    }


@router.post("/pool/{sender_id}", status_code=201)
async def add_to_pool(
    sender_id: str,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    """Добавить аккаунт в пул прогрева."""
    # Проверяем что sender существует и это role='sender'
    result = await db.execute(
        text("SELECT id, slug, role FROM senders WHERE id = :sid"),
        {"sid": sender_id}
    )
    sender = result.fetchone()
    if not sender:
        raise HTTPException(status_code=404, detail="Sender не найден")
    if sender[2] != "sender":
        raise HTTPException(
            status_code=400,
            detail="Только аккаунты с role='sender' можно добавлять в пул прогрева"
        )

    # Upsert в warmup_pool
    await db.execute(
        text("""
            INSERT INTO warmup_pool (sender_id, is_active)
            VALUES (:sid, true)
            ON CONFLICT (sender_id) DO UPDATE
                SET is_active = true,
                    enrolled_at = CASE
                        WHEN warmup_pool.is_active = false THEN NOW()
                        ELSE warmup_pool.enrolled_at
                    END
        """),
        {"sid": sender_id}
    )
    await db.commit()

    logger.info(f"🔥 Warmup: {sender[1]} добавлен в пул прогрева")
    return {"status": "added", "sender_id": sender_id, "slug": sender[1]}


@router.delete("/pool/{sender_id}", status_code=204)
async def remove_from_pool(
    sender_id: str,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    """Удалить аккаунт из пула прогрева и завершить его активные сессии."""
    result = await db.execute(
        text("SELECT id FROM warmup_pool WHERE sender_id = :sid"),
        {"sid": sender_id}
    )
    if not result.fetchone():
        raise HTTPException(status_code=404, detail="Аккаунт не в пуле прогрева")

    # Завершаем активные сессии этого аккаунта
    await db.execute(
        text("""
            UPDATE warmup_sessions
            SET status = 'completed', updated_at = NOW()
            WHERE status = 'active'
              AND (sender_a_id = :sid OR sender_b_id = :sid)
        """),
        {"sid": sender_id}
    )
    await db.execute(
        text("DELETE FROM warmup_pool WHERE sender_id = :sid"),
        {"sid": sender_id}
    )
    await db.commit()
    logger.info(f"🔥 Warmup: {sender_id} удалён из пула прогрева")


@router.patch("/pool/{sender_id}/toggle")
async def toggle_pool_member(
    sender_id: str,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    """Включить / выключить участие аккаунта в прогреве (без удаления из пула)."""
    result = await db.execute(
        text("""
            UPDATE warmup_pool
            SET is_active = NOT is_active, enrolled_at = CASE
                    WHEN is_active = false THEN NOW()   -- перезапускаем отсчёт при включении
                    ELSE enrolled_at
                END
            WHERE sender_id = :sid
            RETURNING is_active
        """),
        {"sid": sender_id}
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Аккаунт не в пуле прогрева")

    await db.commit()
    state = "включён" if row[0] else "приостановлен"
    logger.info(f"🔥 Warmup: {sender_id} {state}")
    return {"sender_id": sender_id, "warmup_active": row[0]}


# ─── Stats ────────────────────────────────────────────────────────────────────

@router.get("/stats")
async def get_stats(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    """Сводная статистика прогрева: сообщения сегодня, активные сессии, аккаунты в пуле."""
    totals = await db.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM warmup_pool WHERE is_active = true)      AS active_accounts,
            (SELECT COUNT(*) FROM warmup_sessions WHERE status = 'active')  AS active_sessions,
            (SELECT COUNT(*) FROM warmup_messages WHERE sent_at >= CURRENT_DATE) AS messages_today,
            (SELECT COUNT(*) FROM warmup_sessions WHERE status = 'completed'
               AND updated_at >= CURRENT_DATE)                             AS sessions_completed_today
    """))
    row = totals.fetchone()

    # По аккаунтам — сколько каждый отправил сегодня
    per_account = await db.execute(text("""
        SELECT s.slug, s.name,
               COUNT(wm.id) AS sent_today,
               EXTRACT(DAY FROM (NOW() - wp.enrolled_at))::int AS enrolled_days
        FROM warmup_pool wp
        JOIN senders s ON s.id = wp.sender_id
        LEFT JOIN warmup_messages wm
               ON wm.from_sender_id = wp.sender_id
              AND wm.sent_at >= CURRENT_DATE
        WHERE wp.is_active = true
        GROUP BY s.slug, s.name, wp.enrolled_at
        ORDER BY s.name
    """))

    return {
        "active_accounts":          int(row[0]),
        "active_sessions":          int(row[1]),
        "messages_today":           int(row[2]),
        "sessions_completed_today": int(row[3]),
        "accounts": [
            {
                "slug":          r[0],
                "name":          r[1],
                "sent_today":    int(r[2]),
                "enrolled_days": int(r[3]),
                "level":         _get_level(int(r[3])),
            }
            for r in per_account.fetchall()
        ],
    }


@router.get("/sessions")
async def list_sessions(
    status: Optional[str] = Query("active", description="Filter: active | completed | all"),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    """Warmup-сессии с прогрессом. По умолчанию — активные."""
    valid = {"active", "completed", "all"}
    if status not in valid:
        raise HTTPException(status_code=400, detail=f"status must be one of: {', '.join(sorted(valid))}")

    if status == "all":
        where_clause = ""
        order_clause = "ORDER BY ws.created_at DESC"
    elif status == "completed":
        where_clause = "WHERE ws.status = 'completed'"
        order_clause = "ORDER BY ws.updated_at DESC"
    else:
        where_clause = "WHERE ws.status = 'active'"
        order_clause = "ORDER BY ws.next_message_at"

    result = await db.execute(text(f"""
        SELECT
            ws.id,
            ws.topic,
            ws.status,
            ws.messages_sent,
            ws.target_messages,
            ws.next_message_at,
            ws.created_at,
            ws.updated_at,
            sa.slug AS slug_a,
            sb.slug AS slug_b
        FROM warmup_sessions ws
        JOIN senders sa ON sa.id = ws.sender_a_id
        JOIN senders sb ON sb.id = ws.sender_b_id
        {where_clause}
        {order_clause}
    """))

    return {
        "sessions": [
            {
                "id":              str(r[0]),
                "topic":           r[1],
                "status":          r[2],
                "messages_sent":   r[3],
                "target_messages": r[4],
                "next_message_at": r[5].isoformat() if r[5] else None,
                "created_at":      r[6].isoformat() if r[6] else None,
                "updated_at":      r[7].isoformat() if r[7] else None,
                "sender_a":        r[8],
                "sender_b":        r[9],
                "progress_pct":    round(r[3] / r[4] * 100) if r[4] else 0,
            }
            for r in result.fetchall()
        ]
    }


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    """Детали одной warmup-сессии."""
    result = await db.execute(
        text("""
            SELECT
                ws.id, ws.topic, ws.status,
                ws.messages_sent, ws.target_messages,
                ws.next_message_at, ws.created_at, ws.updated_at,
                sa.slug AS slug_a, sa.name AS name_a,
                sb.slug AS slug_b, sb.name AS name_b
            FROM warmup_sessions ws
            JOIN senders sa ON sa.id = ws.sender_a_id
            JOIN senders sb ON sb.id = ws.sender_b_id
            WHERE ws.id = :sid
        """),
        {"sid": session_id},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Сессия не найдена")

    return {
        "id":              str(row[0]),
        "topic":           row[1],
        "status":          row[2],
        "messages_sent":   row[3],
        "target_messages": row[4],
        "progress_pct":    round(row[3] / row[4] * 100) if row[4] else 0,
        "next_message_at": row[5].isoformat() if row[5] else None,
        "created_at":      row[6].isoformat() if row[6] else None,
        "updated_at":      row[7].isoformat() if row[7] else None,
        "sender_a": {"slug": row[8], "name": row[9]},
        "sender_b": {"slug": row[10], "name": row[11]},
    }


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    limit: int = Query(100, le=200),
    offset: int = Query(0),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    """История сообщений warmup-сессии (для отображения диалога)."""
    # Проверяем что сессия существует
    check = await db.execute(
        text("SELECT sender_a_id FROM warmup_sessions WHERE id = :sid"),
        {"sid": session_id},
    )
    row = check.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Сессия не найдена")

    sender_a_id = str(row[0])

    total_row = await db.execute(
        text("SELECT COUNT(*) FROM warmup_messages WHERE session_id = :sid"),
        {"sid": session_id},
    )
    total = total_row.scalar() or 0

    result = await db.execute(
        text("""
            SELECT
                wm.id,
                wm.message_text,
                wm.sent_at,
                s.slug  AS from_slug,
                s.name  AS from_name,
                wm.from_sender_id
            FROM warmup_messages wm
            JOIN senders s ON s.id = wm.from_sender_id
            WHERE wm.session_id = :sid
            ORDER BY wm.sent_at ASC
            LIMIT :limit OFFSET :offset
        """),
        {"sid": session_id, "limit": limit, "offset": offset},
    )

    messages = [
        {
            "id":          str(r[0]),
            "text":        r[1],
            "sent_at":     r[2].isoformat() if r[2] else None,
            "from_slug":   r[3],
            "from_name":   r[4],
            # direction: "a" — sender_a отправил, "b" — sender_b
            "direction":   "a" if str(r[5]) == sender_a_id else "b",
        }
        for r in result.fetchall()
    ]

    return {"total": total, "messages": messages}
