"""
Warmup Router (Phase 15 — WARM-05..11, D-05/D-06/D-07/D-10/D-11).

Все endpoint'ы workspace-scoped через `Depends(auth_dep)` (Phase 1 D-12 AuthCtx) —
рерайт с legacy `verify_api_key` (D-05). Каждый запрос фильтруется по
`workspace_id` из токена; sender/сессия чужого workspace невидимы.

Endpoints:
  GET    /api/v1/warmup/pool                       — sender'ы workspace + статус в пуле (+ D-11 restriction)
  POST   /api/v1/warmup/pool/{sender_id}           — добавить в пул (workspace-owned 404)
  DELETE /api/v1/warmup/pool/{sender_id}           — убрать из пула (workspace-owned 404)
  PATCH  /api/v1/warmup/pool/{sender_id}/toggle    — вкл/выкл участие (workspace-owned 404)
  GET    /api/v1/warmup/stats                       — сводная статистика workspace
  GET    /api/v1/warmup/sessions                    — warmup-сессии workspace
  GET    /api/v1/warmup/sessions/{id}               — детали сессии (workspace-owned 404)
  GET    /api/v1/warmup/sessions/{id}/messages      — история сообщений (workspace-owned 404)
  GET    /api/v1/warmup/settings                    — master toggle + контент (resolved defaults, D-06/D-10)
  PUT    /api/v1/warmup/settings                    — upsert настроек (master toggle + контент)

Phase 15 ключевые изменения относительно legacy:
- `verify_api_key` → `Depends(auth_dep)` + `WHERE workspace_id = :wid` (D-05).
- GET /pool больше НЕ ссылается на дропнутую `senders.is_active` (миграция 013);
  `wp.is_active` (per-account enroll toggle) сохраняется как `warmup_active`.
- D-11: per-account `restriction_status` / `restricted_until` + derived `warmup_reason`
  (почему аккаунт не греется) — БЕЗ новой error-колонки, derive по locked-решению.
- GET/PUT /settings — master toggle (D-06) + per-workspace контент (D-10) с
  resolved defaults (пустые topics → WARMUP_TOPICS, NULL prompt → WARMUP_SYSTEM_PROMPT).
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.warmup import LEVEL_CONFIG, WARMUP_SYSTEM_PROMPT, WARMUP_TOPICS
from app.utils.auth import AuthCtx, auth_dep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/warmup", tags=["warmup"])


def _get_level(enrolled_days: int) -> int:
    """Уровень прогрева по дням."""
    for i, (start, end, *_) in enumerate(LEVEL_CONFIG, 1):
        if start <= enrolled_days < end:
            return i
    return 5


def _derive_warmup_reason(
    restriction_status: Optional[str],
    restricted_until,  # datetime | None
) -> Optional[str]:
    """D-11 (LOCKED): derive «почему аккаунт не греется» из restriction-полей.

    НЕТ новой error-колонки и НЕТ изменений `_send_via_telethon` — причина
    вычисляется здесь, в ответе /pool, по `senders.restriction_status` /
    `restricted_until`. Возвращает человекочитаемый текст (RU) или None, если
    аккаунт не под ограничением.
    """
    if restriction_status == "frozen":
        return "Аккаунт заморожен Telegram — прогрев приостановлен"
    if restriction_status == "spam_limited":
        if restricted_until is not None:
            return (
                "Аккаунт под спам-ограничением — прогрев возобновится после "
                f"{restricted_until.isoformat()}"
            )
        return "Аккаунт под спам-ограничением — прогрев приостановлен"
    if restriction_status and restriction_status != "none":
        return f"Аккаунт ограничен ({restriction_status}) — прогрев приостановлен"
    return None


# ─── Pool management ──────────────────────────────────────────────────────────

@router.get("/pool")
async def list_pool(
    db: AsyncSession = Depends(get_db),
    ctx: AuthCtx = Depends(auth_dep),
):
    """
    Все sender'ы workspace с их статусом в пуле прогрева.
    Возвращает как участников пула, так и тех, кто в него не добавлен.

    D-11: каждый аккаунт несёт restriction_status / restricted_until +
    derived warmup_reason (почему аккаунт не греется).
    """
    result = await db.execute(text("""
        SELECT
            s.id,
            s.slug,
            s.name,
            s.phone,
            wp.id              AS pool_id,
            wp.is_active       AS warmup_active,
            wp.enrolled_at,
            COALESCE(
                EXTRACT(DAY FROM (NOW() - wp.enrolled_at))::int, 0
            )                  AS enrolled_days,
            (
                SELECT COUNT(*) FROM warmup_messages wm
                WHERE wm.from_sender_id = s.id
                  AND wm.workspace_id = :wid
                  AND wm.sent_at >= CURRENT_DATE
            )                  AS sent_today,
            s.restriction_status,
            s.restricted_until
        FROM senders s
        LEFT JOIN warmup_pool wp
               ON wp.sender_id = s.id
              AND wp.workspace_id = :wid
        WHERE s.role = 'sender'
          AND s.workspace_id = :wid
        ORDER BY s.name
    """), {"wid": str(ctx.workspace_id)})
    rows = result.fetchall()

    return {
        "senders": [
            {
                "id":                 str(r[0]),
                "slug":               r[1],
                "name":               r[2],
                "phone":              r[3],
                "in_pool":            r[4] is not None,
                "warmup_active":      bool(r[5]) if r[5] is not None else False,
                "enrolled_at":        r[6].isoformat() if r[6] else None,
                "enrolled_days":      int(r[7]),
                "level":              _get_level(int(r[7])) if r[4] is not None else None,
                "sent_today":         int(r[8]),
                # D-11 additions:
                "restriction_status": r[9],
                "restricted_until":   r[10].isoformat() if r[10] else None,
                "warmup_reason":      _derive_warmup_reason(r[9], r[10]),
            }
            for r in rows
        ]
    }


async def _assert_workspace_owns_sender(
    db: AsyncSession, sender_id: str, workspace_id: str
) -> tuple[str, str]:
    """Проверить что sender принадлежит workspace. 404 если нет.

    Mirror of senders.py _validate_workspace_owns_* — закрывает cross-workspace
    мутации (WARM-05). Возвращает (id, slug, role).
    """
    result = await db.execute(
        text("""
            SELECT id, slug, role
            FROM senders
            WHERE id = :sid AND workspace_id = :wid
        """),
        {"sid": sender_id, "wid": workspace_id},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Sender не найден")
    return str(row[0]), row[1], row[2]


@router.post("/pool/{sender_id}", status_code=201)
async def add_to_pool(
    sender_id: str,
    db: AsyncSession = Depends(get_db),
    ctx: AuthCtx = Depends(auth_dep),
):
    """Добавить аккаунт в пул прогрева (только аккаунт текущего workspace)."""
    wid = str(ctx.workspace_id)
    _id, slug, role = await _assert_workspace_owns_sender(db, sender_id, wid)
    if role != "sender":
        raise HTTPException(
            status_code=400,
            detail="Только аккаунты с role='sender' можно добавлять в пул прогрева",
        )

    # Upsert в warmup_pool (workspace_id привязан к workspace из токена).
    await db.execute(
        text("""
            INSERT INTO warmup_pool (sender_id, workspace_id, is_active)
            VALUES (:sid, :wid, true)
            ON CONFLICT (sender_id) DO UPDATE
                SET is_active = true,
                    enrolled_at = CASE
                        WHEN warmup_pool.is_active = false THEN NOW()
                        ELSE warmup_pool.enrolled_at
                    END
        """),
        {"sid": sender_id, "wid": wid},
    )
    await db.commit()

    logger.info(f"🔥 Warmup: {slug} добавлен в пул прогрева (ws={wid[:8]})")
    return {"status": "added", "sender_id": sender_id, "slug": slug}


@router.delete("/pool/{sender_id}", status_code=204)
async def remove_from_pool(
    sender_id: str,
    db: AsyncSession = Depends(get_db),
    ctx: AuthCtx = Depends(auth_dep),
):
    """Удалить аккаунт из пула прогрева и завершить его активные сессии."""
    wid = str(ctx.workspace_id)
    await _assert_workspace_owns_sender(db, sender_id, wid)

    result = await db.execute(
        text("SELECT id FROM warmup_pool WHERE sender_id = :sid AND workspace_id = :wid"),
        {"sid": sender_id, "wid": wid},
    )
    if not result.fetchone():
        raise HTTPException(status_code=404, detail="Аккаунт не в пуле прогрева")

    # Завершаем активные сессии этого аккаунта в пределах workspace.
    await db.execute(
        text("""
            UPDATE warmup_sessions
            SET status = 'completed', updated_at = NOW()
            WHERE status = 'active'
              AND workspace_id = :wid
              AND (sender_a_id = :sid OR sender_b_id = :sid)
        """),
        {"sid": sender_id, "wid": wid},
    )
    await db.execute(
        text("DELETE FROM warmup_pool WHERE sender_id = :sid AND workspace_id = :wid"),
        {"sid": sender_id, "wid": wid},
    )
    await db.commit()
    logger.info(f"🔥 Warmup: {sender_id} удалён из пула прогрева (ws={wid[:8]})")


@router.patch("/pool/{sender_id}/toggle")
async def toggle_pool_member(
    sender_id: str,
    db: AsyncSession = Depends(get_db),
    ctx: AuthCtx = Depends(auth_dep),
):
    """Включить / выключить участие аккаунта в прогреве (без удаления из пула)."""
    wid = str(ctx.workspace_id)
    await _assert_workspace_owns_sender(db, sender_id, wid)

    result = await db.execute(
        text("""
            UPDATE warmup_pool
            SET is_active = NOT is_active, enrolled_at = CASE
                    WHEN is_active = false THEN NOW()   -- перезапускаем отсчёт при включении
                    ELSE enrolled_at
                END
            WHERE sender_id = :sid AND workspace_id = :wid
            RETURNING is_active
        """),
        {"sid": sender_id, "wid": wid},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Аккаунт не в пуле прогрева")

    await db.commit()
    state = "включён" if row[0] else "приостановлен"
    logger.info(f"🔥 Warmup: {sender_id} {state} (ws={wid[:8]})")
    return {"sender_id": sender_id, "warmup_active": row[0]}


# ─── Stats ────────────────────────────────────────────────────────────────────

@router.get("/stats")
async def get_stats(
    db: AsyncSession = Depends(get_db),
    ctx: AuthCtx = Depends(auth_dep),
):
    """Сводная статистика прогрева workspace: сообщения сегодня, активные сессии, аккаунты в пуле."""
    wid = str(ctx.workspace_id)
    totals = await db.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM warmup_pool
              WHERE is_active = true AND workspace_id = :wid)               AS active_accounts,
            (SELECT COUNT(*) FROM warmup_sessions
              WHERE status = 'active' AND workspace_id = :wid)              AS active_sessions,
            (SELECT COUNT(*) FROM warmup_messages
              WHERE sent_at >= CURRENT_DATE AND workspace_id = :wid)        AS messages_today,
            (SELECT COUNT(*) FROM warmup_sessions
              WHERE status = 'completed' AND updated_at >= CURRENT_DATE
                AND workspace_id = :wid)                                    AS sessions_completed_today
    """), {"wid": wid})
    row = totals.fetchone()

    # По аккаунтам — сколько каждый отправил сегодня (только workspace).
    per_account = await db.execute(text("""
        SELECT s.slug, s.name,
               COUNT(wm.id) AS sent_today,
               EXTRACT(DAY FROM (NOW() - wp.enrolled_at))::int AS enrolled_days
        FROM warmup_pool wp
        JOIN senders s ON s.id = wp.sender_id
        LEFT JOIN warmup_messages wm
               ON wm.from_sender_id = wp.sender_id
              AND wm.workspace_id = :wid
              AND wm.sent_at >= CURRENT_DATE
        WHERE wp.is_active = true
          AND wp.workspace_id = :wid
        GROUP BY s.slug, s.name, wp.enrolled_at
        ORDER BY s.name
    """), {"wid": wid})

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
    ctx: AuthCtx = Depends(auth_dep),
):
    """Warmup-сессии workspace с прогрессом. По умолчанию — активные."""
    wid = str(ctx.workspace_id)
    valid = {"active", "completed", "all"}
    if status not in valid:
        raise HTTPException(status_code=400, detail=f"status must be one of: {', '.join(sorted(valid))}")

    if status == "all":
        where_clause = "WHERE ws.workspace_id = :wid"
        order_clause = "ORDER BY ws.created_at DESC"
    elif status == "completed":
        where_clause = "WHERE ws.status = 'completed' AND ws.workspace_id = :wid"
        order_clause = "ORDER BY ws.updated_at DESC"
    else:
        where_clause = "WHERE ws.status = 'active' AND ws.workspace_id = :wid"
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
    """), {"wid": wid})

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
    ctx: AuthCtx = Depends(auth_dep),
):
    """Детали одной warmup-сессии (только текущего workspace)."""
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
            WHERE ws.id = :sid AND ws.workspace_id = :wid
        """),
        {"sid": session_id, "wid": str(ctx.workspace_id)},
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
    ctx: AuthCtx = Depends(auth_dep),
):
    """История сообщений warmup-сессии workspace (для отображения диалога)."""
    wid = str(ctx.workspace_id)
    # Проверяем что сессия существует и принадлежит workspace.
    check = await db.execute(
        text("SELECT sender_a_id FROM warmup_sessions WHERE id = :sid AND workspace_id = :wid"),
        {"sid": session_id, "wid": wid},
    )
    row = check.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Сессия не найдена")

    sender_a_id = str(row[0])

    total_row = await db.execute(
        text("SELECT COUNT(*) FROM warmup_messages WHERE session_id = :sid AND workspace_id = :wid"),
        {"sid": session_id, "wid": wid},
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
            WHERE wm.session_id = :sid AND wm.workspace_id = :wid
            ORDER BY wm.sent_at ASC
            LIMIT :limit OFFSET :offset
        """),
        {"sid": session_id, "wid": wid, "limit": limit, "offset": offset},
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


# ─── Settings (master toggle + content) ────────────────────────────────────────

class WarmupSettingsUpdate(BaseModel):
    """PUT /settings body — все поля опциональны; отсутствующие сбрасываются в дефолт.

    `enabled` — master toggle прогрева workspace (D-06/D-07). `topics` / `system_prompt`
    / `tone` — per-workspace контент (D-10); пустые → resolved code-defaults в GET.
    """
    enabled: bool = False
    topics: List[str] = Field(default_factory=list)
    system_prompt: Optional[str] = None
    language: str = "ru"
    tone: Optional[str] = None


def _resolve_settings(
    enabled: bool,
    topics,            # list | None
    system_prompt: Optional[str],
    language: Optional[str],
    tone: Optional[str],
) -> dict:
    """D-10: вернуть настройки с resolved defaults (то, что реально в действии).

    Пустые topics → 24 WARMUP_TOPICS, NULL system_prompt → WARMUP_SYSTEM_PROMPT.
    """
    return {
        "enabled":       bool(enabled),
        "topics":        list(topics) if topics else list(WARMUP_TOPICS),
        "system_prompt": system_prompt if system_prompt else WARMUP_SYSTEM_PROMPT,
        "language":      language or "ru",
        "tone":          tone,
    }


@router.get("/settings")
async def get_settings_endpoint(
    db: AsyncSession = Depends(get_db),
    ctx: AuthCtx = Depends(auth_dep),
):
    """Настройки прогрева workspace: master toggle + контент с resolved defaults (D-06/D-10).

    Нет строки → дефолты с enabled=false (explicit opt-in, D-06).
    """
    result = await db.execute(
        text("""
            SELECT enabled, topics, system_prompt, language, tone
            FROM warmup_settings
            WHERE workspace_id = :wid
        """),
        {"wid": str(ctx.workspace_id)},
    )
    row = result.fetchone()
    if not row:
        return _resolve_settings(False, None, None, "ru", None)
    return _resolve_settings(row[0], row[1], row[2], row[3], row[4])


@router.put("/settings")
async def update_settings_endpoint(
    body: WarmupSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    ctx: AuthCtx = Depends(auth_dep),
):
    """Upsert настроек прогрева workspace (master toggle + контент, D-06/D-10).

    Идемпотентный INSERT ... ON CONFLICT (workspace_id) DO UPDATE. Возвращает
    {status, settings} с resolved defaults (что реально в действии).
    """
    import json

    wid = str(ctx.workspace_id)
    await db.execute(
        text("""
            INSERT INTO warmup_settings
                (workspace_id, enabled, topics, system_prompt, language, tone, updated_at)
            VALUES
                (:wid, :enabled, CAST(:topics AS jsonb), :prompt, :lang, :tone, NOW())
            ON CONFLICT (workspace_id) DO UPDATE SET
                enabled       = EXCLUDED.enabled,
                topics        = EXCLUDED.topics,
                system_prompt = EXCLUDED.system_prompt,
                language      = EXCLUDED.language,
                tone          = EXCLUDED.tone,
                updated_at    = NOW()
        """),
        {
            "wid": wid,
            "enabled": body.enabled,
            "topics": json.dumps(body.topics or []),
            "prompt": body.system_prompt,
            "lang": body.language or "ru",
            "tone": body.tone,
        },
    )
    await db.commit()

    logger.info(
        f"🔥 Warmup: настройки обновлены (ws={wid[:8]}, enabled={body.enabled})"
    )
    resolved = _resolve_settings(
        body.enabled, body.topics, body.system_prompt, body.language, body.tone
    )
    return {"status": "saved", "settings": resolved}
