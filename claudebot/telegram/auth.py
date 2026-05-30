"""Single-user access control.

The bot must only ever serve its owner — running other people's prompts on your
subscription is account sharing (and a ban risk). ``allowed_user_ids`` is the
allowlist; empty means the bot is locked to nobody until you add an ID.
"""

from __future__ import annotations

from claudebot.core.config import Settings


def is_allowed(user_id: int | None, settings: Settings) -> bool:
    return user_id is not None and user_id in settings.allowed_user_ids
