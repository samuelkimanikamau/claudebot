"""Telegram front-end: a single bot process that fans messages to Claude sessions."""

from claudebot.telegram.bridge import TelegramBridge

__all__ = ["TelegramBridge"]
