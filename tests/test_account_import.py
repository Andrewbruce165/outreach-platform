"""Phase 21 — Bulk Telegram account import (Wave 0 RED scaffold).

Covers the per-account import surface. Every production symbol is imported INSIDE the
test body (deferred import) so ``--collect-only`` stays clean while the assertions fail
(RED) until the downstream tasks land the code:

- test_sqlite_to_stringsession_offline        → IMPT-03 (SQLite .session → StringSession, offline)
- test_fingerprint_override_and_strict_fallback→ IMPT-04 (per-account fingerprint override; strict fallback)
- test_preview_pairing                         → IMPT-01 (ZIP unzip + .json↔.session pair, no connect)
- test_twofa_encrypted_at_rest                 → IMPT-05 (2FA password Fernet-encrypted at rest)
- test_dedup_skip_and_proxy                    → IMPT-06 (dedup by telegram_id + proxy from JSON)
- test_partial_success_and_start_state         → IMPT-07 (per-file partial success; imported = active/none)

No test performs a real Telegram connect: the offline conversion uses a SYNTHETIC vendor
SQLiteSession (``build_vendor_sqlite_session`` fixture, never the live sample), and the
import routine's Telethon client is the stubbed ``stub_import_telethon`` fixture.
"""

import io
import json
import uuid
import zipfile

import pytest
from sqlalchemy import text as _t

pytestmark = pytest.mark.asyncio


def _basenames(entries):
    """Extract the pairing-key basenames from a preview list.

    Tolerant of either a list[str] of basenames or a list[dict] carrying a
    'basename'/'name' key, so this scaffold does not over-constrain 21-03's exact
    return shape — only the pairing behaviour it must produce.
    """
    out = set()
    for e in entries or []:
        if isinstance(e, str):
            out.add(e)
        elif isinstance(e, dict):
            out.add(e.get("basename") or e.get("name"))
    return out


# ─── IMPT-03: offline SQLite → StringSession conversion ─────────────────────────

async def test_sqlite_to_stringsession_offline(tmp_path, build_vendor_sqlite_session):
    """A vendor SQLite .session converts to a StringSession offline (no network) and
    round-trips to the same auth_key."""
    from app.services.account_import import sqlite_to_string_session  # RED until 21-04
    from telethon.sessions import StringSession

    src_path = build_vendor_sqlite_session(tmp_path, dc_id=2, auth_key_byte=0x11)
    with open(src_path, "rb") as f:
        session_bytes = f.read()

    ss = sqlite_to_string_session(session_bytes)

    assert isinstance(ss, str)
    assert ss.startswith("1A"), f"expected a version-1 StringSession, got {ss[:4]!r}"
    rebuilt = StringSession(ss)
    assert rebuilt.auth_key is not None
    assert rebuilt.auth_key.key == bytes([0x11]) * 256


async def test_sqlite_to_stringsession_tolerates_extra_column(
    tmp_path, build_vendor_sqlite_session
):
    """A vendor .session whose ``sessions`` table carries an EXTRA column (seen in the
    wild: a 6th ``tmp_auth_key`` written by a patched/forked Telethon) still converts.

    Regression for the field bug where such files failed ``empty_or_invalid_session``
    because Telethon's own ``SQLiteSession`` does ``SELECT *`` and unpacks exactly 5
    values → ``too many values to unpack (expected 5)`` even though the auth_key is
    valid. Our explicit-column reader must ignore the extra column."""
    import sqlite3

    from app.services.account_import import sqlite_to_string_session
    from telethon.sessions import StringSession

    src_path = build_vendor_sqlite_session(tmp_path, dc_id=2, auth_key_byte=0x33)
    # Simulate the vendor's patched schema: append a 6th column to `sessions`.
    con = sqlite3.connect(src_path)
    con.execute("ALTER TABLE sessions ADD COLUMN tmp_auth_key blob")
    con.commit()
    con.close()
    with open(src_path, "rb") as f:
        session_bytes = f.read()

    ss = sqlite_to_string_session(session_bytes)

    rebuilt = StringSession(ss)
    assert rebuilt.dc_id == 2
    assert rebuilt.auth_key is not None
    assert rebuilt.auth_key.key == bytes([0x33]) * 256


