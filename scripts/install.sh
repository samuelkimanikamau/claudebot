#!/usr/bin/env bash
# claudebot installer — works as a local `./scripts/install.sh` or piped:
#   curl -fsSL .../scripts/install.sh | bash
#
# Creates an isolated venv at ~/.claudebot/venv, links `claudebot` into
# ~/.local/bin, then runs the setup wizard. No root required.
set -euo pipefail

REPO_URL="${CLAUDEBOT_REPO:-https://github.com/samuelkimanikamau/claudebot.git}"
STATE_DIR="${CLAUDEBOT_STATE_DIR:-$HOME/.claudebot}"
VENV="$STATE_DIR/venv"
BIN_DIR="$HOME/.local/bin"

c_bold=$'\033[1m'; c_cyan=$'\033[36m'; c_green=$'\033[32m'; c_yellow=$'\033[33m'; c_dim=$'\033[2m'; c_reset=$'\033[0m'
info()  { printf '%s\n' "${c_cyan}•${c_reset} $*"; }
ok()    { printf '%s\n' "${c_green}✓${c_reset} $*"; }
warn()  { printf '%s\n' "${c_yellow}⚠${c_reset}  $*"; }
die()   { printf '%s\n' "${c_yellow}✗ $*${c_reset}" >&2; exit 1; }

printf '\n%s\n' "${c_bold}${c_cyan}  claudebot installer${c_reset}"
printf '%s\n\n' "${c_dim}  A Telegram bot that drives the real Claude Code — no API key.${c_reset}"

# --- prerequisites ----------------------------------------------------------
# Pick the newest suitable Python. macOS often ships /usr/bin/python3 as 3.9
# while a 3.10+ build lives under a versioned name (python3.13, etc.), so probe
# versioned binaries before the bare `python3` rather than failing on the first.
PY=""
for cand in python3.14 python3.13 python3.12 python3.11 python3.10 python3 python; do
  p="$(command -v "$cand" 2>/dev/null)" || continue
  if "$p" -c 'import sys;sys.exit(0 if sys.version_info[:2]>=(3,10) else 1)' 2>/dev/null; then
    PY="$p"; break
  fi
done
if [ -z "$PY" ]; then
  saw="$(python3 -V 2>&1 || echo 'none on PATH')"
  warn "No Python 3.10+ found (saw: $saw). Install a newer Python, then re-run:"
  warn "   macOS (Homebrew):  brew install python@3.12"
  warn "   Linux (apt):       sudo apt install python3.12 python3.12-venv"
  warn "   Any OS (pyenv):    pyenv install 3.12 && pyenv global 3.12"
  die "Python 3.10+ is required."
fi
PYVER="$("$PY" -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
ok "python $PYVER ($PY)"

if ! command -v claude >/dev/null 2>&1; then
  warn "Claude Code (\`claude\`) is not on PATH — install it and run \`claude auth login\`:"
  warn "   npm install -g @anthropic-ai/claude-code"
else
  ok "claude $(claude --version 2>/dev/null | head -n1)"
fi

# --- locate the source ------------------------------------------------------
SRC=""
if [ -n "${BASH_SOURCE[0]:-}" ]; then
  maybe="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd || true)"
  [ -n "$maybe" ] && [ -f "$maybe/pyproject.toml" ] && SRC="$maybe"
fi
if [ -z "$SRC" ]; then
  SRC="$STATE_DIR/src"
  if [ -d "$SRC/.git" ]; then
    info "Updating source in $SRC"
    git -C "$SRC" pull --ff-only --quiet || warn "git pull failed; using existing checkout"
  else
    command -v git >/dev/null 2>&1 || die "git not found and no local source. Install git, or clone the repo and run scripts/install.sh."
    info "Cloning $REPO_URL"
    mkdir -p "$STATE_DIR"
    git clone --depth 1 "$REPO_URL" "$SRC" --quiet
  fi
fi
ok "source: $SRC"

# --- venv + install ---------------------------------------------------------
info "Creating venv at $VENV"
venv_err="$(mktemp)"
if ! "$PY" -m venv "$VENV" 2>"$venv_err"; then
  sed 's/^/    /' "$venv_err" >&2 2>/dev/null || true
  rm -f "$venv_err"
  warn "Could not create a virtualenv with $PY ($PYVER)."
  warn "On Debian/Ubuntu the venv module ships in a separate package — install it and re-run:"
  warn "   sudo apt install python$PYVER-venv"
  die "venv creation failed."
fi
rm -f "$venv_err"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
info "Installing claudebot"
"$VENV/bin/python" -m pip install --quiet -e "$SRC"
ok "installed claudebot $("$VENV/bin/claudebot" --version | awk '{print $2}')"

# --- link onto PATH ---------------------------------------------------------
mkdir -p "$BIN_DIR"
ln -sf "$VENV/bin/claudebot" "$BIN_DIR/claudebot"
ok "linked $BIN_DIR/claudebot"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) warn "$BIN_DIR is not on your PATH. Add this to your shell profile:"
     warn "   export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

# --- setup wizard -----------------------------------------------------------
# When piped (`curl … | bash`) our stdin is the script pipe, not the keyboard,
# so reattach the terminal for the wizard's prompts. With no terminal at all
# (CI, nested pipes) skip it — the wizard would only bail anyway.
printf '\n'
if [ -r /dev/tty ]; then
  info "Launching setup…"
  "$VENV/bin/claudebot" setup </dev/tty || warn "Setup did not finish — run \`claudebot setup\` any time."
else
  warn "No terminal attached — finish setup later with: claudebot setup"
fi

printf '\n%s\n' "${c_green}${c_bold}Done.${c_reset}"
printf '%s\n'   "  ${c_cyan}claudebot doctor${c_reset}            verify everything"
printf '%s\n'   "  ${c_cyan}claudebot run${c_reset}               run it now (foreground)"
printf '%s\n\n' "  ${c_cyan}claudebot service install${c_reset}   keep it always-on"
