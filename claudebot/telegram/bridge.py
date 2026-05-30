"""The Telegram bot process: one poller, fanned out to per-chat Claude sessions.

Exactly ONE ``getUpdates`` consumer runs per bot token (running two triggers a
permanent Telegram 409). Updates are processed concurrently across chats, while
the per-session lock in ClaudeSession serializes turns within a single chat.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from telegram import Update
from telegram.constants import ChatAction
from telegram.error import TelegramError
from telegram.ext import (
    AIORateLimiter,
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from claudebot.claude.manager import SessionManager
from claudebot.core.config import Settings
from claudebot.core.logging import get_logger
from claudebot.core.paths import state_dir
from claudebot.telegram.auth import is_allowed
from claudebot.telegram.streaming import Streamer

log = get_logger("claudebot.telegram")

_WELCOME = (
    "👋 I'm your Claude Code bot. Send me anything and I'll run it through the real "
    "Claude Code on this machine.\n\n"
    "Commands:\n"
    "/new — fresh conversation\n"
    "/status — session info\n"
    "/cd <path> — change working directory\n"
    "/stop — abort the current reply\n"
)

_COMMANDS = [
    ("new", "Start a fresh Claude conversation"),
    ("status", "Show session info"),
    ("cd", "Change working directory"),
    ("stop", "Abort the current reply"),
    ("help", "What this bot can do"),
]


class TelegramBridge:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.manager = SessionManager(settings)
        self.app: Application | None = None

    # --- wiring -------------------------------------------------------------

    def build(self) -> Application:
        app = (
            ApplicationBuilder()
            .token(self.settings.telegram_bot_token.get_secret_value())
            .rate_limiter(AIORateLimiter())
            .concurrent_updates(True)
            .post_init(self._post_init)
            .post_shutdown(self._post_shutdown)
            .build()
        )
        app.add_handler(CommandHandler(["start", "help"], self._cmd_start))
        app.add_handler(CommandHandler("new", self._cmd_new))
        app.add_handler(CommandHandler("status", self._cmd_status))
        app.add_handler(CommandHandler("cd", self._cmd_cd))
        app.add_handler(CommandHandler("stop", self._cmd_stop))
        app.add_handler(MessageHandler(filters.PHOTO, self._on_photo))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text))
        app.add_error_handler(self._on_error)
        self.app = app
        return app

    def run(self) -> None:
        app = self.build()
        log.info(
            "claudebot starting — allowed users: %s, cwd: %s, model: %s, mode: %s",
            self.settings.allowed_user_ids or "(NONE — bot is locked!)",
            self.settings.working_dir,
            self.settings.model or "default",
            self.settings.permission_mode,
        )
        app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

    async def _post_init(self, app: Application) -> None:
        self.manager.start_background()
        with contextlib.suppress(TelegramError):
            await app.bot.set_my_commands(_COMMANDS)

    async def _post_shutdown(self, app: Application) -> None:
        await self.manager.shutdown()

    # --- access control -----------------------------------------------------

    async def _guard(self, update: Update) -> bool:
        user = update.effective_user
        if is_allowed(user.id if user else None, self.settings):
            return True
        uid = user.id if user else "?"
        uname = user.username if user else "?"
        log.warning("denied message from user_id=%s (@%s)", uid, uname)
        if update.message:
            with contextlib.suppress(TelegramError):
                await update.message.reply_text(
                    "⛔ You're not authorized to use this bot.\n"
                    f"Your Telegram ID is {uid} — ask the owner to add it."
                )
        return False

    # --- commands -----------------------------------------------------------

    async def _cmd_start(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        await update.message.reply_text(_WELCOME)

    async def _cmd_new(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        await self.manager.reset(update.effective_chat.id)
        await update.message.reply_text("🆕 Started a fresh conversation.")

    async def _cmd_status(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        session = await self.manager.get(update.effective_chat.id)
        alive = "running" if session.is_alive else "idle (resumes on next message)"
        await update.message.reply_text(
            "claudebot status\n"
            f"• session: {session.session_id}\n"
            f"• state: {alive}\n"
            f"• working dir: {session.working_dir}\n"
            f"• model: {self.settings.model or 'default'}\n"
            f"• permission mode: {self.settings.permission_mode}"
        )

    async def _cmd_cd(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        if not ctx.args:
            await update.message.reply_text("Usage: /cd <path>")
            return
        path = Path(" ".join(ctx.args)).expanduser()
        if not path.is_dir():
            await update.message.reply_text(f"❌ Not a directory: {path}")
            return
        await self.manager.reset(update.effective_chat.id, working_dir=path)
        await update.message.reply_text(f"📁 Working dir set to {path}\nStarted a fresh session there.")

    async def _cmd_stop(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        session = await self.manager.get(update.effective_chat.id)
        if session.busy:
            await session.interrupt()
            await update.message.reply_text("🛑 Stopping…")
        else:
            await update.message.reply_text("Nothing is running.")

    # --- messages -----------------------------------------------------------

    async def _on_text(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        await self._handle(update, ctx, update.message.text or "")

    async def _on_photo(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        photo = update.message.photo[-1]
        inbox = state_dir() / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        dest = inbox / f"{photo.file_unique_id}.jpg"
        try:
            tg_file = await ctx.bot.get_file(photo.file_id)
            await tg_file.download_to_drive(str(dest))
        except TelegramError as exc:
            await update.message.reply_text(f"⚠️ Couldn't download the image: {exc}")
            return
        caption = update.message.caption or "Look at this image and tell me about it."
        await self._handle(update, ctx, caption, image_paths=[dest])

    async def _handle(
        self,
        update: Update,
        ctx: ContextTypes.DEFAULT_TYPE,
        text: str,
        image_paths: list[Path] | None = None,
    ) -> None:
        chat_id = update.effective_chat.id
        text = text.strip()
        if not text and not image_paths:
            return
        session = await self.manager.get(chat_id)
        streamer = Streamer(ctx.bot, chat_id, self.settings)
        async with _typing(ctx.bot, chat_id):
            try:
                result = await session.ask(text, on_event=streamer.on_event, image_paths=image_paths)
            except Exception as exc:  # noqa: BLE001 - surface any failure to the user
                log.exception("chat %s: turn failed", chat_id)
                await streamer.error(f"⚠️ Error talking to Claude: {exc}")
                return
        await streamer.finalize(result.text)
        if result.is_error:
            with contextlib.suppress(TelegramError):
                await ctx.bot.send_message(chat_id, "⚠️ Claude reported an error for that turn.")
        if self.settings.show_cost and result.cost:
            with contextlib.suppress(TelegramError):
                await ctx.bot.send_message(chat_id, f"💸 cost: ${result.cost:.4f}")

    async def _on_error(self, update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        log.error("unhandled error: %s", ctx.error, exc_info=ctx.error)


@contextlib.asynccontextmanager
async def _typing(bot, chat_id: int):
    """Keep the Telegram 'typing…' indicator alive for the whole turn."""
    stop = asyncio.Event()

    async def loop() -> None:
        while not stop.is_set():
            with contextlib.suppress(TelegramError):
                await bot.send_chat_action(chat_id, ChatAction.TYPING)
            try:
                await asyncio.wait_for(stop.wait(), timeout=4.0)
            except asyncio.TimeoutError:
                continue

    task = asyncio.create_task(loop())
    try:
        yield
    finally:
        stop.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
