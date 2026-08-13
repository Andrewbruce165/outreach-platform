"""CR-04 (re-review 260706): Phase-20 profile methods vs get_client signature.

Regression: Batch G (WR-14) made ``get_client`` require ``sender_id`` as its 2nd
positional arg, but the 9 Phase-20 canonical methods kept calling the old 2-arg
form → ``TypeError`` on EVERY profile/username/photo/2FA call. Deployed broken;
nastiest symptom: the username-check router swallowed the crash and returned
``available=true`` for every input.

Two guards:
1. Signature-order lock for all 15 methods (canonical + router-facing aliases):
   positional order must start (sender_slug, sender_id, encrypted_session).
2. Behavioral: a canonical method actually forwards sender_id to get_client.
"""

import inspect

import pytest
from unittest.mock import AsyncMock, patch

PHASE20_METHODS = [
    "update_profile",
    "check_username",
    "set_username",
    "update_username",
    "set_profile_photo",
    "upload_profile_photo",
    "delete_profile_photo",
    "delete_profile_photos",
    "resync_profile",
    "fetch_profile",
    "change_2fa_password",
    "edit_2fa",
    "start_recovery_email",
    "set_recovery_email",
    "confirm_recovery_email",
]


def test_phase20_methods_signature_order_matches_get_client():
    """Every Phase-20 method (and get_client itself) must take
    (sender_slug, sender_id, encrypted_session) as its first three params —
    the order the routers pass positionally. Drift here is the exact CR-04 bug."""
    from app.services.telegram import TelegramService

    for name in PHASE20_METHODS + ["get_client"]:
        fn = getattr(TelegramService, name)
        params = [p for p in inspect.signature(fn).parameters if p != "self"]
        assert params[:3] == ["sender_slug", "sender_id", "encrypted_session"], (
            f"{name}: positional order must be (sender_slug, sender_id, "
            f"encrypted_session), got {params[:3]} — CR-04 regression"
        )


@pytest.mark.asyncio
async def test_check_username_forwards_sender_id_to_get_client():
    """The canonical per-op skeleton must pass sender_id through to get_client
    (which needs it to flip auth_status by PRIMARY KEY on a dead session, WR-14)."""
    from app.services.telegram import telegram_service

    class _FakeClient:
        async def __call__(self, request):
            return True  # CheckUsernameRequest → available

    fake = _FakeClient()
    with patch.object(
        type(telegram_service), "get_client", new=AsyncMock(return_value=fake)
    ) as gc, patch.object(
        type(telegram_service), "disconnect_client", new=AsyncMock()
    ):
        res = await telegram_service.check_username(
            "sender-1", "uuid-1", "enc-session", "newname", proxy=None
        )

    assert res == {"available": True, "reason": None}
    args = gc.await_args.args
    assert args[0] == "sender-1" and args[1] == "uuid-1" and args[2] == "enc-session", (
        f"get_client must receive (slug, sender_id, session), got {args[:3]}"
    )


@pytest.mark.asyncio
async def test_start_recovery_email_no_2fa_password_raises_clear_error():
    """Bug (recovery-email-password-algo): an account WITHOUT a cloud (2FA)
    password makes GetPasswordRequest return has_password=False / current_algo=None.
    Feeding that to telethon compute_check raised the raw
    'unsupported password algorithm NoneType' ValueError, which the router surfaced
    verbatim as a 500 toast. start_recovery_email must gate on has_password BEFORE
    compute_check and raise the recognizable NO_2FA_PASSWORD marker instead."""
    from app.services.telegram import telegram_service

    class _NoPasswordAccount:
        # Mirrors telethon account.Password for an account with no cloud password.
        has_password = False
        current_algo = None

    class _FakeClient:
        async def __call__(self, request):
            return _NoPasswordAccount()  # GetPasswordRequest → no 2FA password

    fake = _FakeClient()
    with patch.object(
        type(telegram_service), "get_client", new=AsyncMock(return_value=fake)
    ), patch.object(
        type(telegram_service), "disconnect_client", new=AsyncMock()
    ):
        with pytest.raises(ValueError) as exc_info:
            await telegram_service.start_recovery_email(
                "sender-1", "uuid-1", "enc-session",
                current_password=None, email="recover@example.com", proxy=None,
            )

    # The marker the router maps to 400 NO_2FA_PASSWORD — NOT the raw telethon text.
    assert "NO_2FA_PASSWORD" in str(exc_info.value)
    assert "unsupported password algorithm" not in str(exc_info.value)


