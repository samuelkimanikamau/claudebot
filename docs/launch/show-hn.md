# Show HN — draft

## Title (pick one, ≤80 chars)

1. `Show HN: Claudebot – drive Claude Code from Telegram, on your own machine`
2. `Show HN: Text your dev machine – a self-hosted Telegram bridge to Claude Code`
3. `Show HN: Claudebot – Claude Code on your phone, no API key, no SDK`

Option 1 is the safest: names the product, the mechanism, and the self-hosted
angle without hype. URL: https://github.com/samuelkimanikamau/claudebot
(repo, not the landing page — HN prefers source).

## First comment (post immediately after submitting)

---

Hi HN — I built this because I kept walking away from my desk while Claude Code
was mid-task, and the terminal doesn't follow you to the kitchen.

claudebot is a single Python process (~3.5k lines including tests) that bridges
Telegram to the real `claude` binary on your machine. Each chat gets a
persistent `claude -p --input-format stream-json --output-format stream-json`
child; your message goes to its stdin, the reply streams back into Telegram
bubble-by-bubble, and session ids persist to disk so a reboot resumes every
conversation with `--resume`.

Two design decisions worth defending up front:

**"Is this against Anthropic's ToS?"** I researched this carefully before
building. The "no third-party harness" rule is a credential-scope rule: don't
feed your subscription OAuth token to anything that isn't Claude Code (the
Agent SDK, Cursor, raw API calls, gateways). claudebot never touches the token
— it pipes text into the genuine `claude` process and reads JSON back, the same
headless interface Anthropic documents (`claude -p` on a subscription is
supported; there's a dedicated subscription credit for it since mid-June 2026).
The bot is also deliberately single-user: the allowlist ships empty, and the
README is blunt that adding anyone else is account sharing.

**"Why not the Agent SDK / API?"** Because then you pay twice — once for the
plan, again per token. The whole point is that the flat-rate subscription you
already have does the inference. I evaluated five architectures (SDK, one-shot
`-p` per message, PTY scraping, forking the official Telegram plugin, and
owning the stream-json loop) — the comparison is in docs/DECISIONS.md, and the
stream-json loop was the only one that scored on every axis.

Security model, since this thing can run Bash on your machine: fail-closed
Telegram-ID allowlist, DM-only handlers, a system-prompt preamble that declares
attached files and fetched pages untrusted data, the bot token and all config
stripped from the child environment, and a file lock so a second instance can't
409 your bot token. I treat it like an SSH key and the README tells you to do
the same.

Install is one line (curl | bash → interactive wizard → optional
launchd/systemd service). MIT. I'd genuinely value review of the security
posture — the parts that touch your machine are small enough to read in one
sitting.

---

## Prep checklist (before submitting)

- [ ] Visual at the top of the README. The og.png card is launch-fine; better
      if you can add 2–3 real Telegram screenshots (5 min, no editing). A demo
      GIF is pure upside, not a blocker — ship without it if it's fighting you.
- [ ] Make sure `claudebot doctor` and the installer work on a clean macOS + a
      clean Ubuntu box (the first comment that says "install failed" sets the tone).
- [ ] Publish the engineering post (engineering-post.md) a few days earlier;
      link it from the README. Don't link it in the HN comment unless asked.
- [ ] Submit Tue–Thu, 14:00–16:00 UTC (morning US West Coast).
- [ ] Be available for the first 3–4 hours to answer every comment.
- [ ] Have `git log --oneline` fresh in your head — HN loves specifics.

## Questions to expect

- "What about Anthropic shutting this down?" → It uses only documented headless
  interfaces; if the policy changes, the bot stops working and that's that.
  Policy, not law — the README says exactly this.
- "Why Telegram and not Slack/Discord/Signal?" → The engine (`claudebot/claude/`)
  has zero Telegram imports — it's a front-end detail. Telegram has the best
  bot API ergonomics for a personal single-user tool.
- "How is this different from happy / claudecodeui / the official plugin?" →
  Those are full UIs or put you on Anthropic's session loop. This is ~3.5k
  auditable lines that own the conversation loop, with the credential question
  answered by construction.
- "Can my team use it?" → No, and the README explains why in bold.
