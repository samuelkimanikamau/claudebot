"""launchd LaunchAgent backend (macOS)."""

from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path
from xml.sax.saxutils import escape

from claudebot.core.logging import get_logger
from claudebot.core.paths import log_dir
from claudebot.service import ServiceManager

log = get_logger("claudebot.service.launchd")

LABEL = "ke.ve.claudebot"


class LaunchdService(ServiceManager):
    @property
    def plist_path(self) -> Path:
        return Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"

    @property
    def _domain_target(self) -> str:
        return f"gui/{os.getuid()}"

    @property
    def _service_target(self) -> str:
        return f"gui/{os.getuid()}/{LABEL}"

    # --- plist --------------------------------------------------------------

    def _render_plist(self) -> str:
        logs = log_dir()
        logs.mkdir(parents=True, exist_ok=True)
        args = "".join(f"        <string>{escape(a)}</string>\n" for a in self.bot_argv())
        env = {"PATH": self.path_env(), "HOME": str(Path.home()), **self.extra_env()}
        env_items = "".join(
            f"        <key>{escape(k)}</key>\n        <string>{escape(v)}</string>\n"
            for k, v in env.items()
        )
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LABEL}</string>
    <key>ProgramArguments</key>
    <array>
{args}    </array>
    <key>EnvironmentVariables</key>
    <dict>
{env_items}    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ProcessType</key>
    <string>Background</string>
    <key>StandardOutPath</key>
    <string>{logs / "claudebot.out.log"}</string>
    <key>StandardErrorPath</key>
    <string>{logs / "claudebot.err.log"}</string>
</dict>
</plist>
"""

    # --- operations ---------------------------------------------------------

    def install(self) -> None:
        self.plist_path.parent.mkdir(parents=True, exist_ok=True)
        self.plist_path.write_text(self._render_plist(), "utf-8")
        log.info("wrote %s", self.plist_path)
        # Replace any prior instance, then load + start.
        self._launchctl("bootout", self._service_target, check=False)
        self._launchctl("bootstrap", self._domain_target, str(self.plist_path))
        self._launchctl("enable", self._service_target, check=False)
        self._launchctl("kickstart", "-k", self._service_target, check=False)
        print(f"✅ installed and started {LABEL}")

    def uninstall(self) -> None:
        self._launchctl("bootout", self._service_target, check=False)
        if self.plist_path.exists():
            self.plist_path.unlink()
        print(f"🗑️  removed {LABEL}")

    def start(self) -> None:
        self._launchctl("kickstart", self._service_target)

    def stop(self) -> None:
        # With KeepAlive, the only way to truly stop it is to unload it.
        self._launchctl("bootout", self._service_target, check=False)

    def restart(self) -> None:
        self._launchctl("kickstart", "-k", self._service_target)

    def status(self) -> str:
        result = subprocess.run(
            ["launchctl", "print", self._service_target],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return f"{LABEL}: not loaded (run `claudebot service install`)"
        return result.stdout

    def installed_python(self) -> str | None:
        if not self.plist_path.exists():
            return None
        try:
            data = plistlib.loads(self.plist_path.read_bytes())
            args = data.get("ProgramArguments") or []
            return args[0] if args else None
        except Exception:  # noqa: BLE001
            return None

    def logs(self, follow: bool = False) -> None:
        err = log_dir() / "claudebot.err.log"
        out = log_dir() / "claudebot.out.log"
        for f in (out, err):
            f.parent.mkdir(parents=True, exist_ok=True)
            f.touch(exist_ok=True)
        cmd = ["tail", "-n", "200"]
        if follow:
            cmd.append("-f")
        cmd += [str(out), str(err)]
        subprocess.run(cmd)

    # --- helpers ------------------------------------------------------------

    def _launchctl(self, *args: str, check: bool = True) -> None:
        subprocess.run(["launchctl", *args], check=check)