async def test_sqlite_to_stringsession_rejects_empty_and_authless(tmp_path):
    """Empty bytes, non-sqlite bytes, and a valid-but-authless sqlite all raise
    ``empty_or_invalid_session`` (never a StringSession for a dead pair)."""
    import sqlite3

    from app.services.account_import import sqlite_to_string_session

    for bad in (b"", b"not a database at all"):
        with pytest.raises(ValueError, match="empty_or_invalid_session"):
            sqlite_to_string_session(bad)

    # Valid sqlite with a `sessions` table but a NULL auth_key → still rejected.
    p = tmp_path / "authless.session"
    con = sqlite3.connect(str(p))
    con.execute(
        "CREATE TABLE sessions (dc_id integer primary key, server_address text, "
        "port integer, auth_key blob, takeout_id integer)"
    )
    con.execute("INSERT INTO sessions VALUES (2, '149.154.167.51', 443, NULL, NULL)")
    con.commit()
    con.close()
    with pytest.raises(ValueError, match="empty_or_invalid_session"):
        sqlite_to_string_session(p.read_bytes())


async def test_normalize_vendor_proxy_shapes():
    """``_normalize_vendor_proxy`` accepts the canonical dict, the PySocks-style list
    a real vendor ships (``[type, host, port, rdns, user, pass]``), and a
    ``host:port[:user:pass]`` string — and returns ``None`` for empty/garbage so the
    account still imports (falls back to the pool), never dropped over a proxy."""
    from app.services.account_import import _normalize_vendor_proxy

    # PySocks int type: 3 == HTTP (the exact shape seen in the failing archive).
    got = _normalize_vendor_proxy(
        [3, "resident.proxyshard.com", 8080, True, "user", "pass"]
    )
    assert got == {
        "type": "http",
        "host": "resident.proxyshard.com",
        "port": 8080,
        "username": "user",
        "password": "pass",
    }
    # String type + no auth.
    assert _normalize_vendor_proxy(["socks5", "1.2.3.4", "1080"]) == {
        "type": "socks5",
        "host": "1.2.3.4",
        "port": 1080,
    }
    # Canonical dict passes through.
    d = {"type": "socks5", "host": "1.2.3.4", "port": 1080, "username": "u", "password": "p"}
    assert _normalize_vendor_proxy(d) == d
    # host:port:user:pass string.
    assert _normalize_vendor_proxy("1.2.3.4:1080:u:p") == {
        "type": "socks5",
        "host": "1.2.3.4",
        "port": 1080,
        "username": "u",
        "password": "p",
    }
    # Empty / unusable shapes → None (account still imports).
    for junk in (None, "", [], {}, {"host": ""}, [99, "h", 1], ["socks5"], "noport"):
        assert _normalize_vendor_proxy(junk) is None


async def test_preview_pairs_list_proxy_record():
    """A JSON with a list-form ``proxy`` PAIRS (matched), not malformed — regression for
    the field bug where the ``proxy: dict | None`` schema failed validation on the
    vendor's list proxy and silently bucketed the whole account as malformed."""
    import io
    import json
    import zipfile

    from app.services.account_import import unpack_and_pair

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "79326406301.json",
            json.dumps(
                {
                    "phone": "79326406301",
                    "twoFA": "secret",
                    "proxy": [3, "resident.proxyshard.com", 8080, True, "u", "p"],
                }
            ),
        )
        zf.writestr("79326406301.session", b"\x00fake-sqlite-bytes")

    result = unpack_and_pair(buf.getvalue())
    assert [m["basename"] for m in result["matched"]] == ["79326406301"]
    assert result["malformed"] == []


# ─── IMPT-04: per-account fingerprint override + strict global fallback ─────────

