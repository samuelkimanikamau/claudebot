import asyncio
import json

from telegram.error import BadRequest

from claudebot.claude.events import parse_line
from claudebot.core.config import Settings
from claudebot.telegram.streaming import Streamer


class FakeMessage:
    def __init__(self, message_id: int) -> None:
        self.message_id = message_id


class SlowPreviewBot:
    def __init__(self) -> None:
        self.preview_started = asyncio.Event()
        self.release_preview = asyncio.Event()
        self.sent: list[tuple[str, str | None]] = []
        self.edited: list[tuple[str, str | None, int | None]] = []
        self._next_id = 100

    async def send_message(self, chat_id, text, parse_mode=None):
        if parse_mode is None and text.startswith("preview"):
            self.preview_started.set()
            await self.release_preview.wait()
        self.sent.append((text, parse_mode))
        self._next_id += 1
        return FakeMessage(self._next_id)

    async def edit_message_text(self, text, chat_id, message_id, parse_mode=None):
        if parse_mode is None and text.startswith("preview"):
            self.preview_started.set()
            await self.release_preview.wait()
        self.edited.append((text, parse_mode, message_id))


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, telegram_bot_token="123:abc", allowed_user_ids="1", **kw)


def _partial(text: str):
    return parse_line(
        json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": text},
                },
            }
        )
    )


async def test_on_event_does_not_wait_for_slow_preview_send():
    bot = SlowPreviewBot()
    streamer = Streamer(bot, 1, _settings(edit_interval=0))

    await asyncio.wait_for(streamer.on_event(_partial("preview one")), timeout=0.05)
    await asyncio.wait_for(bot.preview_started.wait(), timeout=0.1)

    assert streamer._buffer == "preview one"
    # Cleanup the background preview task so pytest does not leave it pending.
    bot.release_preview.set()
    await asyncio.sleep(0)


async def test_finalize_cancels_pending_preview_and_sends_authoritative_reply():
    bot = SlowPreviewBot()
    streamer = Streamer(bot, 1, _settings(edit_interval=0, markdown=False))

    preview_task = asyncio.create_task(streamer.on_event(_partial("preview two")))
    await asyncio.wait_for(bot.preview_started.wait(), timeout=0.1)

    await asyncio.wait_for(streamer.finalize("final answer"), timeout=0.1)

    assert ("final answer", None) in bot.sent
    assert preview_task.done()
    assert not any(text == "preview two" for text, _mode in bot.sent)

    # Cleanup if the old implementation is under test and left the preview blocked.
    bot.release_preview.set()
    if not preview_task.done():
        await asyncio.wait_for(preview_task, timeout=0.1)


class RecordBot:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str | None, int]] = []
        self.edited: list[tuple[str, str | None, int]] = []
        self.deleted: list[int] = []
        self.send_markups: list[object] = []  # reply_markup per send, in order
        self.markup_cleared: list[int] = []  # message_ids whose keyboard was removed
        self._next = 0

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):
        self._next += 1
        self.sent.append((text, parse_mode, self._next))
        self.send_markups.append(reply_markup)
        return FakeMessage(self._next)

    async def edit_message_text(self, text, chat_id, message_id, parse_mode=None, reply_markup=None):
        self.edited.append((text, parse_mode, message_id))

    async def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        self.markup_cleared.append(message_id)

    async def delete_message(self, chat_id, message_id):
        self.deleted.append(message_id)


async def test_progressive_streaming_preserves_head_no_reorder():
    bot = RecordBot()
    streamer = Streamer(bot, 1, _settings(edit_interval=0, markdown=False))
    # A 2-block buffer (chunk boundary is 3500).
    streamer._buffer = "A" * 3500 + "\n" + "B" * 1000
    await streamer._preview_now()
    assert len(streamer._block_ids) == 2
    head_id = streamer._block_ids[0]
    assert bot.sent[0][0].startswith("A")  # head streamed first
    assert bot.sent[1][0].startswith("B")

    await streamer.finalize(streamer._buffer)
    # finalize upgrades blocks IN PLACE: head keeps its message id, no new head send.
    assert streamer._block_ids[0] == head_id
    assert any(mid == head_id and text.startswith("A") for text, _m, mid in bot.edited)
    assert len(bot.sent) == 2  # no extra head message created at finalize


