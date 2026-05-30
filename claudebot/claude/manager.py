"""Owns one ClaudeSession per Telegram chat and persists their session ids.

The chat_id -> session_id map is written to ``~/.claudebot/sessions.json`` so that
when the bot restarts (or the service relaunches it), each chat resumes its prior
Claude conversation with ``--resume`` instead of starting blank.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from claudebot.claude.session import ClaudeSession
from claudebot.core.config import Settings
from claudebot.core.logging import get_logger
from claudebot.core.paths import ensure_state_dir, sessions_file

log = get_logger("claudebot.manager")


class SessionManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._sessions: dict[int, ClaudeSession] = {}
        self._map: dict[str, str] = self._load_map()
        self._lock = asyncio.Lock()
        self._idle_task: asyncio.Task | None = None

    # --- lookup -------------------------------------------------------------

    async def get(self, chat_id: int) -> ClaudeSession:
        async with self._lock:
            session = self._sessions.get(chat_id)
            if session is not None:
                return session

            known_id = self._map.get(str(chat_id))
            session = ClaudeSession(
                chat_id,
                self.settings,
                session_id=known_id,
                is_new=known_id is None,
            )
            self._sessions[chat_id] = session
            if known_id is None:
                self._remember(chat_id, session.session_id)
            return session

    async def reset(self, chat_id: int, working_dir: Path | None = None) -> ClaudeSession:
        """Start a fresh Claude conversation for this chat (used by /new and /cd)."""
        async with self._lock:
            old = self._sessions.pop(chat_id, None)
            if old is not None:
                await old.stop()
            session = ClaudeSession(
                chat_id,
                self.settings,
                session_id=None,
                is_new=True,
                working_dir=working_dir,
            )
            self._sessions[chat_id] = session
            self._remember(chat_id, session.session_id)
            return session

    # --- lifecycle ----------------------------------------------------------

    def start_background(self) -> None:
        if self._idle_task is None and self.settings.idle_timeout > 0:
            self._idle_task = asyncio.create_task(self._idle_loop())

    async def shutdown(self) -> None:
        if self._idle_task is not None:
            self._idle_task.cancel()
            self._idle_task = None
        await asyncio.gather(
            *(s.stop() for s in self._sessions.values()), return_exceptions=True
        )

    async def _idle_loop(self) -> None:
        import time

        timeout = self.settings.idle_timeout
        try:
            while True:
                await asyncio.sleep(60)
                now = time.monotonic()
                for session in list(self._sessions.values()):
                    if (
                        session.is_alive
                        and not session.busy
                        and (now - session.last_activity) > timeout
                    ):
                        log.info("chat %s: evicting idle claude child", session.chat_id)
                        await session.stop()
        except asyncio.CancelledError:
            pass

    # --- persistence --------------------------------------------------------

    def _remember(self, chat_id: int, session_id: str) -> None:
        self._map[str(chat_id)] = session_id
        self._save_map()

    def _load_map(self) -> dict[str, str]:
        path = sessions_file()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text("utf-8"))
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("could not read %s: %s", path, exc)
        return {}

    def _save_map(self) -> None:
        ensure_state_dir()
        path = sessions_file()
        tmp = path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(self._map, indent=2), "utf-8")
            tmp.replace(path)
        except OSError as exc:
            log.warning("could not write %s: %s", path, exc)
