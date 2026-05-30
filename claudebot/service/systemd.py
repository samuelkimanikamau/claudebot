"""systemd --user backend (Linux, no root)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from claudebot.core.logging import get_logger
from claudebot.service import ServiceManager

log = get_logger("claudebot.service.systemd")

UNIT_NAME = "claudebot.service"


class SystemdService(ServiceManager):
    @property
    def unit_path(self) -> Path:
        return Path.home() / ".config/systemd/user" / UNIT_NAME

    # --- unit text ----------------------------------------------------------

    def _render_unit(self) -> str:
        argv = " ".join(self.bot_argv())
        env_lines = [f"Environment=PATH={self.path_env()}"]
        for key, value in self.extra_env().items():
            env_lines.append(f"Environment={key}={value}")
        env_block = "\n".join(env_lines)
        return f"""[Unit]
Description=claudebot — Telegram bot driving Claude Code
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=8

[Service]
Type=simple
WorkingDirectory={Path.home()}
{env_block}
ExecStart={argv}
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""

    # --- operations ---------------------------------------------------------

    def install(self) -> None:
        self.unit_path.parent.mkdir(parents=True, exist_ok=True)
        self.unit_path.write_text(self._render_unit(), "utf-8")
        log.info("wrote %s", self.unit_path)
        self._systemctl("daemon-reload")
        self._systemctl("enable", "--now", UNIT_NAME)
        print(f"✅ installed and started {UNIT_NAME}")
        self._linger_hint()

    def uninstall(self) -> None:
        self._systemctl("disable", "--now", UNIT_NAME, check=False)
        if self.unit_path.exists():
            self.unit_path.unlink()
        self._systemctl("daemon-reload")
        self._systemctl("reset-failed", check=False)
        print(f"🗑️  removed {UNIT_NAME}")

    def start(self) -> None:
        self._systemctl("start", UNIT_NAME)

    def stop(self) -> None:
        self._systemctl("stop", UNIT_NAME)

    def restart(self) -> None:
        self._systemctl("restart", UNIT_NAME)

    def status(self) -> str:
        result = subprocess.run(
            ["systemctl", "--user", "status", UNIT_NAME, "--no-pager"],
            capture_output=True,
            text=True,
        )
        return result.stdout or result.stderr

    def installed_python(self) -> str | None:
        if not self.unit_path.exists():
            return None
        for line in self.unit_path.read_text("utf-8").splitlines():
            if line.startswith("ExecStart="):
                return line[len("ExecStart="):].split()[0]
        return None

    def logs(self, follow: bool = False) -> None:
        cmd = ["journalctl", "--user", "-u", UNIT_NAME, "-n", "200", "--no-pager"]
        if follow:
            cmd = ["journalctl", "--user", "-u", UNIT_NAME, "-n", "200", "-f"]
        subprocess.run(cmd)

    # --- helpers ------------------------------------------------------------

    def _systemctl(self, *args: str, check: bool = True) -> None:
        subprocess.run(["systemctl", "--user", *args], check=check)

    def _linger_hint(self) -> None:
        try:
            out = subprocess.run(
                ["loginctl", "show-user", str(os.getuid()), "-p", "Linger"],
                capture_output=True,
                text=True,
            ).stdout
        except FileNotFoundError:
            out = ""
        if "Linger=yes" not in out:
            print(
                "\n⚠️  Enable linger so the bot survives logout/reboot:\n"
                "    loginctl enable-linger $USER\n"
            )
