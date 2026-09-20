#!/usr/bin/env bash
# Android Use — one-command setup for the MCP server (the computer side).
# Sets up the Python environment and registers the server with Claude Code.
#
#   curl -fsSL https://raw.githubusercontent.com/bsaisuryacharan/android-use/main/install.sh | bash
# or, from a clone:
#   ./install.sh
set -euo pipefail

say()  { printf "\033[1;36m▸ %s\033[0m\n" "$1"; }
ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; }
warn() { printf "  \033[33m!\033[0m %s\n" "$1"; }
die()  { printf "  \033[31m✗ %s\033[0m\n" "$1"; exit 1; }

say "Android Use setup"

# 1. Locate the project (clone if run standalone)
if [ -f "pyproject.toml" ] && grep -q "android-use" pyproject.toml 2>/dev/null; then
  ROOT="$(pwd)"
else
  ROOT="${ANDROID_USE_DIR:-$HOME/android-use}"
  if [ ! -d "$ROOT/.git" ]; then
    command -v git >/dev/null || die "git is required"
    say "Cloning into $ROOT"
    git clone --depth 1 https://github.com/bsaisuryacharan/android-use "$ROOT"
  fi
  cd "$ROOT"
fi
ok "project at $ROOT"

# 2. Python environment
if command -v uv >/dev/null 2>&1; then
  say "Creating environment with uv"
  uv venv >/dev/null
  uv pip install -e . >/dev/null
  PY="$ROOT/.venv/bin/python"
else
  command -v python3 >/dev/null || die "python3 (3.10+) or uv is required"
  say "Creating environment with venv"
  python3 -m venv .venv
  ./.venv/bin/pip install -q -e .
  PY="$ROOT/.venv/bin/python"
fi
ok "dependencies installed"

# 3. adb check (optional but recommended for the quick-start path)
if command -v adb >/dev/null 2>&1; then
  DEV=$(adb devices | grep -cw device || true)
  if [ "$DEV" -gt 0 ]; then ok "adb sees $DEV device(s)"; else warn "adb found, but no device connected yet"; fi
else
  warn "adb not found — install Android platform-tools for the quick-start (ADB) path"
fi

# 4. Register with Claude Code if available
if command -v claude >/dev/null 2>&1; then
  say "Registering with Claude Code (user scope)"
  claude mcp remove android-use >/dev/null 2>&1 || true
  claude mcp add -s user android-use -- "$PY" -m android_use.server && ok "registered — restart Claude Code to load the tools"
else
  warn "Claude Code CLI not found. Register manually:"
  echo "     claude mcp add -s user android-use -- \"$PY\" -m android_use.server"
fi

echo
say "Done. Next:"
echo "  • Quick start (ADB):     see QUICKSTART.md"
echo "  • Fully remote (app):    see SETUP_APP.md and CONNECTOR.md"
echo "  • Security — READ FIRST: see SECURITY.md"