async def test_stop_button_rides_first_block_and_clears_on_finalize():
    bot = RecordBot()
    button = object()  # opaque markup — Streamer must not introspect it
    streamer = Streamer(bot, 1, _settings(edit_interval=0, markdown=False), live_markup=button)
    streamer._buffer = "A" * 3500 + "\n" + "B" * 1000  # 2 blocks
    await streamer._preview_now()
    assert bot.send_markups[0] is button  # block 0 carries the Stop button
    assert bot.send_markups[1] is None  # later blocks don't
    await streamer.finalize(streamer._buffer)
    assert streamer._block_ids[0] in bot.markup_cleared  # button removed when done


async def test_error_replaces_head_and_deletes_later_blocks():
    bot = RecordBot()
    streamer = Streamer(bot, 1, _settings(edit_interval=0, markdown=False))
    streamer._buffer = "A" * 3500 + "\n" + "B" * 1000  # streamed as 2 blocks
    await streamer._preview_now()
    head_id, stale_id = streamer._block_ids
    await streamer.error("⚠️ boom")
    # The head bubble shows the error; the trailing partial bubble is removed.
    assert any(text == "⚠️ boom" and mid == head_id for text, _m, mid in bot.edited)
    assert stale_id in bot.deleted
    assert streamer._block_ids == [head_id]


async def test_finalize_deletes_leftover_blocks_when_reply_shrinks():
    bot = RecordBot()
    streamer = Streamer(bot, 1, _settings(edit_interval=0, markdown=False))
    streamer._buffer = "A" * 3500 + "\n" + "B" * 1000  # streamed as 2 blocks
    await streamer._preview_now()
    assert len(streamer._block_ids) == 2
    leftover_id = streamer._block_ids[1]
    # Final reply is short -> 1 chunk; the 2nd streamed bubble must be deleted,
    # not left behind as a stranded "…".
    await streamer.finalize("short final")
    assert leftover_id in bot.deleted
    assert not any(text == "…" for text, _m, _mid in bot.edited)
    # Internal block state is trimmed to match the bubbles that remain.
    assert streamer._block_ids == [leftover_id - 1]
    assert len(streamer._block_last) == 1


def _assistant_tools(*names: str):
    return parse_line(
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": f"t{i}", "name": n, "input": {}}
                        for i, n in enumerate(names)
                    ],
                },
            }
        )
    )


async def test_tool_status_streams_before_any_text_and_clears_on_text():
    bot = RecordBot()
    streamer = Streamer(bot, 1, _settings(edit_interval=0, markdown=False))
    await streamer.on_event(_assistant_tools("Bash", "Read", "Bash"))
    await asyncio.sleep(0.05)  # let the background preview worker run
    # The status stands alone (deduped names, spinner + elapsed appended).
    assert any(text.startswith("🔧 Bash, Read ") for text, _m, _mid in bot.sent)
    # New text clears the status; the same bubble is edited to the text alone.
    await streamer.on_event(_partial("hello"))
    await asyncio.sleep(0.05)
    assert streamer._status == ""
    assert any(text == "hello" for text, _m, _mid in bot.edited)


async def test_tool_status_rides_on_the_tail_block_after_text():
    bot = RecordBot()
    streamer = Streamer(bot, 1, _settings(edit_interval=0, markdown=False))
    streamer._buffer = "some streamed text"
    streamer._status = "🔧 Bash…"
    assert streamer._preview_blocks() == ["some streamed text\n\n🔧 Bash…"]


async def test_failed_edit_is_recorded_not_hot_retried():
    class DeletedBubbleBot(RecordBot):
        def __init__(self) -> None:
            super().__init__()
            self.edit_attempts = 0

        async def edit_message_text(self, text, chat_id, message_id, parse_mode=None, reply_markup=None):
            self.edit_attempts += 1
            raise BadRequest("message to edit not found")

    bot = DeletedBubbleBot()
    streamer = Streamer(bot, 1, _settings(edit_interval=0, markdown=False))
    streamer._buffer = "one"
    await streamer._preview_now()  # first pass sends the bubble
    streamer._buffer = "one two"
    await streamer._preview_now()  # edit fails -> content recorded as shown
    assert bot.edit_attempts == 1
    assert streamer._block_last[0] == "one two"
    assert streamer._last_edit > 0  # throttle clock stamped despite the failure
    await streamer._preview_now()  # unchanged -> the failing edit is NOT retried
    assert bot.edit_attempts == 1


