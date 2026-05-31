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

from claudebot import version_string
from claudebot.claude.manager import SessionManager
from claudebot.core.config import PERMISSION_MODES, Settings
from claudebot.core.logging import get_logger
from claudebot.core.paths import ensure_state_dir, state_dir
from claudebot.telegram.auth import is_allowed
from claudebot.telegram.streaming import Streamer

log = get_logger("claudebot.telegram")

_WELCOME = (
    "👋 I'm your Claude Code bot. Send me anything and I'll run it through the real "
    "Claude Code on this machine.\n\n"
    "Send text, a photo, or a document and I'll act on it.\n\n"
    "Core commands:\n"
    "/new — fresh conversation\n"
    "/status — session info\n"
    "/cd <path> — change working directory\n"
    "/retry — resend your last message\n"
    "/stop — abort the current reply\n\n"
    "Runtime controls:\n"
    "/model <default|opus|sonnet|haiku|id>\n"
    "/effort <default|low|medium|high|xhigh|max>\n"
    "/mode <bypassPermissions|acceptEdits|default|plan|dontAsk>\n"
    "/config — show current runtime config\n"
    "/tools — show tool allow/deny lists\n"
    "/cost <on|off> — show/hide cost footer\n"
    "/timeout <seconds> — turn timeout, 0 disables\n"
    "/idle <seconds> — idle child eviction, 0 disables\n"
)

_COMMANDS = [
    ("new", "Start a fresh Claude conversation"),
    ("status", "Show session info"),
    ("config", "Show runtime model, effort, mode, timeouts"),
    ("model", "Set model: default, opus, sonnet, haiku, or model id"),
    ("effort", "Set thinking effort: default, low, medium, high, xhigh, max"),
    ("mode", "Set permission mode for new turns"),
    ("tools", "Show allowed/disallowed Claude tools"),
    ("cost", "Toggle cost footer: on/off"),
    ("timeout", "Set per-turn timeout seconds; 0 disables"),
    ("idle", "Set idle eviction seconds; 0 disables"),
    ("cd", "Change working directory"),
    ("retry", "Resend your last message"),
    ("stop", "Abort the current reply"),
    ("help", "What this bot can do"),
]

_EFFORTS = {"low", "medium", "high", "xhigh", "max"}
_SESSION_RESTART_OPTIONS = {"model", "effort", "mode"}
# Runtime command key -> the Settings field it writes (used to persist per-chat).
_FIELD_FOR = {
    "model": "model",
    "effort": "effort",
    "mode": "permission_mode",
    "cost": "show_cost",
    "timeout": "turn_timeout",
    "idle": "idle_timeout",
}
_CLEAR_VALUES = {"default", "auto", "none", "off"}
_TRUE_VALUES = {"1", "true", "yes", "y", "on", "enable", "enabled"}
_FALSE_VALUES = {"0", "false", "no", "n", "off", "disable", "disabled"}


def _format_config(settings: Settings) -> str:
    return (
        "claudebot config\n"
        f"• model: {settings.model or 'default'}\n"
        f"• effort: {settings.effort or 'default'}\n"
        f"• permission mode: {settings.permission_mode}\n"
        f"• working dir: {settings.working_dir}\n"
        f"• stream partials: {'on' if settings.stream_partials else 'off'}\n"
        f"• markdown: {'on' if settings.markdown else 'off'}\n"
        f"• show cost: {'on' if settings.show_cost else 'off'}\n"
        f"• idle timeout: {settings.idle_timeout}s\n"
        f"• turn timeout: {settings.turn_timeout}s"
    )


def _format_tools(settings: Settings) -> str:
    allowed = ", ".join(settings.allowed_tools) if settings.allowed_tools else "all default tools"
    disallowed = ", ".join(settings.disallowed_tools) if settings.disallowed_tools else "none"
    return "claudebot tools\n" f"• allowed: {allowed}\n" f"• disallowed: {disallowed}"


