#!/bin/bash
# Interactive finishing steps for android-use.
# Anything needing your password, your eyes, or your decision lives here.

cd "$(dirname "$0")" || exit 1
PY="$PWD/.venv/bin/python"
export PYTHONPATH="$PWD/src"
TS=/usr/local/bin/tailscale
bold() { printf "\033[1m%s\033[0m\n" "$1"; }
ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; }
bad()  { printf "  \033[31m✗\033[0m %s\n" "$1"; }

echo; bold "═══ android-use · finishing steps ═══"; echo

bold "1. Current state"
if curl -s --max-time 5 http://127.0.0.1:8080/healthz >/dev/null 2>&1; then
  ok "MCP HTTP server running on :8080"
else
  bad "MCP HTTP server NOT running - start it with:"
  echo "      PYTHONPATH=src .venv/bin/python -m android_use.http_server --port 8080 &"
fi
if "$TS" funnel status 2>/dev/null | grep -q "Funnel on"; then
  ok "Tailscale Funnel is on"
else
  bad "Funnel is off. Turn it on with:  tailscale funnel --bg --https=443 8080"
fi
"$PY" -m android_use.watchdog --once 2>/dev/null | sed 's/^/  /'
echo

bold "2. Your claude.ai connector URL"
echo "   Treat this like a password. Anyone with it can control your phone."
echo
"$PY" -m android_use.http_server --show-url 2>/dev/null | sed 's/^/   /'
echo
echo "   Paste the connector URL into:  claude.ai → Settings → Connectors"
echo "   → Add custom connector"
echo
read -r -p "   Copy it to your clipboard now? [y/N] " yn
if [[ "$yn" =~ ^[Yy]$ ]]; then
  "$PY" -m android_use.http_server --show-url 2>/dev/null \
    | grep -o 'https://[^ ]*' | tr -d '\n' | pbcopy && ok "copied to clipboard"
fi
echo

bold "3. Survive a reboot?"
echo "   Right now the MCP server and Funnel stop if you reboot."
echo "   This installs a user LaunchAgent (no sudo) that restarts the server."
read -r -p "   Install it? [y/N] " yn
if [[ "$yn" =~ ^[Yy]$ ]]; then
  PLIST="$HOME/Library/LaunchAgents/com.androiduse.mcphttp.plist"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.androiduse.mcphttp</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string><string>-m</string><string>android_use.http_server</string>
    <string>--host</string><string>127.0.0.1</string><string>--port</string><string>8080</string>
  </array>
  <key>EnvironmentVariables</key><dict><key>PYTHONPATH</key><string>$PWD/src</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$HOME/.android-use/mcp-http.log</string>
  <key>StandardErrorPath</key><string>$HOME/.android-use/mcp-http.log</string>
</dict></plist>
EOF
  launchctl unload "$PLIST" 2>/dev/null
  launchctl load "$PLIST" 2>/dev/null && ok "LaunchAgent installed - server restarts on login"
  echo "   Note: Tailscale Funnel persists on its own once set."
fi
echo

bold "4. On the PHONE - I cannot do these for you"
echo "   These are the difference between 'works while you watch' and"
echo "   'works when grandma is in another city'."
echo
echo "   a) Settings → Battery → Background power consumption management"
echo "      → allow BOTH 'Android Use' and 'Tailscale' to run in background"
echo "      (Guards against the process being killed outright.)"
echo
echo "      NOTE: the repeated 'accessibility service turned itself off'"
echo "      problem was NOT this. It was Android revoking restricted"
echo "      settings from a sideloaded app. Already fixed with:"
echo "        adb shell cmd appops set com.androiduse.client \\"
echo "          ACCESS_RESTRICTED_SETTINGS allow"
echo "      Re-apply that after any reinstall of the APK."
echo
echo "   b) Open 'Android Use' → Grant control for 8 hours (or as needed)"
echo
echo "   c) Dismiss any leftover 'Allow wireless debugging' dialog."
echo
bold "Done. Close this window when finished."
echo