async def test_finalize_upgrades_existing_blocks_concurrently():
    class SlowEditBot(RecordBot):
        def __init__(self) -> None:
            super().__init__()
            self.inflight = 0
            self.max_inflight = 0

        async def edit_message_text(self, text, chat_id, message_id, parse_mode=None, reply_markup=None):
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
            await asyncio.sleep(0.01)  # simulate the Telegram round-trip
            self.inflight -= 1
            self.edited.append((text, parse_mode, message_id))

    bot = SlowEditBot()
    streamer = Streamer(bot, 1, _settings(edit_interval=0, markdown=False))
    streamer._buffer = "A" * 3500 + "\n" + "B" * 3500 + "\n" + "C" * 1000
    await streamer._preview_now()
    assert len(streamer._block_ids) == 3
    await streamer.finalize(streamer._buffer + " done")
    # The in-place upgrades overlap instead of paying one round-trip per block.
    assert bot.max_inflight >= 2


def _thinking(text: str):
    return parse_line(
        json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "thinking_delta", "thinking": text},
                },
            }
        )
    )


async def test_thinking_streams_as_status_and_clears_on_text():
    bot = RecordBot()
    streamer = Streamer(bot, 1, _settings(edit_interval=0, markdown=False))
    await streamer.on_event(_thinking("The user wants X, so I should check Y"))
    await asyncio.sleep(0.05)
    assert any(
        text.startswith("💭 ") and text.endswith("check Y") for text, _m, _mid in bot.sent
    )
    # Reply text starting clears the status; the bubble becomes the text alone.
    await streamer.on_event(_partial("Answer:"))
    await asyncio.sleep(0.05)
    assert streamer._status == ""
    assert streamer._thinking == ""


async def test_thinking_preview_can_be_disabled():
    bot = RecordBot()
    streamer = Streamer(bot, 1, _settings(edit_interval=0, markdown=False, show_thinking=False))
    await streamer.on_event(_thinking("private reasoning"))
    await asyncio.sleep(0.05)
    assert bot.sent == []


def test_thinking_tail_compacts_and_truncates():
    from claudebot.telegram.streaming import _thinking_tail

    assert _thinking_tail("a\nb   c") == "a b c"
    tail = _thinking_tail("word " * 100)
    assert tail.startswith("…")
    assert len(tail) <= 161


def test_render_status_appends_spinner_frame_only():
    import time as _time

    from claudebot.telegram.streaming import _SPINNER

    streamer = Streamer(RecordBot(), 1, _settings(markdown=False))
    streamer._status = "🔧 Bash"
    streamer._status_started = _time.monotonic() - 75
    rendered = streamer._render_status()
    assert rendered.startswith("🔧 Bash ")
    assert rendered[-1] in _SPINNER  # ends on the clock frame — no elapsed counter
    # Non-animated statuses (💭) render untouched.
    streamer._status_started = None
    assert streamer._render_status() == "🔧 Bash"


async def test_tool_spinner_keeps_ticking_without_new_events(monkeypatch):
    from claudebot.telegram import streaming as st

    monkeypatch.setattr(st, "_SPIN_TICK", 0.05)
    bot = RecordBot()
    streamer = Streamer(bot, 1, _settings(edit_interval=0.01, markdown=False))
    await streamer.on_event(_assistant_tools("Bash"))
    await asyncio.sleep(0.3)  # no further events — the worker must tick alone
    await streamer.finalize("done")  # stops the worker
    updates = [t for t, _m, _mid in bot.sent + bot.edited if t.startswith("🔧 Bash")]
    assert len(updates) >= 3  # kept editing with no new events
    assert len(set(updates)) >= 2  # the frame actually advanced


async def test_compact_boundary_sets_flag_and_status():
    bot = RecordBot()
    streamer = Streamer(bot, 1, _settings(edit_interval=0, markdown=False))
    event = parse_line(
        '{"type":"system","subtype":"compact_boundary",'
        '"compact_metadata":{"trigger":"auto","pre_tokens":1000000}}'
    )
    await streamer.on_event(event)
    await asyncio.sleep(0.05)
    assert streamer.saw_compaction
    assert any(text == "♻️ conversation compacted" for text, _m, _mid in bot.sent)
    # Reply text replaces the marker; the flag survives for the bridge notice.
    await streamer.on_event(_partial("done"))
    await asyncio.sleep(0.05)
    assert streamer._status == ""
    assert streamer.saw_compaction
