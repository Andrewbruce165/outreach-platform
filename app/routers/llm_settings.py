"""Phase 18 — workspace-scoped LLM settings API (LLMP-01/02/03/04/05/08).

The surface the Settings UI (18-05) drives. Every endpoint resolves EXACTLY ONE config
row per workspace, keyed on `ctx.workspace_id` — the workspace-level setting scope
(D-01: no per-agent granularity; every agent/campaign in the workspace shares this one
LLM config). A workspace can never read or write another workspace's llm-settings.

Endpoints (all under /api/v1/workspace/llm-settings):
  GET    /                  — masked read (api_key_prefix only, NEVER the full key); no row
                              => provider default + api_key_status='unset' + model null (D-02).
  PATCH  /                  — upsert the single workspace row; store the key Fernet-encrypted
                              (D-04), mask to prefix+last4, reset status to 'unset' (must
                              re-test). D-03 gate: switching provider/model needs a key.
  POST   /test-connection   — probe the chosen provider (D-05); success => api_key_status
                              'valid', key-level error => 'invalid'.
  GET    /models            — live, family-filtered model list per provider (D-08); soft-fails
                              to an empty list + note so a provider outage never 500s Settings.

Security invariants (mirror workspace.py):
  - cross-tenant guard: every query filters WHERE workspace_id == ctx.workspace_id (D-01).
  - PATCH / test-connection are JWT-only (_require_jwt), like the other owner mutations.
  - the plaintext api key is NEVER logged and NEVER placed in a response body or an
    HTTPException detail — only `api_key_prefix` (prefix+last4) is ever returned.
"""

import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.services.encryption import decrypt_api_key, encrypt_api_key
from app.services.llm import resolve as llm_resolve
from app.services.llm.capabilities import clamp_max_tokens, filter_chat_models
from app.utils.auth import AuthCtx, auth_dep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["llm-settings"])

_BASE = "/workspace/llm-settings"


# === Schemas ===

class LLMSettingsResponse(BaseModel):
    """Masked read of the workspace LLM config. NO plaintext key field EVER exists here —
    only `api_key_prefix` (prefix+last4) is exposed to the UI (D-04)."""
    provider: str
    model: Optional[str] = None
    api_key_prefix: Optional[str] = None
    api_key_status: str
    temperature: Optional[float] = None
    reasoning_effort: Optional[str] = None
    max_tokens: Optional[int] = None


class LLMSettingsUpdate(BaseModel):
    """All fields optional — a PATCH may change just the model, just the key, etc."""
    provider: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    temperature: Optional[float] = None
    reasoning_effort: Optional[str] = None
    max_tokens: Optional[int] = None


class TestConnectionRequest(BaseModel):
    provider: Optional[str] = None
    api_key: Optional[str] = None


class TestConnectionResponse(BaseModel):
    status: str  # 'valid' | 'invalid'
    detail: Optional[str] = None


class ModelListResponse(BaseModel):
    models: list[str]
    note: Optional[str] = None


# === Helpers ===

def _require_jwt(ctx: AuthCtx) -> None:
    """JWT-only mutations (mirror workspace.py::_require_jwt)."""
    if ctx.source != "jwt":
        raise HTTPException(
            status_code=403,
            detail={"code": "JWT_REQUIRED", "message": "This endpoint requires JWT auth (not API key)"},
        )


def _mask(key: str) -> str:
    """prefix+last4 — the ONLY key material ever returned/stored for display (D-04)."""
    if len(key) <= 10:
        return f"{key[:2]}...{key[-2:]}" if len(key) > 4 else "***"
    return f"{key[:6]}...{key[-4:]}"


async def _get_row(db: AsyncSession, workspace_id):
    """SELECT the single llm_settings row for this workspace (D-01 scope), or None."""
    return (
        await db.execute(
            text(
                """
                SELECT provider, model, api_key_encrypted, api_key_prefix, api_key_status,
                       temperature, reasoning_effort, max_tokens
                  FROM llm_settings
                 WHERE workspace_id = :wid
                """
            ),
            {"wid": str(workspace_id)},
        )
    ).first()


