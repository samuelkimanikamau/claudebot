"""CLAUDEBOT_LOG_FILE switches logging from stderr to a rotating file."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from claudebot.core.logging import setup_logging


def _root_handlers() -> list[logging.Handler]:
    return logging.getLogger().handlers


def test_stderr_by_default(monkeypatch):
    monkeypatch.delenv("CLAUDEBOT_LOG_FILE", raising=False)
    setup_logging("INFO")
    assert not any(isinstance(h, RotatingFileHandler) for h in _root_handlers())


def test_rotating_file_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDEBOT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("CLAUDEBOT_LOG_FILE", "1")
    setup_logging("INFO")

    handler = next(h for h in _root_handlers() if isinstance(h, RotatingFileHandler))
    assert handler.baseFilename == str(tmp_path / "logs" / "claudebot.log")
    assert handler.maxBytes > 0
    assert handler.backupCount > 0

    logging.getLogger("claudebot.test").info("hello rotation")
    handler.flush()
    assert "hello rotation" in (tmp_path / "logs" / "claudebot.log").read_text("utf-8")

    # Restore the default stderr config so later tests aren't writing to tmp_path.
    monkeypatch.delenv("CLAUDEBOT_LOG_FILE")
    setup_logging("INFO")
