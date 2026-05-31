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
