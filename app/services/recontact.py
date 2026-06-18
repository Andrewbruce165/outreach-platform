"""Shared re-contact policy (migration 026).

A conversation *protects* a contact from a cold campaign opener when it is a
live thread: its status is one of :data:`PROTECTED_STATUSES` **and** it has seen
activity within ``recontact_min_age_days`` (``conversations.updated_at`` is kept
honest by the ``messages_touch_conversation`` trigger from migration 026).

Closed (``finished``) or stale dialogs are *not* protected:
  * in ``campaign_enqueue`` (when ``campaigns.allow_recontact`` is true) they no
    longer block a contact from being enqueued;
  * in ``queue._upsert_conversation`` a send to such a contact opens a brand-new
    conversation row (empty AI history = real fresh start) instead of reusing
    the old thread.

This is the single source of truth for both call sites — keep the SQL fragment
here so the enqueue filter and the upsert lookup never drift apart.
"""

# bot_ignored stays protected on purpose: the AI was deliberately silenced on
# that peer (e.g. system bots) — it must never receive a cold opener.
PROTECTED_STATUSES = ("active", "manual", "paused", "lead", "handoff", "bot_ignored")

# Quoted comma list for inlining into raw-SQL IN (...). Fixed module constant
# (no user input) — safe to interpolate into the query string.
_PROTECTED_STATUSES_SQL = ", ".join(f"'{s}'" for s in PROTECTED_STATUSES)


def protected_conversation_sql(age_param: str = "age_days") -> str:
    """Return a SQL boolean fragment that is true for a *protected* dialog.

    Assumes ``status`` and ``updated_at`` columns are in scope (no table alias).
    ``age_param`` is the bind-parameter name carrying ``recontact_min_age_days``
    (integer days) — the caller must supply it in the query params.
    """
    # make_interval(days => N) keeps the bind param an integer — string concat
    # (N || ' days')::interval makes asyncpg infer the param as text and reject
    # an int.
    return (
        f"status IN ({_PROTECTED_STATUSES_SQL}) "
        f"AND updated_at > now() - make_interval(days => :{age_param})"
    )