async def test_fingerprint_override_and_strict_fallback():
    """``make_telegram_client(fingerprint=None)`` is byte-identical to today's global
    ``_CLIENT_FINGERPRINT`` and keeps ``lang_pack='tdesktop'``; a fingerprint dict
    overrides device/version/locale but STILL forces ``lang_pack='tdesktop'`` (D-04)."""
    from telethon.sessions import StringSession
    from app.services.telegram import make_telegram_client, _CLIENT_FINGERPRINT

    # RED until 21-02 adds the `fingerprint=` param (TypeError on the unexpected kwarg).
    c = make_telegram_client(StringSession(), fingerprint=None)
    init = c._init_request
    assert init.device_model == _CLIENT_FINGERPRINT["device_model"]
    assert init.system_version == _CLIENT_FINGERPRINT["system_version"]
    assert init.app_version == _CLIENT_FINGERPRINT["app_version"]
    assert init.lang_code == _CLIENT_FINGERPRINT["lang_code"]
    assert init.system_lang_code == _CLIENT_FINGERPRINT["system_lang_code"]
    assert init.lang_pack == "tdesktop"

    fp = {
        "device_model": "iPhone14,2",
        "system_version": "iOS 17.1",
        "app_version": "10.2",
        "lang_code": "en",
        "system_lang_code": "en-US",
    }
    c2 = make_telegram_client(StringSession(), fingerprint=fp)
    init2 = c2._init_request
    assert init2.device_model == "iPhone14,2"
    assert init2.system_version == "iOS 17.1"
    assert init2.app_version == "10.2"
    assert init2.lang_code == "en"
    assert init2.system_lang_code == "en-US"
    assert init2.lang_pack == "tdesktop"  # strict — never dropped even when overriding


# ─── IMPT-04 (21-02): NULL fingerprint is byte-identical on the CONSTRUCTOR kwargs ──

async def test_null_fingerprint_matches_global():
    """Regression on the built-client kwargs: ``make_telegram_client(fingerprint=None)``
    passes EXACTLY the global ``_CLIENT_FINGERPRINT`` into the client constructor,
    keeps ``api_id``/``api_hash`` at the global settings values (never per-account,
    D-03) and forces ``lang_pack='tdesktop'`` (D-04) — so the 13 phone-onboarded
    senders (all NULL fingerprint) connect byte-identically to pre-Phase-21."""
    from telethon.sessions import StringSession
    from app.services.telegram import make_telegram_client, _CLIENT_FINGERPRINT, settings

    captured = {}

    class _CapturingClient:
        def __init__(self, session, api_id, api_hash, **kwargs):
            captured["api_id"] = api_id
            captured["api_hash"] = api_hash
            captured["kwargs"] = kwargs
            # make_telegram_client patches _init_request.lang_pack after construction.
            self._init_request = type("_R", (), {})()

    client = make_telegram_client(
        StringSession(), client_class=_CapturingClient, fingerprint=None
    )

    # Every fingerprint key equals the global — strict fallback (D-02).
    for key, value in _CLIENT_FINGERPRINT.items():
        assert captured["kwargs"][key] == value
    # api_id/api_hash are the global settings values, never per-account (D-03).
    assert captured["api_id"] == settings.telegram_api_id
    assert captured["api_hash"] == settings.telegram_api_hash
    # lang_pack forced to 'tdesktop' unconditionally after construction (D-04).
    assert client._init_request.lang_pack == "tdesktop"


# ─── IMPT-04 (21-02): the checker seam FORWARDS the fingerprint (value flows) ───

