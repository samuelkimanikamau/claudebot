"""``claudebot`` command-line entry point."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from claudebot import version_string
from claudebot.core.config import load_settings
from claudebot.core.logging import setup_logging


def cmd_run(_args: argparse.Namespace) -> int:
    from pydantic import ValidationError

    try:
        settings = load_settings()
    except ValidationError as exc:
        print("⚠  Configuration is missing or invalid. Run:  claudebot setup\n")
        print(exc)
        return 2

    setup_logging(settings.log_level)
    if not settings.allowed_user_ids:
        print("⚠  CLAUDEBOT_ALLOWED_USER_IDS is empty — the bot will reject everyone.")
        print("   Run `claudebot setup` and add your Telegram ID.\n")

    from claudebot.telegram.bridge import TelegramBridge

    TelegramBridge(settings).run()
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    from claudebot import wizard

    return wizard.run(instance=getattr(args, "instance", None))


def cmd_instances(_args: argparse.Namespace) -> int:
    from pathlib import Path

    from claudebot.core.paths import instance_state_dir, list_instances

    rows = [("(default)", Path.home() / ".claudebot", "")]
    for name in list_instances():
        rows.append((name, instance_state_dir(name), f"--instance {name} "))

    print("\nBots on this machine:\n")
    for name, sdir, flag in rows:
        state = "✓ configured" if (sdir / ".env").exists() else "· not set up"
        print(f"  {name:<14} {state}")
        print(f"  {'':<14} dir: {sdir}")
        print(f"  {'':<14} run: claudebot {flag}run    manage: claudebot {flag}service status\n")
    print("Add another bot:  claudebot --instance <name> setup\n")
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    ok = True

    def check(label: str, good: bool, detail: str = "") -> bool:
        nonlocal ok
        ok = ok and good
        mark = "\033[32m✓\033[0m" if good else "\033[31m✗\033[0m"
        print(f" {mark} {label}" + (f" — {detail}" if detail else ""))
        return good

    print("\nclaudebot doctor\n")

    check(f"Python {platform.python_version()}", sys.version_info >= (3, 10))

    claude = shutil.which("claude")
    if check("claude binary on PATH", bool(claude), claude or "not found — install Claude Code"):
        version = subprocess.run([claude, "--version"], capture_output=True, text=True).stdout.strip()
        if version:
            print(f"     {version}")
        status = subprocess.run(["claude", "auth", "status"], capture_output=True, text=True)
        logged_in = False
        detail = "run: claude auth login"
        try:
            info = json.loads(status.stdout or "{}")
            logged_in = bool(info.get("loggedIn"))
            if logged_in:
                method = info.get("authMethod", "?")
                sub = info.get("subscriptionType")
                detail = f"{method}" + (f" · {sub}" if sub else "")
        except json.JSONDecodeError:
            logged_in = status.returncode == 0 and "true" in status.stdout.lower()
            detail = "" if logged_in else detail
        check("Claude logged in (subscription, no API key)", logged_in, detail)

    from claudebot.core.paths import env_file

    cfg = env_file()
    try:
        settings = load_settings()
    except Exception as exc:  # noqa: BLE001
        check("config loaded", False, f"{cfg} — run `claudebot setup`")
        print(f"     {exc}")
        print("\nFix the ✗ items above, then re-run `claudebot doctor`.")
        return 1

    check("config loaded", True, str(cfg))
    check(
        "allowed users set",
        bool(settings.allowed_user_ids),
        f"{len(settings.allowed_user_ids)} id(s)" if settings.allowed_user_ids
        else "EMPTY — the bot is locked to nobody",
    )
    check("working dir exists", settings.working_dir.is_dir(), str(settings.working_dir))

    token = settings.telegram_bot_token.get_secret_value()
    try:
        data = _telegram_getme(token)
        username = (data.get("result") or {}).get("username")
        check("Telegram token valid", bool(data.get("ok")), f"@{username}" if username else "")
    except urllib.error.HTTPError as exc:
        check("Telegram token valid", False, f"HTTP {exc.code} — bad token?")
    except Exception as exc:  # noqa: BLE001
        check("Telegram reachable", False, str(exc).replace(token, "***"))

    print(
        "\n\033[32mAll good.\033[0m  Start with:  claudebot run"
        if ok
        else "\nFix the ✗ items above, then re-run `claudebot doctor`."
    )
    return 0 if ok else 1


def cmd_service(args: argparse.Namespace) -> int:
    from claudebot.service import UnsupportedPlatform, get_service_manager

    claude_binary = "claude"
    try:
        claude_binary = load_settings().claude_binary
    except Exception:  # noqa: BLE001 - service install shouldn't require full config
        pass

    try:
        manager = get_service_manager(claude_binary, instance=getattr(args, "instance", None))
    except UnsupportedPlatform as exc:
        print(exc)
        return 1

    action = args.action
    if action == "install":
        manager.install()
    elif action == "uninstall":
        manager.uninstall()
    elif action == "start":
        manager.start()
    elif action == "stop":
        manager.stop()
    elif action == "restart":
        manager.restart()
    elif action == "status":
        print(manager.status())
    elif action == "logs":
        manager.logs(follow=args.follow)
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    import claudebot as pkg

    src = Path(pkg.__file__).resolve().parent.parent
    if not (src / "pyproject.toml").is_file():
        print("⚠  claudebot is installed non-editably, so there's no local source to update.")
        print("   Reinstall from your source, e.g.:")
        print("   pip install -U 'git+https://github.com/samuelkimanikamau/claudebot.git'")
        return 1

    # Install into the SERVICE's interpreter when there is one, so dependencies
    # land where the bot actually runs — not just in whatever venv invoked us.
    target_py = sys.executable
    manager = None
    try:
        from claudebot.service import get_service_manager

        manager = get_service_manager(instance=getattr(args, "instance", None))
        svc_py = manager.installed_python()
        if svc_py and Path(svc_py).exists():
            target_py = svc_py
    except Exception:  # noqa: BLE001 - no/unknown service is fine
        manager = None

    print(f"• source:        {src}")
    print(f"• target python: {target_py}")

    is_git = (src / ".git").is_dir()
    pre_sha = ""
    if is_git:
        pre_sha = subprocess.run(
            ["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout.strip()
        if not args.no_pull:
            print("• git pull --ff-only")
            pull = subprocess.run(["git", "-C", str(src), "pull", "--ff-only"])
            if pull.returncode != 0:
                print("✗ git pull failed — aborting (resolve it, or use --no-pull).")
                return pull.returncode
    else:
        print("• (not a git repo — installing the local files as-is)")

    print("• pip install -e .   (picks up code + any new dependencies)")
    rc = subprocess.run([target_py, "-m", "pip", "install", "-q", "-e", str(src)]).returncode
    if rc != 0:
        print("✗ pip install failed")
        _rollback(src, target_py, pre_sha)
        return rc

    # Smoke gate: run the test suite against the pulled code BEFORE going live.
    if not args.no_test:
        has_pytest = subprocess.run(
            [target_py, "-c", "import pytest"], capture_output=True
        ).returncode == 0
        if not has_pytest:
            print("• (pytest not in the target env — skipping smoke gate; `pip install -e '.[dev]'` to enable)")
        else:
            print("• pytest -q   (smoke gate)")
            gate = subprocess.run([target_py, "-m", "pytest", "-q"], cwd=str(src))
            if gate.returncode != 0:
                print("✗ tests failed on the pulled code — NOT restarting.")
                _rollback(src, target_py, pre_sha)
                return gate.returncode

    if not args.no_restart and manager is not None:
        try:
            print("• restarting service")
            manager.restart()
        except Exception as exc:  # noqa: BLE001
            print(f"  (could not restart automatically: {exc})")
            print("  restart it yourself: claudebot service restart")
    elif not args.no_restart:
        print("• no service installed — restart your `claudebot run` process to apply.")

    version = subprocess.run(
        [target_py, "-m", "claudebot", "--version"], capture_output=True, text=True
    ).stdout.strip()
    print(f"\n✓ updated — {version or 'done'}")
    return 0


def _rollback(src: Path, target_py: str, pre_sha: str) -> None:
    """Revert a failed update: reset to the pre-pull commit and reinstall."""
    if not pre_sha:
        return
    print(f"↩  rolling back to {pre_sha[:8]} and reinstalling…")
    subprocess.run(["git", "-C", str(src), "reset", "--hard", pre_sha])
    subprocess.run([target_py, "-m", "pip", "install", "-q", "-e", str(src)])


def _telegram_getme(token: str) -> dict:
    url = f"https://api.telegram.org/bot{token}/getMe"
    with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310 - fixed https host
        return json.load(resp)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claudebot",
        description="An always-on Telegram bot that drives the real Claude Code "
        "on your subscription (no API key, no Agent SDK).",
    )
    parser.add_argument(
        "-V", "--version", action="version", version=f"claudebot {version_string()}"
    )
    parser.add_argument(
        "--instance",
        metavar="NAME",
        help="Operate a SECOND bot with its own state dir, config, lock, and service "
        "(e.g. `claudebot --instance work setup`). Omit for your main bot.",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    sub.add_parser("run", help="Run the bot in the foreground.").set_defaults(func=cmd_run)
    sub.add_parser("setup", help="Interactive setup wizard.").set_defaults(func=cmd_setup)
    sub.add_parser("doctor", help="Check configuration and connectivity.").set_defaults(func=cmd_doctor)
    sub.add_parser("instances", help="List your bots (default + named).").set_defaults(func=cmd_instances)

    update = sub.add_parser("update", help="Pull latest, reinstall, and restart the service.")
    update.add_argument("--no-pull", action="store_true", help="Skip `git pull`.")
    update.add_argument("--no-restart", action="store_true", help="Reinstall without restarting.")
    update.add_argument("--no-test", action="store_true", help="Skip the pytest smoke gate.")
    update.set_defaults(func=cmd_update)

    service = sub.add_parser("service", help="Manage the always-on service.")
    service.add_argument(
        "action",
        choices=["install", "uninstall", "start", "stop", "restart", "status", "logs"],
    )
    service.add_argument("-f", "--follow", action="store_true", help="Follow logs (with `logs`).")
    service.set_defaults(func=cmd_service)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    # A named instance redirects ALL state (config, sessions, lock, logs) to its
    # own dir by setting CLAUDEBOT_STATE_DIR before anything reads it.
    if getattr(args, "instance", None):
        from claudebot.core.paths import instance_state_dir, validate_instance_name

        try:
            validate_instance_name(args.instance)
        except ValueError as exc:
            print(f"⚠  {exc}")
            return 2
        os.environ["CLAUDEBOT_STATE_DIR"] = str(instance_state_dir(args.instance))
    setup_logging(os.environ.get("CLAUDEBOT_LOG_LEVEL", "INFO"))
    return args.func(args)
