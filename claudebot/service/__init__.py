"""Always-alive supervision: install claudebot as a user service.

Linux  -> systemd --user unit (Restart=always) + linger guidance.
macOS  -> launchd LaunchAgent (KeepAlive + RunAtLoad).

Everything is user-level — no root, no sudo, no port binding.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


class UnsupportedPlatform(RuntimeError):
    pass


class ServiceManager:
    """Base class with the bits shared by systemd and launchd back-ends."""

    name = "claudebot"

    def __init__(self, claude_binary: str = "claude") -> None:
        self.claude_binary = claude_binary

    # The command the service runs. Using the current interpreter + ``-m`` makes
    # it venv-correct and PATH-independent.
    def bot_argv(self) -> list[str]:
        return [sys.executable, "-m", "claudebot", "run"]

    def path_env(self) -> str:
        """A PATH that definitely contains the ``claude`` binary and uv/node shims."""
        parts: list[str] = []
        found = shutil.which(self.claude_binary)
        if found:
            parts.append(str(Path(found).resolve().parent))
        home = Path.home()
        for p in (
            "/opt/homebrew/bin",
            "/usr/local/bin",
            home / ".local/bin",
            home / ".npm-global/bin",
            "/usr/bin",
            "/bin",
        ):
            parts.append(str(p))
        # de-dup, keep order
        seen: set[str] = set()
        ordered = [p for p in parts if not (p in seen or seen.add(p))]
        return ":".join(ordered)

    def extra_env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        # Carry a non-default state dir into the service so it finds the same config.
        if os.environ.get("CLAUDEBOT_STATE_DIR"):
            env["CLAUDEBOT_STATE_DIR"] = os.environ["CLAUDEBOT_STATE_DIR"]
        return env

    def installed_python(self) -> str | None:
        """The interpreter the installed unit runs, parsed from disk (or None)."""
        return None

    # Subclasses implement these.
    def install(self) -> None: ...      # noqa: D401,E704
    def uninstall(self) -> None: ...    # noqa: E704
    def start(self) -> None: ...        # noqa: E704
    def stop(self) -> None: ...         # noqa: E704
    def restart(self) -> None: ...      # noqa: E704
    def status(self) -> str: ...        # noqa: E704
    def logs(self, follow: bool = False) -> None: ...  # noqa: E704


def get_service_manager(claude_binary: str = "claude") -> ServiceManager:
    if sys.platform == "darwin":
        from claudebot.service.launchd import LaunchdService

        return LaunchdService(claude_binary)
    if sys.platform.startswith("linux"):
        from claudebot.service.systemd import SystemdService

        return SystemdService(claude_binary)
    raise UnsupportedPlatform(
        f"No service backend for platform {sys.platform!r}. "
        "Run `claudebot run` under your own supervisor (pm2, docker, tmux, …)."
    )