async def test_checker_get_client_threads_fingerprint(monkeypatch):
    """``CheckerService._get_client`` forwards its ``fingerprint=`` kwarg into
    ``make_telegram_client`` (so imported checkers reconnect with THEIR fingerprint),
    and a NULL fingerprint stays NULL (the working checker pool is unchanged). Proven
    by capturing the ``fingerprint=`` kwarg on a stubbed ``make_telegram_client`` — not
    by grepping the literal."""
    from unittest.mock import AsyncMock, MagicMock

    import app.services.checker as checker_mod
    from app.services.checker import CheckerService
    from app.services.encryption import encrypt_session
    from telethon.sessions import StringSession

    captured = {}

    def _fake_make_client(session, proxy=None, flood_sleep_threshold=60,
                          client_class=None, fingerprint=None):
        captured["fingerprint"] = fingerprint
        stub = MagicMock()
        stub.connect = AsyncMock()
        stub.is_user_authorized = AsyncMock(return_value=True)
        stub.disconnect = AsyncMock()
        stub.is_connected = MagicMock(return_value=True)
        return stub

    monkeypatch.setattr(checker_mod, "make_telegram_client", _fake_make_client)

    enc = encrypt_session(StringSession().save())
    svc = CheckerService()

    fp = {"device_model": "KVM", "system_version": "Windows 10 x64",
          "app_version": "6.8.2 x64", "lang_code": "en", "system_lang_code": "en-US"}
    await svc._get_client(enc, proxy=None, fingerprint=fp)
    assert captured["fingerprint"] == fp

    await svc._get_client(enc, proxy=None, fingerprint=None)
    assert captured["fingerprint"] is None


# ─── IMPT-01: preview unzip + pair by basename, no Telegram connect ─────────────

async def test_preview_pairing():
    """``unpack_and_pair`` matches .json↔.session by basename and reports orphans
    (unpaired) + bad-JSON (malformed), with no Telegram connect."""
    from app.services.account_import import unpack_and_pair  # RED until 21-03

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        # 1) matched pair
        zf.writestr("+18646884306.json", json.dumps({"twoFA": None, "app_version": "6.8.2"}))
        zf.writestr("+18646884306.session", b"\x00fake-sqlite-bytes")
        # 2) orphan .json (no matching .session)
        zf.writestr("+15551234567.json", json.dumps({"twoFA": None}))
        # 3) orphan .session (no matching .json)
        zf.writestr("+79990001111.session", b"\x00orphan-session")
        # 4) malformed .json (paired session present, but JSON does not parse)
        zf.writestr("+491234567.json", "{ this is : not valid json")
        zf.writestr("+491234567.session", b"\x00bad-json-session")

    result = unpack_and_pair(buf.getvalue())

    assert "+18646884306" in _basenames(result["matched"])
    unpaired = _basenames(result["unpaired"])
    assert "+15551234567" in unpaired  # orphan json
    assert "+79990001111" in unpaired  # orphan session
    assert "+491234567" in _basenames(result["malformed"])


# ─── IMPT-01: POST /accounts/import/preview stages the ZIP + returns the summary ──

