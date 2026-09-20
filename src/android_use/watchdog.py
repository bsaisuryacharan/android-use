"""Watch the phone and say something when it goes quiet.

The failure this exists for is specific: the client app gets killed, Android
drops its accessibility service, and every tool starts reporting what looks
like a network error. Nothing tells you - you find out when you try to help
someone and cannot.

Run it alongside the MCP server:

    python -m android_use.watchdog            # poll every 60s, notify on change
    python -m android_use.watchdog --once     # single check, for cron
    python -m android_use.watchdog --recover  # also try to self-heal over adb
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import apps
from .adb_backend import get_backend, reset_backend

STATE_PATH = Path(
    os.environ.get(
        "ANDROID_USE_STATE",
        Path(os.environ.get("ANDROID_USE_CONFIG", Path.home() / ".android-use" / "config.json")).parent
        / "watchdog.json",
    )
)
PACKAGE = "com.androiduse.client"
SERVICE = f"{PACKAGE}/{PACKAGE}.ControlAccessibilityService"


@dataclass
class Health:
    ok: bool
    summary: str
    reason: str = ""

    @property
    def kind(self) -> str:
        """Coarse classification, so we only alert when the situation changes."""
        if self.ok:
            return "ok"
        low = self.reason.lower()
        if "accessibility" in low:
            return "service_off"
        if "not granted" in low or "grant" in low:
            return "not_granted"
        return "unreachable"


def check() -> Health:
    """Report on the phone app specifically, not on whatever adb is doing.

    Going through get_backend() here would fall back to adb and then report
    adb's complaint - telling the user to accept a USB debugging prompt when
    the real problem is that the phone is unreachable over the network.
    """
    reset_backend()
    cfg = apps.load_config()
    host, token = cfg.get("bridge_host"), cfg.get("bridge_token")

    if host and token:
        from .app_backend import AppBackend

        bridge = AppBackend(
            host,
            int(cfg.get("bridge_port") or 8765),
            token,
            proxy=cfg.get("bridge_proxy") or "",
        )
        status = bridge.status()
        if status.connected:
            return Health(True, "phone reachable via app")
        return Health(False, "phone app not usable", status.detail)

    # No app configured yet, so adb is the real transport and its state is
    # what matters.
    backend = get_backend()
    status = backend.status()
    if status.connected:
        return Health(True, f"phone reachable via {backend.name}")
    return Health(False, "phone unreachable", status.detail)


def notify(title: str, message: str) -> None:
    """macOS notification. Best effort - never let this break the loop."""
    if sys.platform != "darwin":
        print(f"[{title}] {message}")
        return
    try:
        subprocess.run(
            [
                "osascript", "-e",
                f'display notification {json.dumps(message)} with title {json.dumps(title)}',
            ],
            capture_output=True, timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        pass


def _adb_available() -> bool:
    try:
        from . import adb

        return any(d.ready for d in adb.list_devices())
    except Exception:
        return False


def attempt_recovery() -> str:
    """Put the accessibility service back.

    Tries the phone's own /repair route first: it works over any network,
    including cellular, and needs no cable. Falls back to adb only if the
    bridge itself cannot be reached.
    """
    cfg = apps.load_config()
    host, token = cfg.get("bridge_host"), cfg.get("bridge_token")
    if host and token:
        from .app_backend import AppBackend

        bridge = AppBackend(
            host, int(cfg.get("bridge_port") or 8765), token,
            proxy=cfg.get("bridge_proxy") or "",
        )
        try:
            if bridge.repair().get("service_running"):
                return "phone repaired itself over the network (no cable needed)"
        except Exception as exc:
            last = str(exc)[:120]
        else:
            last = "repair ran but the service did not come back"
    else:
        last = "no bridge configured"

    if not _adb_available():
        from . import wireless

        ok, _ = wireless.ensure_connected()
        if not ok:
            return f"could not repair remotely ({last}); no adb route either"
    try:
        from . import adb

        current = adb.shell("settings get secure enabled_accessibility_services").strip()
        if current in ("null", "None"):
            current = ""
        # Android revokes a sideloaded app's accessibility access unless this
        # appop is allowed; re-assert it or the repair will not hold.
        try:
            adb.shell(f"cmd appops set {PACKAGE} ACCESS_RESTRICTED_SETTINGS allow")
        except Exception:
            pass
        if SERVICE not in current:
            merged = f"{current}:{SERVICE}" if current else SERVICE
            # Append, never replace: overwriting would disable the user's other
            # accessibility services.
            adb.shell(f'settings put secure enabled_accessibility_services "{merged}"')
        adb.shell("settings put secure accessibility_enabled 1")
        time.sleep(4)
        return "re-enabled the accessibility service over adb"
    except Exception as exc:
        return f"recovery failed: {exc}"


def _load() -> dict:
    if STATE_PATH.is_file():
        try:
            return json.loads(STATE_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def _save(state: dict) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, indent=2))
    except OSError:
        pass


def run_once(recover: bool = False, quiet: bool = False) -> Health:
    health = check()
    state = _load()
    previous = state.get("kind")
    stamp = datetime.now().isoformat(timespec="seconds")

    if health.kind != previous:
        # Only speak up on a transition, so a phone that is off overnight does
        # not produce hundreds of identical alerts.
        if health.ok:
            notify("Phone back online", "android-use can reach the phone again.")
        else:
            notify("Phone unreachable", health.reason[:180] or health.summary)
        state["changed_at"] = stamp

    if not health.ok and recover and health.kind == "service_off":
        result = attempt_recovery()
        state["last_recovery"] = f"{stamp}: {result}"
        if not quiet:
            print(f"recovery: {result}")
        health = check()

    state.update(
        {"kind": health.kind, "ok": health.ok, "summary": health.summary,
         "reason": health.reason, "checked_at": stamp}
    )
    _save(state)
    if not quiet:
        mark = "OK  " if health.ok else "DOWN"
        print(f"{stamp}  {mark}  {health.summary}"
              + (f" - {health.reason[:90]}" if health.reason else ""))
    return health


def last_report() -> str:
    state = _load()
    if not state:
        return "The watchdog has not run yet."
    lines = [
        f"Last checked: {state.get('checked_at', 'never')}",
        f"State: {'OK' if state.get('ok') else 'DOWN'} - {state.get('summary', '')}",
    ]
    if state.get("reason"):
        lines.append(f"Reason: {state['reason']}")
    if state.get("changed_at"):
        lines.append(f"Changed at: {state['changed_at']}")
    if state.get("last_recovery"):
        lines.append(f"Last recovery attempt: {state['last_recovery']}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch the phone bridge.")
    parser.add_argument("--interval", type=int, default=60, help="seconds between checks")
    parser.add_argument("--once", action="store_true", help="check once and exit")
    parser.add_argument("--recover", action="store_true",
                        help="try to re-enable the service over adb when it drops")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    if args.once:
        health = run_once(recover=args.recover, quiet=args.quiet)
        sys.exit(0 if health.ok else 1)

    print(f"Watching the phone every {args.interval}s. Ctrl-C to stop.")
    try:
        while True:
            run_once(recover=args.recover, quiet=args.quiet)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
