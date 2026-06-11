"""Per-chat settings scoping, override persistence, and assignment validation."""

import pytest
from pydantic import ValidationError

from claudebot.claude.manager import SessionManager
from claudebot.core.config import Settings
from claudebot.core.paths import overrides_file


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, telegram_bot_token="123:abc", allowed_user_ids="1", **kw)


def test_per_chat_settings_are_independent(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDEBOT_STATE_DIR", str(tmp_path))
    mgr = SessionManager(_settings(model=None))
    mgr.persist_override(111, "model", "opus")
    assert mgr._session_settings(111).model == "opus"
    assert mgr._session_settings(222).model is None  # other chat unaffected
    assert mgr.settings.model is None  # base settings untouched


def test_overrides_persist_across_reload(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDEBOT_STATE_DIR", str(tmp_path))
    mgr = SessionManager(_settings())
    mgr.persist_override(111, "turn_timeout", 300)
    mgr.persist_override(111, "show_cost", True)
    # A fresh manager (simulating a restart) reloads the overrides from disk.
    mgr2 = SessionManager(_settings())
    s = mgr2._session_settings(111)
    assert s.turn_timeout == 300
    assert s.show_cost is True
    assert (overrides_file().stat().st_mode & 0o777) == 0o600


def test_clearing_override_removes_it(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDEBOT_STATE_DIR", str(tmp_path))
    mgr = SessionManager(_settings())
    mgr.persist_override(111, "model", "opus")
    mgr.persist_override(111, "model", None)  # reset to default
    assert mgr._session_settings(111).model is None
    assert "111" not in mgr._overrides


def test_validate_assignment_rejects_bad_mode():
    s = _settings()
    with pytest.raises(ValidationError):
        s.permission_mode = "yolo"


async def test_rotated_session_id_is_persisted(monkeypatch, tmp_path):
    """--resume forks to a NEW session id; the map must follow it or a bot
    restart resumes the stale pre-fork id and drops every turn since."""
    monkeypatch.setenv("CLAUDEBOT_STATE_DIR", str(tmp_path))
    mgr = SessionManager(_settings())
    session = await mgr.get(111)
    assert mgr._map["111"] == session.session_id

    session._set_session_id("rotated-by-resume")  # what the stdout reader does

    # A fresh manager (simulating a restart) resumes the rotated id, not the old one.
    mgr2 = SessionManager(_settings())
    resumed = await mgr2.get(111)
    assert resumed.session_id == "rotated-by-resume"


async def test_working_dir_override_survives_reset_and_restart(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDEBOT_STATE_DIR", str(tmp_path))
    proj = tmp_path / "proj"
    proj.mkdir()
    mgr = SessionManager(_settings())
    mgr.persist_override(111, "working_dir", str(proj))  # what /cd does

    # The fresh session started by /model, /effort, /mode keeps the directory.
    session = await mgr.reset(111)
    assert session.working_dir == proj

    # And so does a session resumed after a bot restart.
    mgr2 = SessionManager(_settings())
    resumed = await mgr2.get(111)
    assert resumed.working_dir == proj
    assert resumed.settings.working_dir == proj  # a Path again, not the stored str


async def test_working_dir_override_scoped_to_one_chat(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDEBOT_STATE_DIR", str(tmp_path))
    proj = tmp_path / "proj"
    proj.mkdir()
    mgr = SessionManager(_settings())
    mgr.persist_override(111, "working_dir", str(proj))
    other = await mgr.get(222)
    assert other.working_dir == mgr.settings.working_dir  # other chat unaffected