async def test_preview_endpoint_stages_and_returns(
    async_client, async_db_session, valid_supabase_jwt
):
    """``POST /api/v1/accounts/import/preview`` unzips + pairs synchronously, returns
    import_id + matched/unpaired/malformed, and stages an ``account_import_stagings``
    row with a FUTURE ``expires_at`` — never leaking the twoFA value or session bytes.
    """
    from datetime import datetime, timezone

    token = valid_supabase_jwt(sub="imp-preview", email="imp-preview@test.com")
    r = await async_client.post(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, r.text
    ws = r.json()["workspace_id"]

    buf = io.BytesIO()
    secret_2fa = "super-secret-2fa"
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "+18646884306.json",
            json.dumps({"twoFA": secret_2fa, "app_version": "6.8.2", "proxy": {"host": "1.2.3.4"}}),
        )
        zf.writestr("+18646884306.session", b"\x00fake-sqlite-bytes")
        zf.writestr("+15551234567.json", json.dumps({"twoFA": None}))  # orphan json
        zf.writestr("+79990001111.session", b"\x00orphan")            # orphan session
        zf.writestr("+491234567.json", "{ not valid json")            # malformed
        zf.writestr("+491234567.session", b"\x00bad")

    resp = await async_client.post(
        "/api/v1/accounts/import/preview",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("accounts.zip", buf.getvalue(), "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    import_id = body["import_id"]
    assert import_id
    assert {m["basename"] for m in body["matched"]} == {"+18646884306"}
    assert {u["basename"] for u in body["unpaired"]} == {"+15551234567", "+79990001111"}
    assert {m["basename"] for m in body["malformed"]} == {"+491234567"}
    # flags only — the raw twoFA value is NEVER in the response.
    assert body["matched"][0]["has_2fa"] is True
    assert body["matched"][0]["has_proxy"] is True
    assert secret_2fa not in resp.text

    # A staging row persisted with the ZIP + a future expires_at.
    row = (await async_db_session.execute(_t("""
        SELECT workspace_id, octet_length(zip_data) AS zlen, expires_at
        FROM account_import_stagings WHERE id = :id
    """), {"id": import_id})).mappings().first()
    assert row is not None
    assert str(row["workspace_id"]) == ws
    assert row["zlen"] == len(buf.getvalue())
    assert row["expires_at"] > datetime.now(timezone.utc)


# ─── IMPT-05: 2FA password Fernet-encrypted at rest, never plaintext ────────────

async def test_twofa_encrypted_at_rest(async_db_session, test_workspace):
    """A stored ``senders.twofa_password_enc`` is Fernet ciphertext (never the plaintext)
    and decrypts back to the original password."""
    from app.services.account_import import encrypt_twofa  # RED until 21-04/21-05
    from app.services.encryption import decrypt_session

    plaintext = "s3cr3t-2fa-pass"
    enc = encrypt_twofa(plaintext)
    assert enc != plaintext
    assert plaintext not in enc

    sid = str(uuid.uuid4())
    await async_db_session.execute(_t("""
        INSERT INTO senders (id, workspace_id, slug, name, phone, session_string,
                             role, auth_status, lifecycle_status, twofa_password_enc)
        VALUES (:id, :ws, :slug, 'Imported', '+18646884306', 'encrypted_stub',
                'sender', 'ok', 'active', :enc)
    """), {"id": sid, "ws": str(test_workspace.id), "slug": f"imp-{sid[:8]}", "enc": enc})
    await async_db_session.commit()

    stored = (await async_db_session.execute(_t(
        "SELECT twofa_password_enc FROM senders WHERE id = :id"
    ), {"id": sid})).scalar_one()
    assert stored != plaintext
    assert decrypt_session(stored) == plaintext


# ─── IMPT-06: dedup by telegram_id + proxy from JSON ────────────────────────────

async def test_dedup_skip_and_proxy(async_db_session, test_workspace, stub_import_telethon):
    """Second import of an already-connected telegram_id → item result
    'already_connected' and the existing sender's session_string is untouched; a JSON
    proxy is honoured on the imported sender."""
    import app.services.account_import as ai_mod  # RED until 21-04
    from app.services.account_import import import_one_account

    stub_import_telethon.install(ai_mod)
    dup_tg_id = 778899
    stub_import_telethon.client.get_me.return_value.id = dup_tg_id

    # Pre-existing sender with the same telegram_id and a known session_string.
    existing_id = str(uuid.uuid4())
    await async_db_session.execute(_t("""
        INSERT INTO senders (id, workspace_id, slug, name, phone, telegram_id,
                             session_string, role, auth_status, lifecycle_status)
        VALUES (:id, :ws, 'existing-dup', 'Existing', '+18646884306', :tid,
                'ORIGINAL_UNTOUCHED', 'sender', 'ok', 'active')
    """), {"id": existing_id, "ws": str(test_workspace.id), "tid": dup_tg_id})

    # Job + item carrying a proxy in the vendor JSON.
    job_id = str(uuid.uuid4())
    await async_db_session.execute(_t("""
        INSERT INTO account_import_jobs (id, workspace_id, role, status, total)
        VALUES (:id, :ws, 'sender', 'running', 1)
    """), {"id": job_id, "ws": str(test_workspace.id)})
    proxy = {"type": "socks5", "host": "1.2.3.4", "port": 1080,
             "username": "u", "password": "p"}
    item_id = str(uuid.uuid4())
    await async_db_session.execute(_t("""
        INSERT INTO account_import_items
            (id, job_id, workspace_id, basename, session_blob, vendor_json)
        VALUES (:id, :job, :ws, '+18646884306', :blob, CAST(:vj AS JSONB))
    """), {"id": item_id, "job": job_id, "ws": str(test_workspace.id),
           "blob": b"\x00sqlite", "vj": json.dumps({"proxy": proxy})})
    await async_db_session.commit()

    item = (await async_db_session.execute(_t(
        "SELECT * FROM account_import_items WHERE id = :id"), {"id": item_id})).mappings().first()

    result = await import_one_account(async_db_session, dict(item))

    assert result == "already_connected"
    untouched = (await async_db_session.execute(_t(
        "SELECT session_string FROM senders WHERE id = :id"), {"id": existing_id})).scalar_one()
    assert untouched == "ORIGINAL_UNTOUCHED"


# ─── IMPT-07: per-file partial success + imported start state ───────────────────

async def test_partial_success_and_start_state(
    async_db_session, test_workspace, stub_import_telethon, tmp_path, build_vendor_sqlite_session
):
    """A batch with one broken pair → that item 'failed', a good pair imports 'ok' and
    the created sender starts lifecycle_status='active' + restriction_status='none'."""
    import app.services.account_import as ai_mod  # RED until 21-04
    from app.services.account_import import import_one_account

    stub_import_telethon.install(ai_mod)
    stub_import_telethon.client.get_me.return_value.id = 555001

    good_path = build_vendor_sqlite_session(tmp_path, dc_id=2, auth_key_byte=0x22)
    with open(good_path, "rb") as f:
        good_blob = f.read()

    job_id = str(uuid.uuid4())
    await async_db_session.execute(_t("""
        INSERT INTO account_import_jobs (id, workspace_id, role, status, total)
        VALUES (:id, :ws, 'sender', 'running', 2)
    """), {"id": job_id, "ws": str(test_workspace.id)})

    good_id, bad_id = str(uuid.uuid4()), str(uuid.uuid4())
    await async_db_session.execute(_t("""
        INSERT INTO account_import_items
            (id, job_id, workspace_id, basename, session_blob, vendor_json)
        VALUES (:id, :job, :ws, '+15550000001', :blob, '{}'::jsonb)
    """), {"id": good_id, "job": job_id, "ws": str(test_workspace.id), "blob": good_blob})
    await async_db_session.execute(_t("""
        INSERT INTO account_import_items
            (id, job_id, workspace_id, basename, session_blob, vendor_json)
        VALUES (:id, :job, :ws, '+15550000002', :blob, '{}'::jsonb)
    """), {"id": bad_id, "job": job_id, "ws": str(test_workspace.id),
           "blob": b"\x00not-a-sqlite-file"})
    await async_db_session.commit()

    good_item = dict((await async_db_session.execute(_t(
        "SELECT * FROM account_import_items WHERE id = :id"), {"id": good_id})).mappings().first())
    bad_item = dict((await async_db_session.execute(_t(
        "SELECT * FROM account_import_items WHERE id = :id"), {"id": bad_id})).mappings().first())

    good_result = await import_one_account(async_db_session, good_item)
    bad_result = await import_one_account(async_db_session, bad_item)

    assert good_result in ("imported", "ok")
    assert bad_result not in ("imported", "ok", "already_connected")

    created = (await async_db_session.execute(_t("""
        SELECT lifecycle_status, restriction_status FROM senders
        WHERE workspace_id = :ws AND phone = '+15550000001'
    """), {"ws": str(test_workspace.id)})).mappings().first()
    assert created is not None
    assert created["lifecycle_status"] == "active"
    assert created["restriction_status"] == "none"


# ─── IMPT-10 (21-02): 2FA autofill uses the stored password + account fingerprint ──

async def test_2fa_autofill_uses_stored_password(
    async_client, async_db_session, valid_supabase_jwt
):
    """IMPT-10 (D-06/D-07): ``POST /senders/{slug}/2fa`` with ``current_password``
    OMITTED on an IMPORTED account falls back to the stored, decrypted
    ``twofa_password_enc`` as ``current_password`` AND connects under the account's
    ``client_fingerprint`` — while the plaintext is NEVER returned in the response."""
    from unittest.mock import AsyncMock, patch

    import app.services.telegram as telegram_module
    from app.services.encryption import encrypt_session

    token = valid_supabase_jwt(sub="imp-2fa", email="imp-2fa@test.com")
    r = await async_client.post(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, r.text
    ws = r.json()["workspace_id"]

    plaintext = "imported-2fa-pass"
    enc = encrypt_session(plaintext)
    fp = {"device_model": "KVM", "system_version": "Windows 10 x64",
          "app_version": "6.8.2 x64", "lang_code": "en", "system_lang_code": "en-US"}

    sid = str(uuid.uuid4())
    await async_db_session.execute(_t("""
        INSERT INTO senders (id, workspace_id, slug, name, phone, session_string,
                             role, auth_status, lifecycle_status,
                             twofa_password_enc, client_fingerprint)
        VALUES (:id, :ws, 'imp-2fa-1', 'Imported', '+18646884306', 'encrypted_stub',
                'sender', 'ok', 'active', :enc, CAST(:fp AS JSONB))
    """), {"id": sid, "ws": ws, "enc": enc, "fp": json.dumps(fp)})
    await async_db_session.commit()

    with patch.object(
        telegram_module.telegram_service, "edit_2fa",
        new=AsyncMock(return_value={"success": True}), create=True,
    ) as mock_2fa:
        resp = await async_client.post(
            "/api/v1/senders/imp-2fa-1/2fa",
            headers={"Authorization": f"Bearer {token}"},
            json={"new_password": "brand-new-pass"},  # current_password OMITTED
        )

    assert resp.status_code == 200, resp.text
    assert mock_2fa.await_count == 1
    kwargs = mock_2fa.await_args.kwargs
    # (a) D-06: the stored decrypted password is used as current_password.
    assert kwargs["current_password"] == plaintext
    # (b) the account fingerprint reached edit_2fa (Part-B threading, site edit_2fa).
    assert kwargs["fingerprint"] == fp
    # (c) D-07: the plaintext is NOWHERE in the response body.
    assert plaintext not in resp.text


# ─── IMPT-04 (21-02): a profile method forwards fingerprint into get_client ─────

async def test_profile_method_accepts_fingerprint():
    """A Phase-20 TelegramService profile method accepts + forwards a ``fingerprint``
    into ``self.get_client`` — so the router can pass ``sender.client_fingerprint``
    without raising ``TypeError``. Proven by capturing the ``fingerprint=`` kwarg on a
    stubbed ``get_client``.

    NB: patch via ``patch.object`` (context manager), NOT ``monkeypatch.setattr`` on
    the singleton instance — monkeypatch restores a resolved-from-class attribute by
    re-setting a bound method onto the instance ``__dict__``, which then shadows the
    class-level ``patch.object`` a later test relies on (poisons
    test_cr04_profile_call_signatures). ``patch.object`` deletes the instance attr on
    exit, so no cross-test leakage."""
    from unittest.mock import AsyncMock, patch

    from app.services.telegram import telegram_service

    captured = {}

    async def _fake_get_client(sender_slug, sender_id, encrypted_session,
                               proxy=None, fingerprint=None):
        captured["fingerprint"] = fingerprint
        return AsyncMock()

    fp = {"device_model": "KVM", "system_version": "Windows 10 x64",
          "app_version": "6.8.2 x64", "lang_code": "en", "system_lang_code": "en-US"}

    with patch.object(telegram_service, "get_client", new=_fake_get_client), \
            patch.object(telegram_service, "disconnect_client", new=AsyncMock()):
        # update_profile just dispatches the pre-built request via `await client(request)`.
        result = await telegram_service.update_profile(
            "slug", "sid", "enc", object(), proxy=None, fingerprint=fp
        )
    assert result == {"success": True}
    assert captured["fingerprint"] == fp
