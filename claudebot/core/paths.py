"""Filesystem locations for claudebot's per-user state.

Everything lives under ``~/.claudebot`` (override with ``CLAUDEBOT_STATE_DIR``):

    ~/.claudebot/
        .env            # config written by the setup wizard (chmod 600)
        sessions.json   # chat_id -> claude session_id map, for --resume
        logs/           # reserved for file logging
"""

from __future__ import annotations

import os
from pathlib import Path


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
