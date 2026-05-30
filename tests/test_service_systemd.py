from claudebot.service.systemd import SystemdService


def test_systemd_unit_bounds_stop_time_before_force_kill():
    unit = SystemdService()._render_unit()

    assert "TimeoutStopSec=10" in unit
    assert "KillMode=control-group" in unit
