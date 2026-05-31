"""Render a Claude turn into Telegram as it streams — progressively, head-first.

The reply is streamed into a series of stable per-block messages (one per ~3500
chars, the SAME boundary finalize() uses). The user reads it from the TOP, growing
downward; nothing is shown as a throwaway tail, and finalize() never jumps back to
the start — it just upgrades each existing block bubble from plain text to
MarkdownV2 in place. So what you watched stream is exactly what you keep.

A background ``_preview_worker`` throttles the live edits off the stdout-read path,
and the final send tries MarkdownV2 first with a plain-text fallback per block, so
a reply is never lost to a formatting/parse error.
"""

from __future__ import annotations

import asyncio
import contextlib
import time

from telegram.error import BadRequest, RetryAfter, TelegramError

from claudebot.claude import events
from claudebot.claude.events import ClaudeEvent
from claudebot.core.config import Settings
from claudebot.core.logging import get_logger
from claudebot.telegram.format import to_telegram

log = get_logger("claudebot.telegram.stream")

_HARD_LIMIT = 4096          # Telegram's max message length
_RAW_LIMIT = 3500           # block/chunk boundary; leaves room for MarkdownV2 escaping


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
        # One Telegram message per streamed block; _block_last[i] is the plain text
        # last shown in block i (to skip 'not modified' edits).
        self._block_ids: list[int] = []
        self._block_last: list[str] = []
        self._last_edit = 0.0
        self._finalized = False
        self._preview_task: asyncio.Task | None = None

    # --- live streaming (plain text, head-anchored blocks) ------------------

    async def on_event(self, event: ClaudeEvent) -> None:
        if self._finalized:
            return
        if self.stream_partials and events.is_partial(event):
            delta = events.partial_text(event)
            if delta:
                self._buffer += delta
                self._schedule_preview()
        elif not self.stream_partials and events.is_assistant(event):
            txt = events.assistant_text(event)
            if txt:
                self._buffer += txt
                self._schedule_preview()  # background worker respects edit_interval

    def _schedule_preview(self) -> None:
        """Kick a background preview edit without blocking Claude stdout reads."""
        if self._finalized:
            return
        if self._preview_task is None or self._preview_task.done():
            self._preview_task = asyncio.create_task(self._preview_worker())

    async def _preview_worker(self) -> None:
        """Throttle live Telegram edits independently of Claude event consumption."""
        try:
            while not self._finalized:
                delay = self.edit_interval - (time.monotonic() - self._last_edit)
                if delay > 0:
                    await asyncio.sleep(delay)
                await self._preview_now()
                if self._finalized or self._preview_blocks() == self._block_last:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - preview failures must never break turns
            log.debug("preview worker error: %s", exc)
        finally:
            if self._preview_task is asyncio.current_task():
                self._preview_task = None

    def _preview_blocks(self) -> list[str]:
        """Split the buffer into blocks using the SAME boundary finalize() uses."""
        if not self._buffer.strip():
            return []
        return chunk_text(self._buffer, _RAW_LIMIT)

    async def _preview_now(self) -> None:
        # A worker that slipped past the loop guard must not edit after finalize().
        if self._finalized:
            return
        blocks = self._preview_blocks()
        if not blocks:
            return
        try:
            for i, blk in enumerate(blocks):
                if i < len(self._block_ids):
                    if self._block_last[i] == blk:
                        continue  # unchanged -> skip ('message is not modified')
                    await self.bot.edit_message_text(
                        blk, chat_id=self.chat_id, message_id=self._block_ids[i]
                    )
                    self._block_last[i] = blk
                else:
                    msg = await self.bot.send_message(self.chat_id, blk)
                    self._block_ids.append(msg.message_id)
                    self._block_last.append(blk)
            self._last_edit = time.monotonic()
        except RetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 0.5)
        except BadRequest:
            pass
        except TelegramError as exc:
            log.debug("preview error: %s", exc)

    async def _cancel_preview(self) -> None:
        task, self._preview_task = self._preview_task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    # --- finalization (in-place MarkdownV2 upgrade per block) ---------------

    async def finalize(self, text: str) -> None:
        self._finalized = True
        await self._cancel_preview()
        raw = (text or "").strip() or "(no reply)"
        chunks = chunk_text(raw)  # same _RAW_LIMIT boundary as the streamed blocks
        for i, chunk in enumerate(chunks):
            if i < len(self._block_ids):
                # Upgrade the block the user already watched: plain -> MarkdownV2, in place.
                await self._send(chunk, edit_id=self._block_ids[i])
            else:
                msg_id = await self._send(chunk, edit_id=None)
                if msg_id is not None:
                    self._block_ids.append(msg_id)
                    self._block_last.append(chunk)
        # Reply ended up SHORTER than what streamed -> blank the leftover bubbles
        # so a stale streamed tail isn't left behind.
        for j in range(len(chunks), len(self._block_ids)):
            with contextlib.suppress(TelegramError):
                await self.bot.edit_message_text(
                    "…", chat_id=self.chat_id, message_id=self._block_ids[j]
                )

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
        await self._cancel_preview()
        try:
            if self._block_ids:
                await self.bot.edit_message_text(
                    message, chat_id=self.chat_id, message_id=self._block_ids[0]
                )
            else:
                await self.bot.send_message(self.chat_id, message)
        except TelegramError:
            with contextlib.suppress(TelegramError):
                await self.bot.send_message(self.chat_id, message)
