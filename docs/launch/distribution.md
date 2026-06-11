# Distribution checklist

Sequence: README GIF → awesome-list PRs + soft Reddit post → fix reported
friction → engineering post → Show HN → (optional) Product Hunt.

## Awesome-list PRs (20 min each, permanent traffic)

Suggested entry line (adapt per list's format):

> [claudebot](https://github.com/samuelkimanikamau/claudebot) — Self-hosted
> Telegram bridge to the real `claude` binary over stream-json. Your
> subscription, your machine, no API key. Single-user by design.

Targets, in priority order:

1. **hesreallyhim/awesome-claude-code** — the canonical list (check the
   "applications" / "orchestrators" sections; it has a CONTRIBUTING flow).
2. **jqueryscript/awesome-claude-code** — tools & integrations format.
3. **jmanhype/awesome-claude-code** — plugins/integrations/resources.
4. **rohitg00/awesome-claude-code-toolkit** — has "companion apps" and
   "ecosystem" sections; claudebot fits companion apps.
5. **github.com/topics/claude-code** — not a PR: add the `claude-code`,
   `telegram-bot`, `claude` topics to the repo settings so it appears there.

## Reddit (soft launch, before HN)

- r/ClaudeAI and r/ClaudeCode — post the demo video with a title like
  "I bridge Telegram to the claude binary on my machine — subscription only,
  token never leaves Claude Code". Lead with the ToS-honesty angle; that
  community asks immediately.
- r/selfhosted — angle: "single-user, no SaaS, two JSON files of state".
- Answer every comment; collect the friction reports as GitHub issues.

## Competitive positioning (for comments, not for the page)

- **happy / claudecodeui** (mobile/web UIs, big stars): full clients with their
  own infrastructure. claudebot is ~3.5k auditable lines riding an app you
  already have, with the credential question answered by construction.
- **Official Telegram plugin**: validation that the use case is sanctioned;
  but fixed UX on Anthropic's session loop.
- **RichardAtCT/claude-code-telegram**: mature, but Agent-SDK-based — the
  credential/pay-twice issue claudebot exists to avoid.

## Metrics (already wired)

- **Installs**: `claudebot-installs` on the VE.KE box (counts
  `GET /install.sh` across rotated nginx logs; Cloudflare-proxied, so count
  requests, not IPs).
- **Stars / traffic**: GitHub Insights → Traffic, check referrers weekly
  during launch fortnight.
- Decide per channel: if a channel sends visitors but no installs, fix the
  README, not the channel.

## Community loop

- Enable GitHub Discussions; pin a "what broke during install?" thread.
- A small Telegram group for the project — users are Telegram people; put the
  invite link in the README and the bot's /help.
- Tag a `v0.1.0` release before the HN post so "what version?" has an answer.
