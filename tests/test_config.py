"""Unit tests for config parsing (CSV lists, validation)."""

import pytest
from pydantic import ValidationError

from claudebot.core.config import Settings


def _settings(**env) -> Settings:
    # _env_file=None so the test never reads a real ~/.claudebot/.env.
    return Settings(_env_file=None, **env)


def test_csv_user_ids_parsed_to_ints(monkeypatch):
    monkeypatch.setenv("CLAUDEBOT_TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("CLAUDEBOT_ALLOWED_USER_IDS", "111, 222 ,333")
    s = _settings()
    assert s.allowed_user_ids == [111, 222, 333]


def test_empty_user_ids_is_empty_list(monkeypatch):
    monkeypatch.setenv("CLAUDEBOT_TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("CLAUDEBOT_ALLOWED_USER_IDS", "")
    assert _settings().allowed_user_ids == []


def test_token_is_required(monkeypatch):
    monkeypatch.delenv("CLAUDEBOT_TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(ValidationError):
        _settings()


def test_invalid_permission_mode_rejected(monkeypatch):
    monkeypatch.setenv("CLAUDEBOT_TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("CLAUDEBOT_PERMISSION_MODE", "yolo")
    with pytest.raises(ValidationError):
        _settings()


def test_disallowed_tools_csv(monkeypatch):
    monkeypatch.setenv("CLAUDEBOT_TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("CLAUDEBOT_DISALLOWED_TOOLS", "WebFetch, Bash")
    assert _settings().disallowed_tools == ["WebFetch", "Bash"]