def _default_response() -> LLMSettingsResponse:
    """D-02 default-off: no row => platform provider default, no model chosen yet, key unset."""
    return LLMSettingsResponse(
        provider="openai",
        model=None,
        api_key_prefix=None,
        api_key_status="unset",
        temperature=None,
        reasoning_effort=None,
        max_tokens=None,
    )


# === Endpoints ===

@router.get(_BASE, response_model=LLMSettingsResponse)
async def get_llm_settings(
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Masked read of the workspace LLM config (D-01 workspace-scoped). Absent row =>
    platform default (D-02). NEVER decrypts the key into the response — api_key_prefix only."""
    row = await _get_row(db, ctx.workspace_id)
    if row is None:
        return _default_response()
    (provider, model, _enc, prefix, status,
     temperature, reasoning_effort, max_tokens) = row
    return LLMSettingsResponse(
        provider=provider,
        model=model,
        api_key_prefix=prefix,
        api_key_status=status,
        temperature=temperature,
        reasoning_effort=reasoning_effort,
        max_tokens=max_tokens,
    )


@router.patch(_BASE, response_model=LLMSettingsResponse)
async def patch_llm_settings(
    update: LLMSettingsUpdate,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Upsert THE single workspace row (D-01). Store the key Fernet-encrypted (D-04),
    mask to prefix+last4, reset api_key_status to 'unset' (re-test required). D-03 gate:
    switching to a non-default provider/model needs a key (stored or in the body)."""
    _require_jwt(ctx)
    settings = get_settings()

    row = await _get_row(db, ctx.workspace_id)
    stored_encrypted = row[2] if row is not None else None

    # Effective (post-PATCH) provider/model — fall back to the stored values, then defaults.
    cur_provider = row[0] if row is not None else "openai"
    cur_model = row[1] if row is not None else None
    eff_provider = update.provider if update.provider is not None else cur_provider
    eff_model = update.model if update.model is not None else cur_model

    # D-03 key-mandatory gate: a switch off the platform default (non-openai provider OR a
    # model other than the platform default) requires a key — either newly supplied in the
    # body or already stored & encrypted for this workspace.
    is_switch = (eff_provider != "openai") or (
        eff_model is not None and eff_model != settings.openai_model
    )
    has_key = bool(update.api_key) or bool(stored_encrypted)
    if is_switch and not has_key:
        raise HTTPException(
            status_code=400,
            detail={"code": "KEY_REQUIRED", "message": "An API key is required to switch provider/model"},
        )

    # Build the column values to upsert.
    new_provider = eff_provider
    new_model = eff_model
    new_temperature = update.temperature if update.temperature is not None else (row[5] if row else None)
    new_reasoning = update.reasoning_effort if update.reasoning_effort is not None else (row[6] if row else None)
    new_max_tokens = update.max_tokens if update.max_tokens is not None else (row[7] if row else None)

    # Defensive green-corridor clamp when both model + max_tokens are present (18-02 also
    # clamps at call time; this keeps the persisted value sane too).
    if new_model and new_max_tokens is not None:
        new_max_tokens = clamp_max_tokens(new_model, new_max_tokens)

    if update.api_key:
        new_encrypted = encrypt_api_key(update.api_key)  # D-04 Fernet
        new_prefix = _mask(update.api_key)
        new_status = "unset"  # a new key MUST be re-tested before it is trusted
    else:
        new_encrypted = stored_encrypted
        new_prefix = row[3] if row is not None else None
        new_status = row[4] if row is not None else "unset"

    await db.execute(
        text(
            """
            INSERT INTO llm_settings
                (workspace_id, provider, model, api_key_encrypted, api_key_prefix,
                 api_key_status, temperature, reasoning_effort, max_tokens)
            VALUES
                (:wid, :provider, :model, :enc, :prefix, :status, :temp, :effort, :max_tokens)
            ON CONFLICT (workspace_id) DO UPDATE SET
                provider          = EXCLUDED.provider,
                model             = EXCLUDED.model,
                api_key_encrypted = EXCLUDED.api_key_encrypted,
                api_key_prefix    = EXCLUDED.api_key_prefix,
                api_key_status    = EXCLUDED.api_key_status,
                temperature       = EXCLUDED.temperature,
                reasoning_effort  = EXCLUDED.reasoning_effort,
                max_tokens        = EXCLUDED.max_tokens,
                updated_at        = NOW()
            """
        ),
        {
            "wid": str(ctx.workspace_id),
            "provider": new_provider,
            "model": new_model,
            "enc": new_encrypted,
            "prefix": new_prefix,
            "status": new_status,
            "temp": new_temperature,
            "effort": new_reasoning,
            "max_tokens": new_max_tokens,
        },
    )
    await db.commit()

    return LLMSettingsResponse(
        provider=new_provider,
        model=new_model,
        api_key_prefix=new_prefix,
        api_key_status=new_status,
        temperature=new_temperature,
        reasoning_effort=new_reasoning,
        max_tokens=new_max_tokens,
    )


@router.post(f"{_BASE}/test-connection", response_model=TestConnectionResponse)
async def test_connection(
    body: TestConnectionRequest,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Probe the chosen provider with the resolved key (D-05). Success => flip the stored
    api_key_status to 'valid'; a key-level (or any probe) error => 'invalid'. The key is
    resolved from the body override first, else the stored decrypted key. Never leaks it."""
    _require_jwt(ctx)

    row = await _get_row(db, ctx.workspace_id)
    provider = body.provider or (row[0] if row is not None else "openai")

    # Resolve the key: body override wins; else decrypt the stored key.
    key = body.api_key
    if not key and row is not None and row[2]:
        key = decrypt_api_key(row[2])
    if not key:
        raise HTTPException(
            status_code=400,
            detail={"code": "KEY_REQUIRED", "message": "No API key to test (provide one or save it first)"},
        )

    try:
        await llm_resolve.probe_key(provider, key)
        new_status = "valid"
        result = TestConnectionResponse(status="valid")
    except Exception as exc:  # noqa: BLE001 — any probe failure => invalid; key never in detail
        new_status = "invalid"
        detail = "key-level error" if llm_resolve.is_key_level_error(exc) else "probe failed"
        result = TestConnectionResponse(status="invalid", detail=detail)

    # Persist the outcome only when a row exists (a body-only test has nothing to flip).
    if row is not None:
        await db.execute(
            text(
                "UPDATE llm_settings SET api_key_status = :s, updated_at = NOW() "
                "WHERE workspace_id = :wid"
            ),
            {"s": new_status, "wid": str(ctx.workspace_id)},
        )
        await db.commit()

    return result


@router.get(f"{_BASE}/models", response_model=ModelListResponse)
async def list_models(
    provider: str = Query(...),
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Live, family-filtered model list per provider (D-08). Uses the stored/decrypted key.
    Soft-fails to an empty list + a note on any provider error — test-connection is the
    authoritative validity signal, so a transient /v1/models outage must not 500 Settings."""
    row = await _get_row(db, ctx.workspace_id)
    key = None
    if row is not None and row[2]:
        key = decrypt_api_key(row[2])
    if provider == "openai" and not key:
        # OpenAI can list against the platform key (default-off path) so the picker still
        # works before a BYO key is saved.
        import os
        key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise HTTPException(
            status_code=400,
            detail={"code": "KEY_REQUIRED", "message": "Save and validate an API key to list models"},
        )

    try:
        raw_ids = await llm_resolve.list_model_ids(provider, key)
        kept = filter_chat_models(provider, raw_ids)
        return ModelListResponse(models=kept)
    except Exception:  # noqa: BLE001 — soft-fail, never leak the key
        logger.warning("[llm-settings] model list unavailable for provider=%s workspace=%s",
                       provider, ctx.workspace_id)
        return ModelListResponse(models=[], note="model list unavailable")
