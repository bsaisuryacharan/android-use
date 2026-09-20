#!/bin/bash
# Per-customer isolation for android-use.
#
# Each customer gets their own server process, their own config file, their own
# URL secret and their own port. Isolation is structural: there is no shared
# state and no "which tenant is this?" check that could be got wrong, because a
# process only ever has one customer's config.
#
#   ./tenant.sh create <id> [port]   provision a customer
#   ./tenant.sh start  <id>          run their server
#   ./tenant.sh stop   <id>
#   ./tenant.sh list
#   ./tenant.sh url    <id>          print their connector URL
#   ./tenant.sh expose <id>          tailscale serve path -> their port

set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
TENANTS="$ROOT/tenants"
PY="$ROOT/.venv/bin/python"
TS=/usr/local/bin/tailscale

die() { echo "error: $*" >&2; exit 1; }
tenant_dir() { echo "$TENANTS/$1"; }
cfg_of()     { echo "$TENANTS/$1/config.json"; }
port_of()    { [ -f "$TENANTS/$1/port" ] && cat "$TENANTS/$1/port" || echo ""; }

cmd_create() {
  local id="${1:-}" port="${2:-}"
  [ -n "$id" ] || die "usage: tenant.sh create <id> [port]"
  [[ "$id" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || die "id must be lowercase letters, digits, - or _"
  local dir; dir="$(tenant_dir "$id")"
  [ -d "$dir" ] && die "tenant '$id' already exists"

  if [ -z "$port" ]; then
    port=8100
    while [ -d "$TENANTS" ] && grep -rqs "^$port$" "$TENANTS"/*/port 2>/dev/null; do
      port=$((port+1))
    done
  fi

  mkdir -p "$dir"
  echo "$port" > "$dir/port"
  # An empty device list, not a copy of anyone else's.
  printf '{\n  "devices": {},\n  "default_device": ""\n}\n' > "$dir/config.json"
  chmod 700 "$dir"; chmod 600 "$dir/config.json"

  # Generating the secret here means it never passes through a shared file.
  ANDROID_USE_CONFIG="$(cfg_of "$id")" PYTHONPATH="$ROOT/src" \
    "$PY" -m android_use.http_server --show-url >/dev/null 2>&1 || true

  echo "created tenant '$id'"
  echo "  config: $(cfg_of "$id")"
  echo "  port:   $port"
  echo "Next: ./tenant.sh start $id"
}

cmd_start() {
  local id="${1:-}"; [ -n "$id" ] || die "usage: tenant.sh start <id>"
  local dir; dir="$(tenant_dir "$id")"; [ -d "$dir" ] || die "no tenant '$id'"
  local port; port="$(port_of "$id")"
  if [ -f "$dir/pid" ] && kill -0 "$(cat "$dir/pid")" 2>/dev/null; then
    echo "tenant '$id' already running (pid $(cat "$dir/pid"))"; return
  fi
  ANDROID_USE_CONFIG="$(cfg_of "$id")" \
  ANDROID_USE_STATE="$dir/watchdog.json" \
  PYTHONPATH="$ROOT/src" \
    nohup "$PY" -m android_use.http_server --host 127.0.0.1 --port "$port" \
      > "$dir/server.log" 2>&1 &
  echo $! > "$dir/pid"
  sleep 3
  if curl -s --max-time 5 "http://127.0.0.1:$port/healthz" >/dev/null; then
    echo "tenant '$id' running on 127.0.0.1:$port (pid $(cat "$dir/pid"))"
  else
    echo "tenant '$id' started but not healthy - see $dir/server.log" >&2
  fi
}

cmd_stop() {
  local id="${1:-}"; [ -n "$id" ] || die "usage: tenant.sh stop <id>"
  local dir; dir="$(tenant_dir "$id")"
  [ -f "$dir/pid" ] || { echo "tenant '$id' not running"; return; }
  kill "$(cat "$dir/pid")" 2>/dev/null || true
  rm -f "$dir/pid"
  echo "stopped '$id'"
}

cmd_list() {
  [ -d "$TENANTS" ] || { echo "no tenants yet"; return; }
  printf "%-14s %-6s %-9s %s\n" ID PORT STATE DEVICES
  for dir in "$TENANTS"/*/; do
    [ -d "$dir" ] || continue
    local id port state count
    id="$(basename "$dir")"; port="$(port_of "$id")"
    if [ -f "$dir/pid" ] && kill -0 "$(cat "$dir/pid")" 2>/dev/null; then
      state="running"; else state="stopped"; fi
    count="$("$PY" -c "import json,sys;print(len(json.load(open(sys.argv[1])).get('devices',{})))" "$dir/config.json" 2>/dev/null || echo "?")"
    printf "%-14s %-6s %-9s %s\n" "$id" "$port" "$state" "$count"
  done
}

cmd_url() {
  local id="${1:-}"; [ -n "$id" ] || die "usage: tenant.sh url <id>"
  ANDROID_USE_CONFIG="$(cfg_of "$id")" PYTHONPATH="$ROOT/src" \
    "$PY" -m android_use.http_server --show-url
}

cmd_expose() {
  local id="${1:-}"; [ -n "$id" ] || die "usage: tenant.sh expose <id>"
  local port; port="$(port_of "$id")"
  echo "This publishes tenant '$id' to the public internet. Run:"
  echo
  echo "  $TS funnel --bg --set-path /t/$id http://127.0.0.1:$port"
  echo
  echo "Deliberately not run for you: exposing a service that controls phones"
  echo "should be an explicit act, not a side effect of a script."
}

case "${1:-}" in
  create) shift; cmd_create "$@" ;;
  start)  shift; cmd_start "$@" ;;
  stop)   shift; cmd_stop "$@" ;;
  list)   shift; cmd_list "$@" ;;
  url)    shift; cmd_url "$@" ;;
  expose) shift; cmd_expose "$@" ;;
  *) echo "usage: tenant.sh {create|start|stop|list|url|expose} [args]"; exit 1 ;;
esac
