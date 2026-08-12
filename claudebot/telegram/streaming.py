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
    """Split text into <=limit pieces, preferring newline boundaries.

    Chunks are fence-balanced: a ``` code block that spans a boundary is closed
    at the end of one chunk and reopened (same language) at the start of the
    next, so each Telegram message converts to MarkdownV2 on its own instead of
    rendering the continuation as mangled half-fence soup.
    """
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
    return _balance_fences(chunks)


def _balance_fences(chunks: list[str]) -> list[str]:
    """Close/reopen ``` fences at chunk boundaries so every chunk stands alone."""
    open_info: str | None = None  # info string of the fence open at this point
    balanced: list[str] = []
    for chunk in chunks:
        was_open = open_info
        for line in chunk.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("```"):
                open_info = stripped[3:].strip() if open_info is None else None
        if was_open is not None:
            chunk = f"```{was_open}\n{chunk}"
        if open_info is not None:
            chunk = f"{chunk}\n```"
        balanced.append(chunk)
    return balanced


def _thinking_tail(text: str, limit: int = 160) -> str:
    """The last ~limit chars of the thinking, compacted to one line."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    tail = text[-limit:]
    cut = tail.find(" ")
    if 0 <= cut < limit // 2:  # start at a word boundary when one is near
        tail = tail[cut + 1 :]
    return "…" + tail


class Streamer:
    def __init__(self, bot, chat_id: int, settings: Settings, *, live_markup=None) -> None:
        self.bot = bot
        self.chat_id = chat_id
        self.edit_interval = settings.edit_interval
        self.stream_partials = settings.stream_partials
        self.show_thinking = settings.show_thinking
        self.markdown = settings.markdown
        # Inline keyboard (e.g. a Stop button) shown on the FIRST streamed block
        # while the turn is live; cleared again on finalize()/error().
        self._live_markup = live_markup
        self._buffer = ""
        # Live activity line: "💭 …" while Claude reasons, "🔧 Bash…" while it
        # runs tools — the otherwise-silent phases of a turn. Cleared as soon
        # as new reply text streams.
        self._status = ""
        self._thinking = ""  # current thinking block, for the 💭 tail
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
                self._status = ""
                self._thinking = ""
                self._schedule_preview()
            elif self.show_thinking:
                think = events.partial_thinking(event)
                if think:
                    self._thinking += think
                    self._status = "💭 " + _thinking_tail(self._thinking)
                    self._schedule_preview()
        elif not self.stream_partials and events.is_assistant(event):
            txt = events.assistant_text(event)
            if txt:
                self._buffer += txt
                self._status = ""
                self._thinking = ""
                self._schedule_preview()  # background worker respects edit_interval
        if events.is_assistant(event):
            # A tool_use message means a tool phase is starting — often the
            # longest, otherwise-silent part of a turn. Surface it.
            names = list(dict.fromkeys(events.assistant_tool_names(event)))
            if names:
                self._status = "🔧 " + ", ".join(names)[:200] + "…"
                self._thinking = ""  # a new thinking block may follow the tools
                self._schedule_preview()

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
        """Split the buffer into blocks using the SAME boundary finalize() uses.

        The tool-status line rides on the tail block (or stands alone before any
        text has streamed); it stays within the 4096 hard limit because blocks
        are cut at 3500 and the status is capped short."""
        blocks = chunk_text(self._buffer, _RAW_LIMIT) if self._buffer.strip() else []
        if self._status:
            if blocks:
                blocks[-1] = f"{blocks[-1]}\n\n{self._status}"
            else:
                blocks = [self._status]
        return blocks

    async def _preview_now(self) -> None:
        # A worker that slipped past the loop guard must not edit after finalize().
        if self._finalized:
            return
        blocks = self._preview_blocks()
        if not blocks:
            return
        # Stamp BEFORE the network calls: the throttle then paces pass *starts*
        # (a true edit_interval cadence instead of interval + round-trips), and
        # a persistently failing edit below can't hot-loop the worker.
        self._last_edit = time.monotonic()
        for i, blk in enumerate(blocks):
            # Stop button rides on block 0 only (omit the kwarg entirely when
            # unused so test fakes with narrow signatures keep working).
            kw = {"reply_markup": self._live_markup} if i == 0 and self._live_markup else {}
            try:
                if i < len(self._block_ids):
                    if self._block_last[i] == blk:
                        continue  # unchanged -> skip ('message is not modified')
                    await self.bot.edit_message_text(
                        blk, chat_id=self.chat_id, message_id=self._block_ids[i], **kw
                    )
                    self._block_last[i] = blk
                else:
                    msg = await self.bot.send_message(self.chat_id, blk, **kw)
                    self._block_ids.append(msg.message_id)
                    self._block_last.append(blk)
            except RetryAfter as exc:
                await asyncio.sleep(exc.retry_after + 0.5)
                return  # finish this content on the next pass
            except BadRequest:
                if i < len(self._block_last):
                    # 'message is not modified', or the user deleted this bubble
                    # ('message to edit not found') — record the content as shown
                    # so the worker doesn't re-attempt the same edit every pass.
                    self._block_last[i] = blk
                else:
                    return  # a failed SEND has no bubble; don't send later blocks out of order
            except TelegramError as exc:
                log.debug("preview error: %s", exc)
                return  # transient network trouble — retry on the next pass

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
        # Upgrade the blocks the user already watched (plain -> MarkdownV2) in
        # place, CONCURRENTLY — each edit targets its own message id, so a
        # multi-block reply settles in one round-trip instead of N. The rate
        # limiter paces the actual HTTP calls; _send never raises TelegramError.
        upgrades = [
            self._send(chunk, edit_id=self._block_ids[i])
            for i, chunk in enumerate(chunks[: len(self._block_ids)])
        ]
        if upgrades:
            await asyncio.gather(*upgrades, return_exceptions=True)
        # NEW trailing blocks must be sent one by one, in order — the previous
        # send's completion determines chat ordering.
        for chunk in chunks[len(self._block_ids):]:
            msg_id = await self._send(chunk, edit_id=None)
            if msg_id is not None:
                self._block_ids.append(msg_id)
                self._block_last.append(chunk)
        # Reply ended up SHORTER than what streamed -> delete the leftover bubbles
        # so a stale streamed tail (or a stranded "…") isn't left behind.
        for j in range(len(chunks), len(self._block_ids)):
            with contextlib.suppress(TelegramError):
                await self.bot.delete_message(
                    chat_id=self.chat_id, message_id=self._block_ids[j]
                )
        del self._block_ids[len(chunks):]
        del self._block_last[len(chunks):]
        await self._clear_live_markup()

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
        # Drop any later streamed blocks so stale partial content isn't left behind.
        for mid in self._block_ids[1:]:
            with contextlib.suppress(TelegramError):
                await self.bot.delete_message(chat_id=self.chat_id, message_id=mid)
        del self._block_ids[1:]
        del self._block_last[1:]
        await self._clear_live_markup()

    async def _clear_live_markup(self) -> None:
        """Drop the live Stop keyboard from block 0 once the turn is over.

        The finalize edit usually clears it implicitly, but a 'message is not
        modified' outcome (final text == streamed text) would leave it stranded.
        """
        if self._live_markup is None or not self._block_ids:
            return
        with contextlib.suppress(TelegramError):
            await self.bot.edit_message_reply_markup(
                chat_id=self.chat_id, message_id=self._block_ids[0], reply_markup=None
            )
