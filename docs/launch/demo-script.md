# Demo video script

Two cuts from one recording session: a **30s master** (README GIF, X, the HN
thumbnail) and a **75s extended** (Reddit, the engineering post). Shoot the
extended beats; cut down.

## The trick that makes it shootable

A real task on camera is unpredictable and slow. So rig it: plant a one-line
bug in a real repo *before* recording. Claude finds and fixes a planted
one-liner in well under a minute, the demo stays 100% real (no fake bubbles),
and you can re-shoot takes by re-planting.

## Prep (15 min, before recording)

1. **Stage the repo.** Fresh clone of claudebot itself at `~/work/demo`
   (meta: the bot fixes its own source on camera).
   Plant the bug — one line, visually obvious diff:
   ```bash
   cd ~/work/demo
   sed -i '' 's/_RAW_LIMIT = 3500/_RAW_LIMIT = 35/' claudebot/telegram/streaming.py
   .venv/bin/pytest -q   # confirm: a handful of chunking tests now fail
   ```
2. **Bot settings for speed** (in the chat, before recording):
   `/cd ~/work/demo` · `/model sonnet` · `/effort low` — fast first token,
   fast fix. `/new` for a clean context.
3. **Phone hygiene.** Clear the chat (or use a fresh chat), Do Not Disturb ON,
   full-ish battery, bot has a profile photo. Telegram in light or dark mode —
   match the terminal theme (dark on dark looks best with the brand).
4. **Terminal pane.** One window, big font (16–18pt), dark theme, prompt
   shortened (`PS1='\W $ '`). Pre-type `pytest -q` in history so re-running is
   one ↑+Enter.
5. **Recording setup.** Phone mirrored to the Mac via QuickTime (Movie
   Recording → camera dropdown → iPhone) for a crisp feed; terminal captured
   natively (Screen Studio / QuickTime). Compose side-by-side in the edit —
   phone left ~40%, terminal right ~60%.
6. **Dry run once.** Replies vary per take; do one rehearsal, then re-plant
   the bug (`git checkout .` + sed again) and shoot.

## 30-second master cut

| Time | Screen | Action / exact line | Overlay caption |
|---|---|---|---|
| 0–4s | Terminal | Run `pytest -q` → red: `4 failed, 62 passed` | "the build is broken — and I'm not at my desk" |
| 4–8s | Phone | Type and send: **`tests are failing — find the bug, fix it, run the suite`** → the 👀 reaction lands on your message | "texting my dev machine" |
| 8–20s | Phone (speed-ramp 2–3× through the middle) | Reply streams in: Claude names the file, the 🛑 Stop button visible under the streaming bubble | "the real Claude Code, on my machine, on my subscription" |
| 20–25s | Phone | Final bubble: fixed line + `66 passed` | — |
| 25–30s | Terminal | ↑+Enter `pytest -q` → all green. Beat. | end card: **claudebot.ve.ke** · `curl -fsSL claudebot.ve.ke/install.sh \| bash` |

Rules for the cut: no music until 20s (let it feel like a screen recording,
not an ad), one speed-ramp only, captions in a mono font, nothing animated
except the screens themselves.

## 75-second extended cut (adds three beats)

Insert after the master's 25s mark:

| Beat | Screen | Action / exact line |
|---|---|---|
| A — photo input | Phone | Send a screenshot of an error with caption **`what's breaking here?`** → Claude reads it and answers |
| B — runtime control | Phone | Send bare **`/model`** → the tap-to-set keyboard appears → tap `opus` → "model set to opus · fresh conversation" |
| C — the kill switch | Phone | Send **`refactor the whole streaming module`** → as it starts streaming, tap **🛑 Stop** → "🛑 Stopped." |

Beat C matters: showing you can yank the leash is the best 5 seconds of
security messaging available.

## Export targets

- **README**: GIF ≤ 12 MB (or mp4 — GitHub renders mp4 dragged into the README
  via a release asset link), 800px wide, loop.
- **X / Reddit**: mp4, 1080p, with the captions burned in.
- **Show HN**: don't attach video; the README GIF does the work when they
  click through.

## Lines to type, collected (copy-paste during the shoot)

```
tests are failing — find the bug, fix it, run the suite
what's breaking here?
/model
refactor the whole streaming module
```
