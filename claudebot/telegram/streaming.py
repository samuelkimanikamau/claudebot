"""Render a Claude turn into Telegram as it streams.

UX rule (borrowed from the better Claude bots): own ONE live message and edit it
forward while Claude types, then on completion lay down the authoritative reply —
rendered as Telegram MarkdownV2 (bold, code, tables→monospace), chunked across
messages at the 4096-char limit.

Two safety properties:
* The live *preview* is sent as PLAIN text — formatting a half-finished message
  would routinely cut a Markdown entity and get rejected mid-stream.
* The final send tries MarkdownV2 first and falls back to plain text per chunk,
  so a reply is never lost to a formatting/parse error.
"""

from __future__ import annotations

import asyncio
import time

from telegram.error import BadRequest, RetryAfter, TelegramError

from claudebot.claude import events
from claudebot.claude.events import ClaudeEvent
from claudebot.core.config import Settings
from claudebot.core.logging import get_logger
from claudebot.telegram.format import to_telegram

log = get_logger("claudebot.telegram.stream")

_HARD_LIMIT = 4096          # Telegram's max message length
_RAW_LIMIT = 3500           # chunk raw markdown here; leaves room for MarkdownV2 escaping


def chunk_text(text: str, limit: int = _RAW_LIMIT) -> list[str]:
    """Split text into <=limit pieces, preferring newline boundaries."""
    text = text or ""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        cut = window.rfind("\n")
        if cut < int(limit * 0.5):  # no decent newline -> hard cut
            cut = limit
        chunks.append(rest[:cut])
        rest = rest[cut:]
        if rest.startswith("\n"):
            rest = rest[1:]
    if rest:
        chunks.append(rest)
    return chunks


class Streamer:
    def __init__(self, bot, chat_id: int, settings: Settings) -> None:
        self.bot = bot
        self.chat_id = chat_id
        self.edit_interval = settings.edit_interval
        self.stream_partials = settings.stream_partials
        self.markdown = settings.markdown
        self._buffer = ""
        self._live_id: int | None = None
        self._last_preview: str | None = None
        self._last_edit = 0.0
        self._finalized = False

    # --- live streaming (plain text) ----------------------------------------

    async def on_event(self, event: ClaudeEvent) -> None:
        if self._finalized:
            return
        if self.stream_partials and events.is_partial(event):
            delta = events.partial_text(event)
            if delta:
                self._buffer += delta
                await self._preview()
        elif not self.stream_partials and events.is_assistant(event):
            txt = events.assistant_text(event)
            if txt:
                self._buffer += txt
                await self._preview()  # respect edit_interval; finalize() lays down the truth

    async def _preview(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._last_edit) < self.edit_interval:
            return
        text = self._buffer
        if not text.strip():
            return
        shown = text if len(text) <= _HARD_LIMIT else "…" + text[-(_HARD_LIMIT - 1):]
        if shown == self._last_preview:
            return
        try:
            if self._live_id is None:
                msg = await self.bot.send_message(self.chat_id, shown)
                self._live_id = msg.message_id
            else:
                await self.bot.edit_message_text(
                    shown, chat_id=self.chat_id, message_id=self._live_id
                )
            self._last_preview = shown
            self._last_edit = now
        except RetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 0.5)
        except BadRequest:
            pass
        except TelegramError as exc:
            log.debug("preview error: %s", exc)

    # --- finalization (formatted, with plain fallback) ----------------------

    async def finalize(self, text: str) -> None:
        self._finalized = True
        raw = (text or "").strip() or "(no reply)"
        chunks = chunk_text(raw)
        self._live_id = await self._send(chunks[0], edit_id=self._live_id)
        for chunk in chunks[1:]:
            await self._send(chunk, edit_id=None)

    async def _send(self, raw: str, edit_id: int | None) -> int | None:
        """Send/edit one chunk: MarkdownV2 first, then plain text on rejection."""
        body, mode = to_telegram(raw) if self.markdown else (raw, None)
        attempts = [(body, mode)]
        if mode is not None:
            attempts.append((raw, None))  # plain fallback
        for send_body, parse_mode in attempts:
            try:
                return await self._deliver(send_body, parse_mode, edit_id)
            except BadRequest as exc:
                if "not modified" in str(exc).lower():
                    return edit_id
                continue  # parse/length error -> try the next (plain) attempt
            except RetryAfter as exc:
                await asyncio.sleep(exc.retry_after + 0.5)
                try:
                    return await self._deliver(send_body, parse_mode, edit_id)
                except TelegramError:
                    return edit_id
            except TelegramError as exc:
                log.debug("send error: %s", exc)
                return edit_id
        return edit_id

    async def _deliver(self, text: str, parse_mode: str | None, edit_id: int | None) -> int | None:
        if edit_id is None:
            msg = await self.bot.send_message(self.chat_id, text, parse_mode=parse_mode)
            return msg.message_id
        await self.bot.edit_message_text(
            text, chat_id=self.chat_id, message_id=edit_id, parse_mode=parse_mode
        )
        return edit_id

    async def error(self, message: str) -> None:
        self._finalized = True
        try:
            if self._live_id is not None:
                await self.bot.edit_message_text(
                    message, chat_id=self.chat_id, message_id=self._live_id
                )
            else:
                await self.bot.send_message(self.chat_id, message)
        except TelegramError:
            try:
                await self.bot.send_message(self.chat_id, message)
            except TelegramError:
                pass
