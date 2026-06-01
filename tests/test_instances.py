"""Tests for the multi-instance (second-bot) support."""

import pytest

from claudebot.core.paths import instance_state_dir, instances_root, validate_instance_name
from claudebot.service.launchd import LaunchdService
from claudebot.service.systemd import SystemdService


def test_validate_instance_name_accepts_sane_names():
    assert validate_instance_name("work") == "work"
    assert validate_instance_name("bot-2") == "bot-2"
    assert validate_instance_name("a_b9") == "a_b9"


@pytest.mark.parametrize("bad", ["Work", "-x", "has space", "a/b", "x" * 40, "", "café"])
def test_validate_instance_name_rejects_bad(bad):
    with pytest.raises(ValueError):
        validate_instance_name(bad)


def test_instance_state_dir_is_under_instances_root():
    assert instance_state_dir("work") == instances_root() / "work"


def test_default_service_naming_is_unchanged():
    # The default (un-named) bot must keep its original names so existing
    # installs are never disrupted.
    s = SystemdService("claude")
    assert s.name == "claudebot"
    assert s.unit_name == "claudebot.service"
    assert "--instance" not in s.bot_argv()
    assert LaunchdService("claude").label == "ke.ve.claudebot"


def test_named_instance_gets_its_own_names():
    s = SystemdService("claude", "work")
    assert s.name == "claudebot-work"
    assert s.unit_name == "claudebot-work.service"
    assert s.bot_argv()[-3:] == ["--instance", "work", "run"]

    lp = LaunchdService("claude", "research")
    assert lp.label == "ke.ve.claudebot.research"
    assert lp.plist_path.name == "ke.ve.claudebot.research.plist"
    assert lp.bot_argv()[-3:] == ["--instance", "research", "run"]
