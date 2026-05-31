"""claudebot — an always-on Telegram bot that drives the real Claude Code binary.

It talks to the genuine ``claude`` executable over its headless ``stream-json``
protocol on your logged-in Pro/Max subscription. No API key, no Agent SDK — the
OAuth token never leaves Claude Code, which keeps the setup inside Anthropic's
supported usage (see README "Is this allowed?").
"""

from __future__ import annotations

import subprocess
from pathlib import Path

__version__ = "0.1.0"


def version_string() -> str:
    """``__version__`` plus the short git commit, when running from a checkout.

    Lets you tell exactly which build is deployed (e.g. after ``claudebot update``)
    from ``--version`` and ``/status``.
    """
    try:
        root = Path(__file__).resolve().parent.parent
        if (root / ".git").exists():
            sha = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                timeout=2,
            ).stdout.strip()
            if sha:
                return f"{__version__}+{sha}"
    except Exception:  # noqa: BLE001 - version display must never crash
        pass
    return __version__
