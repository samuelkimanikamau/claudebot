import plistlib

from claudebot.service.launchd import LaunchdService


def test_plist_enables_rotating_file_logging(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDEBOT_STATE_DIR", str(tmp_path))
    data = plistlib.loads(LaunchdService()._render_plist().encode())

    assert data["EnvironmentVariables"]["CLAUDEBOT_LOG_FILE"] == "1"
    # The raw redirects stay as crash nets for output logging can't capture.
    assert data["StandardOutPath"].endswith("claudebot.out.log")
    assert data["StandardErrorPath"].endswith("claudebot.err.log")


def test_instance_plist_keeps_its_own_state_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDEBOT_STATE_DIR", str(tmp_path))
    data = plistlib.loads(LaunchdService(instance="work")._render_plist().encode())

    env = data["EnvironmentVariables"]
    assert env["CLAUDEBOT_LOG_FILE"] == "1"
    assert env["CLAUDEBOT_STATE_DIR"] == str(tmp_path)
