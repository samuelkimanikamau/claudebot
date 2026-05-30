"""A long-lived ``claude`` child process bridged to one Telegram chat.

Architecture (research Approach A — "own the loop"):

    one Telegram chat  ->  one ClaudeSession  ->  one persistent
                           `claude -p --input-format stream-json
                                   --output-format stream-json ...` child

We write each user turn to the child's stdin as a stream-json envelope and read
events off its stdout until the turn's ``result`` arrives. The child stays warm
between turns (context lives in its memory — no transcript re-read), and on crash
or idle-eviction we transparently respawn with ``--resume <session_id>`` so the
conversation continues.

The subscription credential never leaves the real ``claude`` binary — we only
pipe text in and JSON out — which is what keeps this inside Anthropic's supported
usage (no API key, not the Agent SDK).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from claudebot.claude import events
from claudebot.claude.events import ClaudeEvent
from claudebot.core.config import Settings
from claudebot.core.logging import get_logger

log = get_logger("claudebot.claude")

# StreamReader line buffer. A single assistant/result line can be large.
_MAX_LINE = 16 * 1024 * 1024
# Sentinel placed on the event queue when the child's stdout closes (it exited).
_EOF = object()

EventCallback = Callable[[ClaudeEvent], Awaitable[None]]


class _ChildGone(Exception):
    """The claude child exited mid-turn; the caller should respawn and retry."""


@dataclass(slots=True)
class TurnResult:
    text: str
    session_id: str
    is_error: bool = False
    cost: float | None = None
    tools_used: list[str] = field(default_factory=list)


class ClaudeSession:
    def __init__(
        self,
        chat_id: int,
        settings: Settings,
        *,
        session_id: str | None = None,
        is_new: bool = True,
        working_dir: Path | None = None,
    ) -> None:
        self.chat_id = chat_id
        self.settings = settings
        self.session_id = session_id or str(uuid.uuid4())
        self.working_dir = working_dir or settings.working_dir
        self.last_activity = time.monotonic()

        self._is_new = is_new
        self._ever_started = False
        self._interrupted = False
        self._proc: asyncio.subprocess.Process | None = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._last_stderr = ""
        self._lock = asyncio.Lock()

    # --- public API ---------------------------------------------------------

    @property
    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    async def ask(
        self,
        text: str,
        *,
        on_event: EventCallback | None = None,
        image_paths: Sequence[Path] | None = None,
    ) -> TurnResult:
        """Send one user turn and return Claude's reply. Serialized per chat."""
        async with self._lock:
            if not self.is_alive:
                await self._respawn()
            try:
                return await self._run_turn(text, on_event, image_paths)
            except (_ChildGone, BrokenPipeError, ConnectionResetError):
                if self._interrupted:
                    self._interrupted = False
                    return TurnResult(text="🛑 Stopped.", session_id=self.session_id)
                log.warning("chat %s: child gone, respawning with --resume", self.chat_id)
                await self._respawn()
                return await self._run_turn(text, on_event, image_paths)
            finally:
                self.last_activity = time.monotonic()

    async def stop(self) -> None:
        """Kill the child but keep ``session_id`` so the next ask() resumes."""
        await self._kill_proc()

    async def interrupt(self) -> None:
        """Abort the in-flight turn (used by /stop). Next message resumes context."""
        self._interrupted = True
        await self._kill_proc()

    # --- turn loop ----------------------------------------------------------

    async def _run_turn(
        self,
        text: str,
        on_event: EventCallback | None,
        image_paths: Sequence[Path] | None,
    ) -> TurnResult:
        await self._write_user(text, image_paths)

        collected: list[str] = []
        tools: list[str] = []
        result_event: ClaudeEvent | None = None

        while True:
            item = await self._queue.get()
            if item is _EOF:
                raise _ChildGone()
            event: ClaudeEvent = item
            if on_event is not None:
                try:
                    await on_event(event)
                except Exception as exc:  # never let UI errors break the turn
                    log.debug("chat %s: on_event error: %s", self.chat_id, exc)
            if events.is_assistant(event):
                collected.append(events.assistant_text(event))
                tools.extend(events.assistant_tool_names(event))
            elif events.is_result(event):
                result_event = event
                break

        if result_event is None:  # pragma: no cover - loop only exits on result/_EOF
            raise _ChildGone()

        final = events.result_text(result_event) or "".join(collected)
        return TurnResult(
            text=final.strip(),
            session_id=self.session_id,
            is_error=events.result_is_error(result_event),
            cost=events.result_cost(result_event),
            tools_used=tools,
        )

    async def _write_user(self, text: str, image_paths: Sequence[Path] | None) -> None:
        content: list[dict] = []
        for p in image_paths or []:
            # Same machine — point Claude at the file and let it Read it.
            content.append(
                {"type": "text", "text": f"[The user attached a file. Read it: {p}]"}
            )
        content.append({"type": "text", "text": text})
        envelope = {"type": "user", "message": {"role": "user", "content": content}}
        data = (json.dumps(envelope) + "\n").encode("utf-8")

        proc = self._proc
        if proc is None or proc.stdin is None or proc.returncode is not None:
            raise _ChildGone()
        proc.stdin.write(data)
        await proc.stdin.drain()

    # --- process lifecycle --------------------------------------------------

    async def _respawn(self) -> None:
        await self._kill_proc()
        # First ever start of a brand-new session sets the id with --session-id;
        # every other start (loaded-from-disk, crash, idle-evict) uses --resume.
        resume = self._ever_started or not self._is_new
        await self._start(resume=resume)
        self._ever_started = True

    async def _start(self, *, resume: bool) -> None:
        args = self._build_args(resume=resume)
        log.info(
            "chat %s: spawning claude (%s %s)",
            self.chat_id,
            "resume" if resume else "new",
            self.session_id,
        )
        self._proc = await asyncio.create_subprocess_exec(
            self.settings.claude_binary,
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.working_dir),
            env=self._child_env(),
            limit=_MAX_LINE,
        )
        self._queue = asyncio.Queue()
        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())

    def _build_args(self, *, resume: bool) -> list[str]:
        s = self.settings
        args = [
            "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",
            "--replay-user-messages",
            "--permission-mode", s.permission_mode,
        ]
        if resume:
            args += ["--resume", self.session_id]
        else:
            args += ["--session-id", self.session_id]
        if s.stream_partials:
            args += ["--include-partial-messages"]
        if s.model:
            args += ["--model", s.model]
        if s.effort:
            args += ["--effort", s.effort]
        if s.append_system_prompt:
            args += ["--append-system-prompt", s.append_system_prompt]
        if s.system_prompt_file:
            args += ["--append-system-prompt-file", str(s.system_prompt_file)]
        if s.allowed_tools:
            args += ["--allowedTools", *s.allowed_tools]
        if s.disallowed_tools:
            args += ["--disallowedTools", *s.disallowed_tools]
        return args

    def _child_env(self) -> dict[str, str]:
        env = dict(os.environ)
        # Force the SUBSCRIPTION path: strip API-key auth so the child falls back
        # to the logged-in `claude` OAuth/keychain creds (no key, no API billing).
        for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            env.pop(key, None)
        # Keep Claude's own tool subprocesses non-interactive so a turn can't
        # wedge on a pager or a credential prompt.
        env.setdefault("GIT_TERMINAL_PROMPT", "0")
        env.setdefault("GIT_PAGER", "cat")
        env.setdefault("PAGER", "cat")
        env.setdefault("PYTHONUNBUFFERED", "1")
        return env

    async def _read_stdout(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                event = events.parse_line(line.decode("utf-8", "replace"))
                if event is None:
                    continue
                if event.session_id:
                    self.session_id = event.session_id  # authoritative id
                await self._queue.put(event)
        except Exception as exc:
            log.debug("chat %s: stdout reader stopped: %s", self.chat_id, exc)
        finally:
            await self._queue.put(_EOF)

    async def _read_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace").rstrip()
                if text:
                    self._last_stderr = text
                    log.debug("chat %s [claude stderr]: %s", self.chat_id, text)
        except Exception:
            pass

    async def _kill_proc(self) -> None:
        proc, self._proc = self._proc, None
        for task in (self._reader_task, self._stderr_task):
            if task is not None:
                task.cancel()
        self._reader_task = self._stderr_task = None
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        except ProcessLookupError:
            pass
