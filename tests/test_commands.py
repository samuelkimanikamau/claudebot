"""Unit tests for Telegram command helpers."""

from claudebot.core.config import Settings
from claudebot.telegram.bridge import _COMMANDS, _apply_runtime_setting, _format_config


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, telegram_bot_token="123:abc", allowed_user_ids="1", **kw)


def test_command_menu_exposes_runtime_controls():
    names = {name for name, _description in _COMMANDS}
    assert {"model", "effort", "mode", "config", "cost", "timeout", "idle", "tools"} <= names


def test_format_config_includes_runtime_controls():
    text = _format_config(_settings(model="sonnet", effort="high", show_cost=True))
    assert "model: sonnet" in text
    assert "effort: high" in text
    assert "permission mode: bypassPermissions" in text
    assert "show cost: on" in text


def test_apply_model_sets_runtime_value_and_needs_fresh_session():
    settings = _settings()
    changed, restart, message = _apply_runtime_setting(settings, "model", ["opus"])
    assert changed is True
    assert restart is True
    assert settings.model == "opus"
    assert "model set to opus" in message


def test_apply_effort_rejects_unknown_value():
    settings = _settings(effort="high")
    changed, restart, message = _apply_runtime_setting(settings, "effort", ["turbo"])
    assert changed is False
    assert restart is False
    assert settings.effort == "high"
    assert "Allowed effort" in message


def test_apply_default_clears_optional_model():
    settings = _settings(model="opus")
    changed, restart, message = _apply_runtime_setting(settings, "model", ["default"])
    assert changed is True
    assert restart is True
    assert settings.model is None
    assert "model reset to default" in message


def test_apply_cost_toggles_without_new_session():
    settings = _settings(show_cost=False)
    changed, restart, message = _apply_runtime_setting(settings, "cost", ["on"])
    assert changed is True
    assert restart is False
    assert settings.show_cost is True
    assert "cost footer on" in message


def test_apply_timeout_accepts_seconds_without_new_session():
    settings = _settings(turn_timeout=1800)
    changed, restart, message = _apply_runtime_setting(settings, "timeout", ["600"])
    assert changed is True
    assert restart is False
    assert settings.turn_timeout == 600
    assert "turn timeout set to 600s" in message