def _apply_runtime_setting(
    settings: Settings, key: str, args: list[str]
) -> tuple[bool, bool, str]:
    """Apply a runtime setting from a Telegram command.

    Returns ``(changed, requires_fresh_session, user_message)``.
    """
    value = " ".join(args).strip()
    if not value:
        return False, False, _usage_for(key, settings)

    normalized = value.lower()
    if key == "model":
        if normalized in _CLEAR_VALUES:
            settings.model = None
            return True, True, "✅ model reset to default."
        if any(ch.isspace() for ch in value):
            return False, False, "Usage: /model <default|opus|sonnet|haiku|model-id>"
        settings.model = value
        return True, True, f"✅ model set to {value}."

    if key == "effort":
        if normalized in _CLEAR_VALUES:
            settings.effort = None
            return True, True, "✅ effort reset to default."
        if normalized not in _EFFORTS:
            return False, False, "Allowed effort values: default, low, medium, high, xhigh, max."
        settings.effort = normalized
        return True, True, f"✅ effort set to {normalized}."

    if key == "mode":
        if value not in PERMISSION_MODES:
            allowed = ", ".join(PERMISSION_MODES)
            return False, False, f"Allowed permission modes: {allowed}."
        settings.permission_mode = value
        return True, True, f"✅ permission mode set to {value}."

    if key == "cost":
        flag = _parse_bool(normalized)
        if flag is None:
            return False, False, "Usage: /cost <on|off>"
        settings.show_cost = flag
        return True, False, f"✅ cost footer {'on' if flag else 'off'}."

    if key == "timeout":
        seconds = _parse_seconds(value)
        if seconds is None:
            return False, False, "Usage: /timeout <seconds>  (0 disables)"
        settings.turn_timeout = seconds
        return True, False, f"✅ turn timeout set to {seconds}s."

    if key == "idle":
        seconds = _parse_seconds(value)
        if seconds is None:
            return False, False, "Usage: /idle <seconds>  (0 disables)"
        settings.idle_timeout = seconds
        return True, False, f"✅ idle timeout set to {seconds}s."

    return False, False, f"Unknown setting: {key}"


def _usage_for(key: str, settings: Settings) -> str:
    current = {
        "model": settings.model or "default",
        "effort": settings.effort or "default",
        "mode": settings.permission_mode,
        "cost": "on" if settings.show_cost else "off",
        "timeout": f"{settings.turn_timeout}s",
        "idle": f"{settings.idle_timeout}s",
    }.get(key, "unknown")
    examples = {
        "model": "Usage: /model <default|opus|sonnet|haiku|model-id>",
        "effort": "Usage: /effort <default|low|medium|high|xhigh|max>",
        "mode": f"Usage: /mode <{'|'.join(PERMISSION_MODES)}>",
        "cost": "Usage: /cost <on|off>",
        "timeout": "Usage: /timeout <seconds>  (0 disables)",
        "idle": "Usage: /idle <seconds>  (0 disables)",
    }.get(key, f"Usage: /{key} <value>")
    return f"Current {key}: {current}\n{examples}"


def _parse_bool(value: str) -> bool | None:
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    return None


def _parse_seconds(value: str) -> int | None:
    try:
        seconds = int(value)
    except ValueError:
        return None
    if seconds < 0:
        return None
    return seconds


