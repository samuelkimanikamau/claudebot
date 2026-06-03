import asyncio
import json

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
        self._next = 0

    async def send_message(self, chat_id, text, parse_mode=None):
        self._next += 1
        self.sent.append((text, parse_mode, self._next))
        return FakeMessage(self._next)

    async def edit_message_text(self, text, chat_id, message_id, parse_mode=None):
        self.edited.append((text, parse_mode, message_id))

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
