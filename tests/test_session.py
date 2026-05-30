"""Unit tests for ClaudeSession argument building and child-env hygiene (no spawn)."""

from claudebot.claude.session import SAFETY_PREAMBLE, ClaudeSession
from claudebot.core.config import Settings


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, telegram_bot_token="123:abc", allowed_user_ids="1", **kw)


def test_child_env_strips_secrets(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-be-stripped")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok")
    monkeypatch.setenv("CLAUDEBOT_TELEGRAM_BOT_TOKEN", "123:secret")
    monkeypatch.setenv("CLAUDEBOT_STATE_DIR", "/tmp/x")
    env = ClaudeSession(1, _settings())._child_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    # No claudebot config (incl. the bot token) reaches the child process.
    assert not any(k.startswith("CLAUDEBOT_") for k in env)


def test_build_args_includes_safety_preamble():
    args = ClaudeSession(1, _settings(safety_preamble=True))._build_args(resume=False)
    i = args.index("--append-system-prompt")
    assert SAFETY_PREAMBLE in args[i + 1]
    assert "--session-id" in args  # a brand-new session


def test_build_args_omits_preamble_when_disabled():
    args = ClaudeSession(1, _settings(safety_preamble=False))._build_args(resume=False)
    assert "--append-system-prompt" not in args


def test_build_args_resume_uses_resume_flag():
    s = ClaudeSession(1, _settings(), session_id="sess-123", is_new=False)
    args = s._build_args(resume=True)
    assert "--resume" in args
    assert "sess-123" in args
    assert "--session-id" not in args


def test_turn_timeout_setting_present():
    assert _settings().turn_timeout == 1800
    assert _settings(turn_timeout=0).turn_timeout == 0
