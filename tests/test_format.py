"""Tests for Markdown -> Telegram MarkdownV2 conversion."""

from claudebot.telegram.format import available, to_telegram


def test_bold_converts():
    body, mode = to_telegram("**hi**")
    if available():
        assert mode == "MarkdownV2"
        assert "hi" in body
    else:  # dependency missing -> graceful plain fallback
        assert (body, mode) == ("**hi**", None)


def test_table_does_not_crash():
    md = "| a | b |\n|---|---|\n| 1 | 2 |"
    body, mode = to_telegram(md)
    assert isinstance(body, str) and body


def test_empty_is_plain():
    assert to_telegram("") == ("", None)


def test_special_chars_escaped_when_available():
    body, mode = to_telegram("Cost is 1.5 - check it.")
    if available():
        assert mode == "MarkdownV2"
        # MarkdownV2 requires '.' and '-' to be escaped.
        assert "\\." in body