class _PasswordAccount:
    """Mirrors telethon account.Password for an account WITH a cloud password."""
    has_password = True
    current_algo = object()  # non-None → compute_check would run (we patch it)
    hint = "myhint"

    def __init__(self):
        # new_algo carries a mutable salt1 the real flow randomises in place.
        self.new_algo = type("_Algo", (), {"salt1": b"\x00" * 8})()


@pytest.mark.asyncio
async def test_start_recovery_email_missing_password_requires_it():
    """Fix (recovery-email-password-algo, field-test cycle): for an account WITH a
    cloud password we must re-submit the (re-hashed) password to trigger the email.
    That requires current_password — without it we'd hash "" and REMOVE 2FA. The
    method must raise CURRENT_PASSWORD_REQUIRED (router → 400), never proceed."""
    from app.services.telegram import telegram_service

    class _FakeClient:
        async def __call__(self, request):
            return _PasswordAccount()

    with patch.object(
        type(telegram_service), "get_client", new=AsyncMock(return_value=_FakeClient())
    ), patch.object(
        type(telegram_service), "disconnect_client", new=AsyncMock()
    ):
        with pytest.raises(ValueError) as exc_info:
            await telegram_service.start_recovery_email(
                "sender-1", "uuid-1", "enc-session",
                current_password=None, email="recover@example.com", proxy=None,
            )
    assert "CURRENT_PASSWORD_REQUIRED" in str(exc_info.value)


@pytest.mark.asyncio
async def test_start_recovery_email_sends_full_password_settings():
    """ROOT CAUSE regression: the old code sent PasswordInputSettings(email=only),
    which Telegram accepts as a no-op — no confirmation email, silent 'success'.
    The fix must submit new_algo + new_password_hash (re-hash of the SAME password)
    alongside email, and it must raise/propagate EmailUnconfirmedError so the code
    length flows to the UI. This test captures the outgoing UpdatePasswordSettings
    request and asserts the full password payload is present."""
    from app.services.telegram import telegram_service
    from telethon.errors import EmailUnconfirmedError

    captured = {}

    class _FakeClient:
        async def __call__(self, request):
            name = type(request).__name__
            if name == "GetPasswordRequest":
                return _PasswordAccount()
            if name == "UpdatePasswordSettingsRequest":
                captured["new_settings"] = request.new_settings
                # Telegram's real response to a valid recovery-email change.
                raise EmailUnconfirmedError(request=request, capture=6)
            raise AssertionError(f"unexpected request {name}")

    # start_recovery_email does a local `from telethon.password import ...`, so
    # the patch target is telethon.password (resolved at call time).
    with patch.object(
        type(telegram_service), "get_client", new=AsyncMock(return_value=_FakeClient())
    ), patch.object(
        type(telegram_service), "disconnect_client", new=AsyncMock()
    ), patch(
        "telethon.password.compute_check", new=lambda pwd, pw: object()
    ), patch(
        "telethon.password.compute_digest", new=lambda algo, pw: b"HASH"
    ):
        res = await telegram_service.start_recovery_email(
            "sender-1", "uuid-1", "enc-session",
            current_password="secret", email="recover@example.com", proxy=None,
        )

    ns = captured["new_settings"]
    # The bug was email-only. The fix must carry the re-hashed password too.
    assert ns.email == "recover@example.com"
    assert ns.new_password_hash == b"HASH", "must re-submit re-hashed password, not email-only"
    assert ns.new_algo is not None, "new_algo must be included so Telegram registers the change"
    # code_length from EmailUnconfirmedError must flow back for the UI prompt.
    assert res.get("code_length") == 6
