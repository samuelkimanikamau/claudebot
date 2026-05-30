"""The Telegram bot process: one poller, fanned out to per-chat Claude sessions.

Exactly ONE ``getUpdates`` consumer runs per bot token (running two triggers a
permanent Telegram 409). Updates are processed concurrently across chats, while
the per-session lock in ClaudeSession serializes turns within a single chat.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import re
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
from claudebot.core.paths import ensure_state_dir, state_dir
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
        self._lock_fh = None  # held open for the process lifetime (singleton guard)

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
        # DMs only, fresh messages only (no edited messages / channel posts / groups).
        private = filters.ChatType.PRIVATE & filters.UpdateType.MESSAGE
        app.add_handler(CommandHandler(["start", "help"], self._cmd_start, filters=private))
        app.add_handler(CommandHandler("new", self._cmd_new, filters=private))
        app.add_handler(CommandHandler("status", self._cmd_status, filters=private))
        app.add_handler(CommandHandler("cd", self._cmd_cd, filters=private))
        app.add_handler(CommandHandler("stop", self._cmd_stop, filters=private))
        app.add_handler(MessageHandler(filters.PHOTO & private, self._on_photo))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & private, self._on_text))
        app.add_error_handler(self._on_error)
        self.app = app
        return app

    def run(self) -> None:
        self._acquire_singleton_lock()
        app = self.build()
        log.info(
            "claudebot starting — allowed users: %s, cwd: %s, model: %s, mode: %s",
            self.settings.allowed_user_ids or "(NONE — bot is locked!)",
            self.settings.working_dir,
            self.settings.model or "default",
            self.settings.permission_mode,
        )
        # Subscribe only to message updates — the only type the bot handles.
        app.run_polling(allowed_updates=[Update.MESSAGE], drop_pending_updates=True)

    def _acquire_singleton_lock(self) -> None:
        """Enforce ONE poller per host: a second instance would cause a permanent 409."""
        ensure_state_dir()
        self._lock_fh = open(state_dir() / "claudebot.lock", "w")
        try:
            fcntl.flock(self._lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise SystemExit(
                "another claudebot is already running on this host — refusing to start a "
                "second Telegram poller (it would 409 the bot token permanently)."
            ) from exc

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
        if update.effective_message:
            with contextlib.suppress(TelegramError):
                await update.effective_message.reply_text(
                    "⛔ You're not authorized to use this bot.\n"
                    f"Your Telegram ID is {uid} — ask the owner to add it."
                )
        return False

    # --- commands -----------------------------------------------------------

    async def _cmd_start(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        await update.effective_message.reply_text(_WELCOME)

    async def _cmd_new(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        await self.manager.reset(update.effective_chat.id)
        await update.effective_message.reply_text("🆕 Started a fresh conversation.")

    async def _cmd_status(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        session = await self.manager.get(update.effective_chat.id)
        alive = "running" if session.is_alive else "idle (resumes on next message)"
        await update.effective_message.reply_text(
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
            await update.effective_message.reply_text("Usage: /cd <path>")
            return
        path = Path(" ".join(ctx.args)).expanduser()
        if not path.is_dir():
            await update.effective_message.reply_text(f"❌ Not a directory: {path}")
            return
        await self.manager.reset(update.effective_chat.id, working_dir=path)
        await update.effective_message.reply_text(f"📁 Working dir set to {path}\nStarted a fresh session there.")

    async def _cmd_stop(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        session = await self.manager.get(update.effective_chat.id)
        if session.busy:
            await session.interrupt()
            await update.effective_message.reply_text("🛑 Stopping…")
        else:
            await update.effective_message.reply_text("Nothing is running.")

    # --- messages -----------------------------------------------------------

    async def _on_text(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        await self._handle(update, ctx, update.effective_message.text or "")

    async def _on_photo(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        photo = update.effective_message.photo[-1]
        inbox = state_dir() / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            inbox.chmod(0o700)
        # Sanitize the Telegram-provided id before using it as a filename.
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", photo.file_unique_id)[:128] or "image"
        dest = inbox / f"{safe}.jpg"
        if inbox.resolve() not in dest.resolve().parents:
            log.warning("rejected suspicious image path: %s", dest)
            return
        try:
            tg_file = await ctx.bot.get_file(photo.file_id)
            await tg_file.download_to_drive(str(dest))
        except TelegramError as exc:
            await update.effective_message.reply_text(f"⚠️ Couldn't download the image: {exc}")
            return
        caption = update.effective_message.caption or "Look at this image and tell me about it."
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
        # Don't pile turns onto a busy session — tell the user instead of silently queuing.
        if session.busy:
            with contextlib.suppress(TelegramError):
                await update.effective_message.reply_text(
                    "⏳ Still working on your previous message — send /stop to abort it."
                )
            self._cleanup_files(image_paths)
            return
        streamer = Streamer(ctx.bot, chat_id, self.settings)
        try:
            async with _typing(ctx.bot, chat_id):
                try:
                    result = await session.ask(
                        text, on_event=streamer.on_event, image_paths=image_paths
                    )
                except Exception:  # noqa: BLE001 - surface a generic failure, log details
                    log.exception("chat %s: turn failed", chat_id)
                    await streamer.error("⚠️ Error talking to Claude — check the logs.")
                    return
            await streamer.finalize(result.text)
            if result.is_error:
                with contextlib.suppress(TelegramError):
                    await ctx.bot.send_message(chat_id, "⚠️ Claude reported an error for that turn.")
            if self.settings.show_cost and result.cost:
                with contextlib.suppress(TelegramError):
                    await ctx.bot.send_message(chat_id, f"💸 cost: ${result.cost:.4f}")
        finally:
            self._cleanup_files(image_paths)

    @staticmethod
    def _cleanup_files(paths: list[Path] | None) -> None:
        for p in paths or []:
            with contextlib.suppress(OSError):
                Path(p).unlink(missing_ok=True)

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
