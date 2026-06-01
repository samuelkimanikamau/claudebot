"""Filesystem locations for claudebot's per-user state.

Everything lives under ``~/.claudebot`` (override with ``CLAUDEBOT_STATE_DIR``):

    ~/.claudebot/
        .env            # config written by the setup wizard (chmod 600)
        sessions.json   # chat_id -> claude session_id map, for --resume
        logs/           # reserved for file logging
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# A second/third bot is an "instance": its own state dir, config, lock, and service.
# The default (un-named) bot lives at ~/.claudebot; named instances live under
# ~/.claudebot/instances/<name>.
_INSTANCE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,30}$")


def validate_instance_name(name: str) -> str:
    if not _INSTANCE_RE.match(name):
        raise ValueError(
            f"invalid instance name {name!r}: lowercase letters/digits/'-'/'_', "
            "starting alphanumeric, max 31 chars."
        )
    return name


def instances_root() -> Path:
    return Path.home() / ".claudebot" / "instances"


def instance_state_dir(name: str) -> Path:
    return instances_root() / name


def list_instances() -> list[str]:
    root = instances_root()
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def state_dir() -> Path:
    """Base directory for all per-user state."""
    override = os.environ.get("CLAUDEBOT_STATE_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".claudebot"


def env_file() -> Path:
    return state_dir() / ".env"


def sessions_file() -> Path:
    return state_dir() / "sessions.json"


def overrides_file() -> Path:
    """Per-chat runtime setting overrides (set via /model, /timeout, … in chat)."""
    return state_dir() / "overrides.json"


def log_dir() -> Path:
    return state_dir() / "logs"


def ensure_state_dir() -> Path:
    """Create the state dir (private) and return it."""
    d = state_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        d.chmod(0o700)
    except OSError:
        pass
    return d
