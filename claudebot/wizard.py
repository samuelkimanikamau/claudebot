"""Interactive first-run setup. Writes ``~/.claudebot/.env``.

Prompts are read from ``/dev/tty`` when available, so the wizard still works when
the installer pipes it (``curl … | bash``). Re-running it pre-fills answers from
the existing config.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
from pathlib import Path

from claudebot.core.config import PERMISSION_MODES
from claudebot.core.paths import ensure_state_dir, env_file

C_BOLD = "\033[1m"
C_CYAN = "\033[36m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_DIM = "\033[2m"
C_RESET = "\033[0m"


def _tty() -> tuple[io.TextIOWrapper | None, bool]:
    """Open the controlling terminal. Returns ``(handle, writable)``.

    Prefer ``r+`` so prompts go straight to the terminal; fall back to read-only
    (some environments expose ``/dev/tty`` but not for writing) and emit prompts
    to stdout instead. ``(None, False)`` means there is no controlling terminal.
    """
    for mode in ("r+", "r"):
        try:
            return open("/dev/tty", mode), ("+" in mode)  # noqa: SIM115 - closed at exit
        except OSError:
            continue
    return None, False


class _Prompt:
    def __init__(self) -> None:
        self.tty, self._tty_writable = _tty()
        # We can prompt only with a real terminal: either /dev/tty opened, or
        # stdin itself is a tty. When piped (e.g. `curl … | bash`) both are
        # false and input() would read the leftover pipe / EOF — so bail instead.
        self.interactive = self.tty is not None or sys.stdin.isatty()

    def ask(self, label: str, default: str | None = None) -> str:
        suffix = f" {C_DIM}[{default}]{C_RESET}" if default else ""
        text = f"{C_CYAN}?{C_RESET} {label}{suffix}: "
        if self.tty is not None:
            out = self.tty if self._tty_writable else sys.stdout
            out.write(text)
            out.flush()
            line = self.tty.readline()
            if not line:  # Ctrl-D / closed terminal
                raise EOFError
            answer = line.strip()
        else:
            answer = input(text).strip()
        return answer or (default or "")

    def yes_no(self, label: str, default: bool = True) -> bool:
        d = "Y/n" if default else "y/N"
        ans = self.ask(f"{label} ({d})").lower()
        if not ans:
            return default
        return ans.startswith("y")


def _existing() -> dict[str, str]:
    path = env_file()
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip()
    return values


def _check_claude() -> None:
    claude = shutil.which("claude")
    if not claude:
        print(
            f"{C_YELLOW}⚠  `claude` is not on your PATH.{C_RESET} Install Claude Code first:\n"
            f"   {C_DIM}npm install -g @anthropic-ai/claude-code{C_RESET}\n"
        )
        return
    status = subprocess.run(
        ["claude", "auth", "status"], capture_output=True, text=True
    )
    blob = (status.stdout + status.stderr).lower()
    if status.returncode == 0 and ("true" in blob or "logged in" in blob):
        print(f"{C_GREEN}✓{C_RESET} Claude Code is installed and logged in.\n")
    else:
        print(
            f"{C_YELLOW}⚠  Claude Code may not be logged in.{C_RESET} "
            f"Run {C_BOLD}claude auth login{C_RESET} (needs a Pro/Max/Team/Enterprise plan).\n"
        )


def run(instance: str | None = None) -> int:
    flag = f"--instance {instance} " if instance else ""
    suffix = f"  ·  instance: {instance}" if instance else ""
    print(f"\n{C_BOLD}{C_CYAN}claudebot setup{suffix}{C_RESET}")
    print(f"{C_DIM}Configure the Telegram bot that drives your Claude Code.{C_RESET}\n")

    _check_claude()
    prior = _existing()
    p = _Prompt()

    if not p.interactive:
        print(
            f"{C_YELLOW}⚠  Setup needs an interactive terminal, but none is attached.{C_RESET}\n"
            f"   {C_DIM}This happens when the installer is piped (e.g. `curl … | bash`).{C_RESET}\n\n"
            f"   Finish setup in your terminal with:\n"
            f"       {C_CYAN}{C_BOLD}claudebot {flag}setup{C_RESET}\n"
        )
        return 1

    try:
        return _run_prompts(p, prior, instance, flag)
    except (EOFError, KeyboardInterrupt):
        print(
            f"\n{C_YELLOW}⚠  Setup cancelled — no input received.{C_RESET} "
            f"Re-run {C_CYAN}claudebot {flag}setup{C_RESET} any time.\n"
        )
        return 1


def _run_prompts(p: _Prompt, prior: dict[str, str], instance: str | None, flag: str) -> int:
    # --- Telegram bot token -------------------------------------------------
    print(f"{C_BOLD}1. Telegram bot token{C_RESET}")
    print(f"   Create a bot with {C_CYAN}@BotFather{C_RESET} → /newbot → copy the token.")
    token = ""
    while not token:
        token = p.ask("Bot token", prior.get("CLAUDEBOT_TELEGRAM_BOT_TOKEN"))
        if token and ":" not in token:
            print(f"   {C_YELLOW}That doesn't look like a token (expected 123456:ABC…). Try again.{C_RESET}")
            token = ""

    # --- Allowed user IDs ---------------------------------------------------
    print(f"\n{C_BOLD}2. Who can use the bot?{C_RESET}")
    print(f"   Get your numeric ID from {C_CYAN}@userinfobot{C_RESET}. Comma-separate several.")
    print(f"   {C_DIM}Keep this to yourself — sharing your subscription is a ban risk.{C_RESET}")
    ids = ""
    while not ids:
        ids = p.ask("Allowed Telegram user IDs", prior.get("CLAUDEBOT_ALLOWED_USER_IDS"))
        if ids and not all(part.strip().lstrip("-").isdigit() for part in ids.split(",") if part.strip()):
            print(f"   {C_YELLOW}IDs must be numbers. Try again.{C_RESET}")
            ids = ""

    # --- Working directory --------------------------------------------------
    print(f"\n{C_BOLD}3. Working directory{C_RESET}")
    print(f"   {C_DIM}Where Claude runs (and can read/write files).{C_RESET}")
    default_dir = prior.get("CLAUDEBOT_WORKING_DIR", str(Path.home()))
    workdir = p.ask("Working directory", default_dir)
    workdir = str(Path(workdir).expanduser())

    # --- Model --------------------------------------------------------------
    print(f"\n{C_BOLD}4. Model{C_RESET} {C_DIM}(blank = Claude Code default){C_RESET}")
    model = p.ask("Model (opus / sonnet / haiku / blank)", prior.get("CLAUDEBOT_MODEL", ""))

    # --- Permission mode ----------------------------------------------------
    print(f"\n{C_BOLD}5. Permission mode{C_RESET}")
    print(f"   {C_DIM}bypassPermissions — never blocks (best for unattended; full tool access)")
    print(f"   acceptEdits      — auto-approves edits, may stall on other tools{C_RESET}")
    mode = ""
    default_mode = prior.get("CLAUDEBOT_PERMISSION_MODE", "bypassPermissions")
    while mode not in PERMISSION_MODES:
        mode = p.ask(f"Permission mode {C_DIM}{PERMISSION_MODES}{C_RESET}", default_mode)
        if mode not in PERMISSION_MODES:
            print(f"   {C_YELLOW}Pick one of {PERMISSION_MODES}.{C_RESET}")

    # --- Write --------------------------------------------------------------
    ensure_state_dir()
    path = env_file()
    lines = [
        "# claudebot configuration — written by `claudebot setup`.",
        "# Keep this file private; it contains your bot token.",
        f"CLAUDEBOT_TELEGRAM_BOT_TOKEN={token}",
        f"CLAUDEBOT_ALLOWED_USER_IDS={ids}",
        f"CLAUDEBOT_WORKING_DIR={workdir}",
        f"CLAUDEBOT_PERMISSION_MODE={mode}",
    ]
    if model:
        lines.append(f"CLAUDEBOT_MODEL={model}")
    path.write_text("\n".join(lines) + "\n", "utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass

    print(f"\n{C_GREEN}✓ Saved {path}{C_RESET}")
    print(f"\n{C_BOLD}Next:{C_RESET}")
    print(f"   {C_CYAN}claudebot {flag}doctor{C_RESET}            # verify the setup")
    print(f"   {C_CYAN}claudebot {flag}run{C_RESET}               # run in the foreground")
    print(f"   {C_CYAN}claudebot {flag}service install{C_RESET}   # keep it always-on\n")

    if p.yes_no("Install the always-on service now?", default=False):
        from claudebot.service import UnsupportedPlatform, get_service_manager

        try:
            get_service_manager(prior.get("CLAUDEBOT_CLAUDE_BINARY", "claude"), instance).install()
        except UnsupportedPlatform as exc:
            print(f"{C_YELLOW}{exc}{C_RESET}")
        except Exception as exc:  # noqa: BLE001
            print(f"{C_YELLOW}Service install failed: {exc}{C_RESET}")
    return 0
