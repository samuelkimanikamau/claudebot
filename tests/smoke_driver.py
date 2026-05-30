"""Smoke-test the ClaudeSession driver against the REAL claude binary.

Not a pytest test (it spawns claude and uses your subscription). Run directly:

    python tests/smoke_driver.py

Verifies: spawn → stream-json turn → result parse → cross-process --resume,
all on the subscription with no API key.
"""

import asyncio
import os
import sys
import time

os.environ.setdefault("CLAUDEBOT_TELEGRAM_BOT_TOKEN", "smoke-dummy")
os.environ.setdefault("CLAUDEBOT_ALLOWED_USER_IDS", "1")
os.environ.setdefault("CLAUDEBOT_STATE_DIR", "/tmp/claudebot-smoke")
os.environ.setdefault("CLAUDEBOT_WORKING_DIR", "/tmp")
os.environ.setdefault("CLAUDEBOT_MODEL", "haiku")

from claudebot.claude.session import ClaudeSession
from claudebot.core.config import load_settings
from claudebot.core.logging import setup_logging


async def main() -> int:
    setup_logging("INFO")
    settings = load_settings()
    print(f"[cfg] model={settings.model} mode={settings.permission_mode} cwd={settings.working_dir}")

    seen: list[str] = []

    async def on_event(e):
        seen.append(e.type)

    s1 = ClaudeSession(chat_id=999, settings=settings, session_id=None, is_new=True)
    sid = s1.session_id
    print(f"[turn1] session_id={sid}")
    t0 = time.monotonic()
    r1 = await asyncio.wait_for(
        s1.ask("Reply with exactly the word ALPHA and nothing else.", on_event=on_event),
        timeout=120,
    )
    print(f"[turn1] reply={r1.text!r} is_error={r1.is_error} cost={r1.cost} "
          f"in {time.monotonic() - t0:.1f}s")
    print(f"[turn1] event types: {sorted(set(seen))}")
    await s1.stop()
    await asyncio.sleep(1.5)
    if "ALPHA" not in r1.text.upper():
        print("FAIL: turn 1 did not return ALPHA")
        return 1

    s2 = ClaudeSession(chat_id=999, settings=settings, session_id=sid, is_new=False)
    print(f"[turn2] resuming session_id={s2.session_id}")
    r2 = await asyncio.wait_for(
        s2.ask("What word did I ask you to reply with earlier? Just that word."),
        timeout=120,
    )
    print(f"[turn2] reply={r2.text!r} is_error={r2.is_error}")
    await s2.stop()
    if "ALPHA" not in r2.text.upper():
        print("FAIL: resume did not recall ALPHA")
        return 1

    print("\n✅ PASS: spawn + stream-json turn + result parse + cross-process --resume")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
