"""The administrator's switches for outgoing notifications.

Stored in the Firestore document settings/notifications, which the Notification
function reads (and caches for 30 seconds):

    email_enabled    false = no emails at all (codes still reach the in-app inbox)
    rcs_enabled      false = no RCS messages
    email_provider   "auto": Gmail, and Brevo when Gmail fails or hits its daily limit
                     "gmail" / "brevo": only that one

The function writes settings/notifications_state (Gmail paused until when, and why)
and notification_stats/<day> (how many went by each way). Only ADMIN may change
anything; every change is recorded in settings_log.
"""

from __future__ import annotations

import datetime

from .service import Deps, IdentityError

PROVIDERS = ("auto", "gmail", "brevo")
DEFAULTS = {"email_enabled": True, "rcs_enabled": True, "email_provider": "auto"}


def current(deps: Deps) -> dict:
    chosen = {**DEFAULTS, **{k: v for k, v in (deps.docstore.get("settings", "notifications") or {}).items()
                             if k in DEFAULTS}}
    state = deps.docstore.get("settings", "notifications_state") or {}
    today = datetime.datetime.fromtimestamp(deps.clock(), datetime.timezone.utc).strftime("%Y-%m-%d")
    stats = deps.docstore.get("notification_stats", today) or {}
    paused_until = state.get("gmail_paused_until") or 0
    return {
        **chosen,
        "gmail_paused_until": paused_until if paused_until > deps.clock() else None,
        "gmail_paused_reason": state.get("gmail_paused_reason") if paused_until > deps.clock() else None,
        "today": {k: v for k, v in stats.items() if k != "id"},
        "updated_by": (deps.docstore.get("settings", "notifications") or {}).get("updated_by"),
    }


def update(deps: Deps, actor: str, *, email_enabled: bool | None = None, rcs_enabled: bool | None = None,
           email_provider: str | None = None) -> dict:
    changes = {k: v for k, v in (("email_enabled", email_enabled), ("rcs_enabled", rcs_enabled),
                                 ("email_provider", email_provider)) if v is not None}
    if email_provider is not None and email_provider not in PROVIDERS:
        raise IdentityError(f"email_provider must be one of {', '.join(PROVIDERS)}")
    if not changes:
        raise IdentityError("nothing to change")
    deps.docstore.set("settings", "notifications", {**changes, "updated_by": actor, "updated_at": deps.clock()},
                      merge=True)
    deps.docstore.add("settings_log", {"setting": "notifications", "changes": changes, "actor": actor,
                                       "at": deps.clock()})
    return current(deps)


def resume_gmail(deps: Deps, actor: str) -> dict:
    """Try Gmail again before its 24-hour pause is over (e.g. after the limit reset)."""
    deps.docstore.set("settings", "notifications_state", {"gmail_paused_until": 0, "gmail_paused_reason": None},
                      merge=True)
    deps.docstore.add("settings_log", {"setting": "notifications", "changes": {"gmail_resumed": True},
                                       "actor": actor, "at": deps.clock()})
    return current(deps)
