"""Unit tests for Telegram command helpers."""

from claudebot.core.config import PERMISSION_MODES, Settings
from claudebot.telegram.bridge import (
    _COMMANDS,
    _STOP_CALLBACK,
    _STOP_MARKUP,
    _apply_runtime_setting,
    _format_config,
    _options_keyboard,
)


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


def test_apply_model_sets_runtime_value_and_needs_child_restart():
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


def test_options_keyboard_for_model_has_tap_targets():
    kb = _options_keyboard("model")
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "cfg:model:default" in datas
    assert "cfg:model:opus" in datas
    assert all(len(d.encode()) <= 64 for d in datas)  # Telegram callback_data limit


def test_options_keyboard_mode_covers_all_permission_modes():
    kb = _options_keyboard("mode")
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert datas == [f"cfg:mode:{m}" for m in PERMISSION_MODES]


def test_options_keyboard_none_for_freeform_keys():
    assert _options_keyboard("timeout") is None
    assert _options_keyboard("cost") is None
    assert _options_keyboard("idle") is None


def test_stop_markup_callback_data():
    assert _STOP_MARKUP.inline_keyboard[0][0].callback_data == _STOP_CALLBACK


def test_apply_thinking_toggles_without_restart():
    settings = _settings(show_thinking=True)
    changed, restart, message = _apply_runtime_setting(settings, "thinking", ["off"])
    assert changed is True
    assert restart is False
    assert settings.show_thinking is False
    assert "thinking preview off" in message