class TelegramBridge:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.manager = SessionManager(settings)
        self.app: Application | None = None
        self._lock_fh = None  # held open for the process lifetime (singleton guard)
        self._last_text: dict[int, str] = {}  # per-chat last prompt, for /retry

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
        app.add_handler(CommandHandler("config", self._cmd_config, filters=private))
        app.add_handler(CommandHandler("tools", self._cmd_tools, filters=private))
        # The 6 runtime-setting commands all route through _cmd_runtime.
        for key in _FIELD_FOR:  # model, effort, mode, cost, timeout, idle
            app.add_handler(CommandHandler(key, self._runtime_handler(key), filters=private))
        app.add_handler(CommandHandler("cd", self._cmd_cd, filters=private))
        app.add_handler(CommandHandler("retry", self._cmd_retry, filters=private))
        app.add_handler(CommandHandler("stop", self._cmd_stop, filters=private))
        app.add_handler(MessageHandler(filters.PHOTO & private, self._on_photo))
        app.add_handler(MessageHandler(filters.Document.ALL & private, self._on_document))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & private, self._on_text))
        # Catch-all for message types we don't handle (voice, video, stickers, …).
        app.add_handler(MessageHandler(private & ~filters.COMMAND, self._on_unsupported))
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
        s = session.settings
        alive = "running" if session.is_alive else "idle (resumes on next message)"
        await update.effective_message.reply_text(
            "claudebot status\n"
            f"• version: {version_string()}\n"
            f"• session: {session.session_id}\n"
            f"• state: {alive}\n"
            f"• working dir: {session.working_dir}\n"
            f"• model: {s.model or 'default'}\n"
            f"• effort: {s.effort or 'default'}\n"
            f"• permission mode: {s.permission_mode}\n"
            f"• turn timeout: {s.turn_timeout}s"
        )

    async def _cmd_config(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        session = await self.manager.get(update.effective_chat.id)
        await update.effective_message.reply_text(_format_config(session.settings))

    async def _cmd_tools(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        session = await self.manager.get(update.effective_chat.id)
        await update.effective_message.reply_text(_format_tools(session.settings))

    def _runtime_handler(self, key: str):
        """Build a CommandHandler callback bound to one runtime-setting key."""

        async def handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
            await self._cmd_runtime(update, ctx, key)

        return handler

    async def _cmd_runtime(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE, key: str) -> None:
        if not await self._guard(update):
            return
        chat_id = update.effective_chat.id
        args = list(ctx.args or [])
        session = await self.manager.get(chat_id)
        if key in _SESSION_RESTART_OPTIONS and args and session.busy:
            await update.effective_message.reply_text(
                "⏳ Still working on your previous message — send /stop before changing "
                f"/{key}."
            )
            return
        # Apply to THIS chat's settings only (no cross-chat bleed) and persist it.
        changed, needs_fresh_session, message = _apply_runtime_setting(session.settings, key, args)
        if changed:
            field = _FIELD_FOR.get(key)
            if field is not None:
                self.manager.persist_override(chat_id, field, getattr(session.settings, field))
            if needs_fresh_session:
                await self.manager.reset(chat_id)
                message += "\n🆕 Started a fresh conversation with the new setting."
        await update.effective_message.reply_text(message)

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

    async def _cmd_retry(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        last = self._last_text.get(update.effective_chat.id)
        if not last:
            await update.effective_message.reply_text("Nothing to retry yet.")
            return
        await self._handle(update, ctx, last)

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

    async def _on_document(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        doc = update.effective_message.document
        if doc.file_size and doc.file_size > 20 * 1024 * 1024:
            await update.effective_message.reply_text(
                "⚠️ That file is larger than 20 MB (Telegram's bot download limit)."
            )
            return
        inbox = state_dir() / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            inbox.chmod(0o700)
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", doc.file_name or "file")[:128] or "file"
        uid = re.sub(r"[^A-Za-z0-9_-]", "_", doc.file_unique_id)[:64]
        dest = inbox / f"{uid}_{safe_name}"
        if inbox.resolve() not in dest.resolve().parents:
            log.warning("rejected suspicious document path: %s", dest)
            return
        try:
            tg_file = await ctx.bot.get_file(doc.file_id)
            await tg_file.download_to_drive(str(dest))
        except TelegramError as exc:
            await update.effective_message.reply_text(f"⚠️ Couldn't download the file: {exc}")
            return
        caption = update.effective_message.caption or f"Read the attached file ({safe_name})."
        await self._handle(update, ctx, caption, image_paths=[dest])

    async def _on_unsupported(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        await update.effective_message.reply_text(
            "I can handle text, photos, and documents right now — that message type "
            "isn't supported yet."
        )

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
        if text:
            self._last_text[chat_id] = text  # for /retry
        session = await self.manager.get(chat_id)
        # Don't pile turns onto a busy session — tell the user instead of silently queuing.
        if session.busy:
            with contextlib.suppress(TelegramError):
                await update.effective_message.reply_text(
                    "⏳ Still working on your previous message — send /stop to abort it."
                )
            self._cleanup_files(image_paths)
            return
        streamer = Streamer(ctx.bot, chat_id, session.settings)
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
            if session.settings.show_cost and result.cost:
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
