# Five ways to put Claude Code on Telegram (and why four of them are wrong)

*Draft — publish on a blog (ve.ke or dev.to) a few days before the Show HN.
Target ~1,200 words. The bones and facts are here; tighten the voice on a pass.*

---

I wanted to message my dev machine from my phone and have the real Claude Code
do the work — on the subscription I already pay for, not a second API bill.
Simple ask. It turns out there are five ways to build it, and the differences
between them are where all the interesting engineering lives.

## The constraint that shapes everything

Anthropic's rule for subscription accounts is, at bottom, a credential-scope
rule: your OAuth token must only ever be used by Claude Code itself. Feed it to
the Agent SDK, Cursor, a gateway, or a raw API call and you're outside the
terms. So the architecture question becomes: **how do you drive Claude Code
without ever touching its credentials?**

## The five architectures

**1. The Claude Agent SDK.** The mature option — RichardAtCT's
claude-code-telegram uses it, and it works. But the SDK is its own inference
client; pointing it at subscription credentials is exactly the thing the rule
forbids, and pointing it at an API key means paying twice. Rejected on the
constraint, not on quality.

**2. One-shot `claude -p` per message.** Spawn the binary fresh for every
Telegram message, read stdout, reply. Honest and simple, but every turn
cold-starts: no warm context, transcript re-read each time, several seconds of
latency before the first token. A fine v1. A frustrating daily driver.

**3. PTY / TUI scraping.** Run the interactive `claude` in a pseudo-terminal
and parse the ANSI screen. People do this. The first minor TUI redesign breaks
your parser, and you're reverse-engineering a UI that was never a contract.
Rejected outright.

**4. The official Telegram channel plugin.** Anthropic ships one. But then
you're a peripheral on *their* session loop: fixed UX, no custom commands, and
custom channels need a development flag on every launch. Great validation that
the use case is supported; not a place to build.

**5. Own the loop: bidirectional stream-json.** `claude -p --input-format
stream-json --output-format stream-json` turns the binary into an engine: write
JSON user turns to stdin, read NDJSON events from stdout. One persistent child
per chat, kept warm between turns. The token never leaves the binary because
the binary is doing the inference — you're just its keyboard and screen. This
is what claudebot does.

## What "own the loop" actually costs you

The architecture diagram is one line. The production lessons were not:

- **Resume forks session ids.** `--resume <id>` doesn't continue a session, it
  forks a new id. Persist the rotated id on every change or a restart resumes a
  stale conversation and silently loses everything since. (Found this one the
  hard way in review, not in production — barely.)
- **One poller per token, enforced.** Two `getUpdates` consumers put your bot
  token into a permanent Telegram 409. A `flock` at startup is cheaper than
  debugging that.
- **Telegram's 4,096-char limit meets markdown.** Long replies must chunk, and
  a chunk boundary will eventually land inside a ``` code fence. Convert each
  chunk independently and the second half renders as garbage. The fix:
  fence-aware chunking — close the fence at the boundary, reopen it (with its
  language) in the next message, so every chunk is self-contained markdown.
- **Never auto-retry a dead turn.** If the child dies mid-turn you do not know
  what side effects already happened (it can run Bash). claudebot reports and
  stops; the human decides whether to resend.
- **Streaming into a chat app is an editing problem.** Telegram rate-limits
  message edits, so the renderer throttles edits off the stdout-read path,
  streams head-first into stable per-block messages, and upgrades each block to
  formatted text in place — what you watched stream is exactly what you keep.
- **The child's environment is a security boundary.** Strip the bot token and
  all bot config (a bypassPermissions turn could read its own env), strip
  `ANTHROPIC_API_KEY` (so billing can't silently switch to the API), and
  declare attached files untrusted data in the system prompt — with full tool
  access, a malicious PDF is the attack surface, not your own messages.

## The shape it ended up

~3,500 lines of typed Python including the test suite. One process. Two JSON
state files, no database. The engine package has zero Telegram imports —
Telegram is a front-end detail, and the same loop would sit behind Slack or
Signal unchanged.

Single-user by design: the allowlist ships empty and the README is blunt that
adding anyone else is account sharing plus a free shell on your machine.

Code: https://github.com/samuelkimanikamau/claudebot — MIT, and the parts that
touch your machine are deliberately small enough to audit in an evening.
