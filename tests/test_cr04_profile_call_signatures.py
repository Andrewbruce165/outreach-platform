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
