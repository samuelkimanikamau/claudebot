"""Unit tests for ClaudeSession argument building and child-env hygiene (no spawn)."""

import asyncio

import pytest

from claudebot.claude.session import (
    SAFETY_PREAMBLE,
    ClaudeSession,
    SessionBusy,
    TurnResult,
    _ChildGone,
)
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


async def test_ask_nowait_rejects_while_turn_in_flight():
    """A message racing in mid-turn must be rejected, not silently queued."""
    session = ClaudeSession(1, _settings())
    await session._lock.acquire()  # simulate an in-flight turn holding the lock
    try:
        with pytest.raises(SessionBusy):
            await session.ask("racing message", nowait=True)
    finally:
        session._lock.release()


async def test_stop_during_inflight_turn_does_not_respawn(monkeypatch):
    class FakeProc:
        returncode = None

    session = ClaudeSession(1, _settings(), session_id="sess-123", is_new=False)
    session._proc = FakeProc()
    turn_started = asyncio.Event()
    stop_called = asyncio.Event()
    respawns = 0
    calls = 0

    async def fake_run_turn_bounded(text, on_event, image_paths):
        nonlocal calls
        calls += 1
        if calls == 1:
            turn_started.set()
            await stop_called.wait()
            raise _ChildGone()
        return TurnResult(text="respawned", session_id=session.session_id)

    async def fake_kill_proc():
        session._proc = None
        stop_called.set()

    async def fake_respawn():
        nonlocal respawns
        respawns += 1
        session._proc = FakeProc()

    monkeypatch.setattr(session, "_run_turn_bounded", fake_run_turn_bounded)
    monkeypatch.setattr(session, "_kill_proc", fake_kill_proc)
    monkeypatch.setattr(session, "_respawn", fake_respawn)

    ask_task = asyncio.create_task(session.ask("hello"))
    await asyncio.wait_for(turn_started.wait(), timeout=1)
    await session.stop()

    result = await asyncio.wait_for(ask_task, timeout=1)

    assert result.text == "🛑 Stopped."
    assert respawns == 0


async def test_unexpected_child_exit_during_turn_does_not_retry_user_message(monkeypatch):
    class FakeProc:
        returncode = None

    session = ClaudeSession(1, _settings(), session_id="sess-123", is_new=False)
    session._proc = FakeProc()
    respawns = 0
    calls = 0

    async def fake_run_turn_bounded(text, on_event, image_paths):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _ChildGone()
        return TurnResult(text="retried duplicate turn", session_id=session.session_id)

    async def fake_respawn():
        nonlocal respawns
        respawns += 1
        session._proc = FakeProc()

    monkeypatch.setattr(session, "_run_turn_bounded", fake_run_turn_bounded)
    monkeypatch.setattr(session, "_respawn", fake_respawn)

    result = await session.ask("deploy the app")

    assert result.is_error is True
    assert "Claude process stopped" in result.text
    assert calls == 1
    assert respawns == 0


def test_changed_effort_reaches_spawn_args_on_resume():
    # /effort mutates the live Settings and stops the child; the next spawn must
    # --resume the SAME session id with the new --effort (conversation preserved).
    settings = _settings(effort=None)
    s = ClaudeSession(1, settings, session_id="abc-123", is_new=False)
    settings.effort = "high"
    args = s._build_args(resume=True)
    assert "--resume" in args and "abc-123" in args
    assert "--effort" in args and "high" in args


def test_build_args_enables_remote_control_by_default():
    args = ClaudeSession(42, _settings())._build_args(resume=False)
    assert "--remote-control" in args
    # The name must be explicit: --remote-control takes an optional value, so a
    # bare flag would swallow whatever argument follows it.
    assert args[args.index("--remote-control") + 1].startswith("claudebot-")
    assert "42" in args[args.index("--remote-control") + 1]


def test_build_args_omits_remote_control_when_disabled():
    args = ClaudeSession(1, _settings(remote_control=None))._build_args(resume=False)
    assert "--remote-control" not in args


def test_remote_control_name_is_stable_across_respawns():
    session = ClaudeSession(7, _settings())
    first = session.remote_control_name
    session.session_id = "a-new-forked-id"  # what --resume does to us
    assert session.remote_control_name == first


def test_explicit_remote_control_name_is_used_verbatim():
    session = ClaudeSession(1, _settings(remote_control="sams-laptop"))
    assert session.remote_control_name == "sams-laptop"
    assert "sams-laptop" in session._build_args(resume=True)


def test_remote_control_name_has_no_double_hyphen_for_group_chats():
    session = ClaudeSession(-100123456, _settings())
    assert session.remote_control_name == "claudebot-default-100123456"
