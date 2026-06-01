# CLAUDE.md — claudebot

Guidance for Claude Code when working in this repository.

## Project overview

`claudebot` is an always-on **Telegram bot that drives the real Claude Code
binary** over its headless `stream-json` protocol, on the host's logged-in
Pro/Max **subscription** — no API key, no Claude Agent SDK, no official plugin.
The bot owns the Telegram side end-to-end; `claude` does the inference.

This is **research Approach A** ("own the loop"): one persistent `claude -p
--input-format stream-json --output-format stream-json …` child per Telegram
chat, fed user turns on stdin, read back as NDJSON events until each turn's
`result`. See `docs/` for how the alternatives (official channel plugin, PTY
scraping, Agent SDK) were evaluated and rejected.

**Policy:** the subscription OAuth token never leaves the `claude` binary, which
keeps this inside Anthropic's supported usage. The hard rule the code must never
break: do not extract the token or point any non-Claude-Code client at it, and
keep the bot single-user (`allowed_user_ids`). See README → "Is this allowed?".

## Tech stack

- **Python 3.10+**, `asyncio`.
- **python-telegram-bot v22** (`[rate-limiter]` extra) — the Telegram client.
- **pydantic-settings v2** — typed config from `~/.claudebot/.env`.
- **hatchling** — build backend. Console script: `claudebot`.
- No database; state is `~/.claudebot/sessions.json` (chat_id → claude session_id).

## Development commands

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"        # editable install with pytest/ruff

claudebot setup                # interactive wizard → ~/.claudebot/.env
claudebot doctor               # check claude auth + token + config
claudebot run                  # foreground
claudebot service install      # systemd --user (Linux) / launchd (macOS)
claudebot update               # pull + reinstall into the SERVICE venv + restart
claudebot instances            # list your bots (default + named)
claudebot --instance work …    # operate a SECOND bot (own state dir, lock, service)

pytest -q                      # unit tests
ruff check claudebot           # lint
```

Smoke-test the driver against the real binary (no Telegram token needed):
`python tests/smoke_driver.py` (uses model `haiku`, cwd `/tmp`).

## Architecture

```
claudebot/
├── cli.py              # argparse entry: run / setup / doctor / service / update / instances (+ --instance)
├── wizard.py           # interactive onboarding, writes ~/.claudebot/.env
├── core/
│   ├── config.py       # pydantic-settings Settings (env_prefix CLAUDEBOT_)
│   ├── paths.py        # ~/.claudebot state-dir resolution (+ instances/<name>)
│   └── logging.py
├── claude/             # ← the engine
│   ├── events.py       # parse stream-json NDJSON into typed events
│   ├── session.py      # ClaudeSession: persistent subprocess, turn loop, --resume
│   └── manager.py      # SessionManager: chat_id → session, persistence, idle evict
├── telegram/
│   ├── bridge.py       # single PTB Application + handlers (the only getUpdates consumer)
│   ├── streaming.py    # edit-forward renderer (throttle + 4096 chunking)
│   └── auth.py         # allowlist gate
└── service/
    ├── systemd.py      # ~/.config/systemd/user/claudebot.service (Restart=always)
    └── launchd.py      # ~/Library/LaunchAgents/ke.ve.claudebot.plist (KeepAlive)
```

### Multiple bots (instances)

`--instance <name>` sets `CLAUDEBOT_STATE_DIR` early in `main()`, so all state
(config, sessions, lock, logs) redirects to `~/.claudebot/instances/<name>`, and the
service is named `claudebot-<name>` / `ke.ve.claudebot.<name>`. The **default
(un-named) bot keeps `~/.claudebot` and the names `claudebot` / `ke.ve.claudebot`** —
never rename those or existing installs break. `load_settings()` resolves the `.env`
at call time so it follows the instance.

### Key invariants (don't regress these)

- **One Telegram poller per token.** `bridge.py` runs a single `getUpdates`
  consumer; never spawn a second (→ permanent 409). Multiple chats fan out to
  multiple `claude` children, not multiple pollers.
- **Serialize within a chat, concurrent across chats.** `concurrent_updates(True)`
  + a per-`ClaudeSession` `asyncio.Lock`.
- **Subscription only.** `session.py::_child_env` strips `ANTHROPIC_API_KEY` /
  `ANTHROPIC_AUTH_TOKEN` so the child uses the logged-in OAuth creds.
- **Resume across restarts.** `session_id`s persist to `sessions.json`; a known
  chat starts with `--resume`, a new chat with `--session-id`.
- **Permissive parsing.** `events.py` must degrade gracefully on unknown shapes
  (the stream-json contract is still evolving) — return empty, never raise.

## External integrations

- **Claude Code** (`claude`) — the inference engine. Auth via `claude auth login`
  / `claude setup-token`. Health: `claude auth status` (JSON; `loggedIn: true`).
- **Telegram Bot API** — via python-telegram-bot (long-poll). Token from
  @BotFather; user IDs from @userinfobot.

## Conventions

- Type hints throughout; `from __future__ import annotations` in every module.
- Keep `claude/` (engine) free of Telegram imports — it's reusable for any
  front-end. Telegram specifics live in `telegram/`.
- Commit style: `Add/Fix/Update/Remove/Improve <what>`.
