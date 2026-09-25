"""Watch the phones and say something when one goes quiet.

The failure this exists for is specific: the client app gets killed, Android
drops its accessibility service, and every tool starts reporting what looks
like a network error. Nothing tells you - you find out when you try to help
someone and cannot.

Every registered phone is watched separately, so a phone that is off overnight
does not hide the one that just broke, and alerts name the phone.

Run it alongside the MCP server:

    python -m android_use.watchdog            # poll every 60s, notify on change
    python -m android_use.watchdog --once     # single check, for cron
    python -m android_use.watchdog --recover  # also try to self-heal
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .adb_backend import adb_fallback, reset_backend

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


def _bridge(phone):
    from .app_backend import AppBackend

    return AppBackend(phone.host, phone.port, phone.token, proxy=phone.proxy)


def check_all() -> dict[str, Health]:
    """Health of every registered phone, by name.

    Reports on the phone app specifically, not on whatever adb is doing: going
    through the generic backend would fall back to adb and report adb's
    complaint - telling the user to accept a USB debugging prompt when the real
    problem is that the phone is unreachable over the network.
    """
    from . import devices

    reset_backend()
    phones = devices.load_phones()
    results: dict[str, Health] = {}
    for name, phone in sorted(phones.items()):
        status = _bridge(phone).status()
        results[name] = (
            Health(True, "phone reachable via app") if status.connected
            else Health(False, "phone app not usable", status.detail)
        )
    if results:
        return results

    # No app configured yet, so adb is the real transport and its state is
    # what matters.
    status = adb_fallback().status()
    results["adb"] = (
        Health(True, "phone reachable via adb") if status.connected
        else Health(False, "phone unreachable", status.detail)
    )
    return results


def check() -> Health:
    """One verdict for everything: fine only if every phone is fine."""
    results = check_all()
    down = {name: h for name, h in results.items() if not h.ok}
    if not down:
        return Health(True, f"{len(results)} phone(s) reachable")
    if len(results) == 1:
        return next(iter(down.values()))
    names = ", ".join(sorted(down))
    first = next(iter(down.values()))
    return Health(False, f"{len(down)} of {len(results)} phone(s) down: {names}", first.reason)


def notify(title: str, message: str) -> None:
    """Desktop notification. Best effort - never let this break the loop."""
    try:
        if sys.platform == "darwin":
            subprocess.run(
                [
                    "osascript", "-e",
                    f'display notification {json.dumps(message)} with title {json.dumps(title)}',
                ],
                capture_output=True, timeout=10,
            )
            return
        if shutil.which("notify-send"):
            subprocess.run(["notify-send", title, message], capture_output=True, timeout=10)
            return
    except (subprocess.SubprocessError, OSError):
        pass
    print(f"[{title}] {message}")


def _adb_available() -> bool:
    try:
        from . import adb

        return any(d.ready for d in adb.list_devices())
    except Exception:
        return False


def attempt_recovery(name: str = "") -> str:
    """Put a phone's accessibility service back.

    Tries the phone's own /repair route first: it works over any network,
    including cellular, and needs no cable. Falls back to adb only when a
    single phone is configured - with several, there is no telling which one
    the cable is plugged into.
    """
    from . import devices

    phones = devices.load_phones()
    phone = phones.get(name) if name else (next(iter(phones.values())) if len(phones) == 1 else None)
    last = "no bridge configured"
    if phone is not None:
        try:
            if _bridge(phone).repair().get("service_running"):
                return "phone repaired itself over the network (no cable needed)"
            last = "repair ran but the service did not come back"
        except Exception as exc:  # noqa: BLE001 - report, then try adb
            last = str(exc)[:120]

    if len(phones) > 1:
        return f"could not repair remotely ({last})"
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
    results = check_all()
    state = _load()
    phones_state: dict = state.setdefault("phones", {})
    stamp = datetime.now().isoformat(timespec="seconds")

    for name, health in results.items():
        entry = phones_state.setdefault(name, {})
        # Only speak up on a transition, so a phone that is off overnight does
        # not produce hundreds of identical alerts.
        if health.kind != entry.get("kind"):
            if health.ok:
                notify(f"{name}: back online", "android-use can reach the phone again.")
            else:
                notify(f"{name}: unreachable", health.reason[:180] or health.summary)
            entry["changed_at"] = stamp

        if not health.ok and recover and health.kind == "service_off":
            outcome = attempt_recovery("" if name == "adb" else name)
            entry["last_recovery"] = f"{stamp}: {outcome}"
            if not quiet:
                print(f"{name}: recovery: {outcome}")
            health = check_all().get(name, health)
            results[name] = health

        entry.update({"kind": health.kind, "ok": health.ok, "summary": health.summary,
                      "reason": health.reason, "checked_at": stamp})
        if not quiet:
            mark = "OK  " if health.ok else "DOWN"
            print(f"{stamp}  {mark}  {name}: {health.summary}"
                  + (f" - {health.reason[:90]}" if health.reason else ""))

    # Phones removed from the registry should not linger in the report.
    for gone in set(phones_state) - set(results):
        phones_state.pop(gone, None)
    overall = all(h.ok for h in results.values())
    state.update({"ok": overall, "checked_at": stamp})
    _save(state)
    down = sorted(n for n, h in results.items() if not h.ok)
    if not down:
        return Health(True, f"{len(results)} phone(s) reachable")
    first = results[down[0]]
    return Health(False, f"down: {', '.join(down)}", first.reason)


def last_report() -> str:
    state = _load()
    phones = state.get("phones") or {}
    if not phones:
        # Older state files described a single phone at the top level.
        if state.get("checked_at") and "summary" in state:
            phones = {"phone": state}
        else:
            return "The watchdog has not run yet."
    lines = [f"Last checked: {state.get('checked_at', 'never')}"]
    for name, entry in sorted(phones.items()):
        lines.append("")
        lines.append(f"{name}: {'OK' if entry.get('ok') else 'DOWN'} - {entry.get('summary', '')}")
        if entry.get("reason"):
            lines.append(f"  Reason: {entry['reason']}")
        if entry.get("changed_at"):
            lines.append(f"  Changed at: {entry['changed_at']}")
        if entry.get("last_recovery"):
            lines.append(f"  Last recovery attempt: {entry['last_recovery']}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch the phone bridge.")
    parser.add_argument("--interval", type=int, default=60, help="seconds between checks")
    parser.add_argument("--once", action="store_true", help="check once and exit")
    parser.add_argument("--recover", action="store_true",
                        help="try to re-enable the service when it drops")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    if args.once:
        health = run_once(recover=args.recover, quiet=args.quiet)
        sys.exit(0 if health.ok else 1)

    print(f"Watching the phones every {args.interval}s. Ctrl-C to stop.")
    try:
        while True:
            run_once(recover=args.recover, quiet=args.quiet)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
