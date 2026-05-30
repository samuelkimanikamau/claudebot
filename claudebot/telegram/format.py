"""Convert Claude's GitHub-flavored Markdown to Telegram's MarkdownV2.

Claude replies in rich Markdown (``**bold**``, ``## headers``, ``| tables |``,
fenced code). Telegram understands a restricted MarkdownV2 with a long list of
characters that MUST be backslash-escaped, and no table syntax at all. We delegate
to ``telegramify-markdown``, which handles the escaping and renders tables as
fixed-width code blocks.

The caller must still be ready to fall back to plain text: if conversion isn't
available, or Telegram rejects the entities anyway, ``to_telegram`` returns
``(original, None)`` and the sender retries unformatted — a reply must never fail
to deliver just because of formatting.
"""

from __future__ import annotations

from claudebot.core.logging import get_logger

log = get_logger("claudebot.telegram.format")

try:  # optional dependency — degrade to plain text if absent
    import telegramify_markdown

    _convert = getattr(telegramify_markdown, "markdownify", None) or getattr(
        telegramify_markdown, "convert", None
    )
except Exception:  # noqa: BLE001
    _convert = None


def available() -> bool:
    return _convert is not None


def to_telegram(markdown: str) -> tuple[str, str | None]:
    """Return ``(text, parse_mode)`` for ``bot.send_message``.

    ``parse_mode`` is ``"MarkdownV2"`` when conversion succeeded, else ``None``
    (and ``text`` is the original Markdown, to be sent as plain text).
    """
    if _convert is None or not markdown:
        return markdown, None
    try:
        return _convert(markdown), "MarkdownV2"
    except Exception as exc:  # noqa: BLE001 - never let formatting break a reply
        log.debug("markdown->telegram conversion failed: %s", exc)
        return markdown, None
