# Design decisions & the three reference repos

This captures *why* claudebot is built the way it is — including the evaluation
of the three projects that inspired it.

## Goal

One repo that does what `claude-code-telegram`, `hermes-agent`, and `openclaw`
each do a piece of:

- drive the **real `claude` binary** (not the Claude Agent SDK),
- on the logged-in **subscription** (no API key),
- behind **our own Telegram process** (not the official plugin),
- with **good onboarding** and **always-alive** supervision.

## The three repos (ground-truth dissection)

| Repo | What it is | Drives `claude`? | How | Take |
|---|---|---|---|---|
| **RichardAtCT/claude-code-telegram** | Mature Telegram front-end for Claude Code (Python, python-telegram-bot) | ✅ | via the **Claude Agent SDK** (`claude-agent-sdk`) — it *deleted* its old raw-CLI backend | The SDK is exactly what we were asked to avoid. **Stole:** the streaming-to-Telegram UX, the subscription auth toggle, the systemd-user always-alive recipe, the security middleware idea. |
| **NousResearch/hermes-agent** | Self-improving general agent + multi-platform gateway | ❌ | its **own** agent loop over OpenAI/Anthropic HTTP; only shells `claude setup-token`. Impersonates Claude Code headers for subscription use. | Not the Claude Code product at all. **Stole:** the one-line `curl|bash` installer + `/dev/tty` wizard, the `hermes-gateway` systemd/launchd generator, the "migrate from X" onboarding. |
| **openclaw/openclaw** | Self-hosted multi-channel AI gateway (TypeScript). *Not* the Captain-Claw game of the same name. | ✅ (opt-in) | a `claude-cli` backend: **`claude -p --output-format stream-json --include-partial-messages --verbose --resume {id}`**, env stripped of API keys | The single best reference for our engine. **Stole:** the exact headless flag set, the env-clearing trick, the custom grammY channel + edit-forward streaming, the cross-platform daemon templates. |

## The technique we picked: Approach A

Drive the real binary over **bidirectional `stream-json`** — one persistent
`claude -p --input-format stream-json --output-format stream-json --verbose
--replay-user-messages --include-partial-messages` child per chat; write user
turns to stdin, read events until `result`; resume with `--session-id`/`--resume`.

It was the only option that scores ✅ on **all five** axes (every claim verified
live against `claude` 2.1.157 on the dev machine — `apiKeySource:none`, max sub):

| Approach | Real binary | No API key | Own Telegram | Always-alive | Robust |
|---|---|---|---|---|---|
| **A. Headless stream-json (chosen)** | ✅ | ✅ | ✅ total | ✅ clean daemon | ✅ structured JSON |
| B. One-shot `claude -p` per message | ✅ | ✅ | ✅ | ✅ | ✅ but cold-start each turn |
| C. PTY/TUI scraping | ✅ | ✅ | ✅ | ⚠️ | ❌ brittle ANSI |
| D. Fork the official channel plugin | ✅ | ✅ | ⚠️ Telegram only | ⚠️ babysit a session | ⚠️ experimental, allowlist-gated |
| E. Auto-install official plugin | ✅ | ✅ | ❌ fixed UX | ⚠️ | ✅ |

A "owns the conversation loop"; D/E make you a peripheral on Anthropic's session
loop (and custom channels need `--dangerously-load-development-channels` each
launch). B is a strict subset of A — a fine v1, but A gives warm context +
streaming. C and the Agent SDK were rejected outright.

## Is it allowed?

The "no third-party harness" rule is a **credential-scope** rule: it forbids
using your subscription **OAuth token** in any inference client that is *not*
Claude Code (the Agent SDK with sub creds, Cline, Cursor, raw API, gateways).
claudebot only pipes text into the genuine `claude` process and reads JSON back —
the token never leaves Claude Code. Anthropic supports this path explicitly
(`claude -p` on subscription is documented; `claude setup-token`; a dedicated
`claude -p` subscription credit from 2026-06-15). **The lines not to cross:**
don't extract the token to call the API yourself, keep it single-user, don't
hammer it 24/7. See README → "Is this allowed?".

## Multiple bots (instances)

The security audit flagged that the service layer assumed one bot per host (fixed
unit/label names). Resolved: a bot is now an **instance**. `--instance <name>`
redirects all state to `~/.claudebot/instances/<name>` and names the service
`claudebot-<name>` / `ke.ve.claudebot.<name>`, so several bots coexist without
colliding (each takes its own poller lock and needs its own BotFather token). This
is still **single-user** — multiple *bots* owned by one person, not multiple
*people* on one subscription. See README → "Running more than one bot".
