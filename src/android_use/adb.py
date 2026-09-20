"""Thin wrapper around the adb binary."""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass


class AdbError(RuntimeError):
    pass


def _find_adb() -> str:
    override = os.environ.get("ADB_PATH")
    if override:
        return override
    found = shutil.which("adb")
    if found:
        return found
    # Common SDK locations that are often missing from a GUI app's PATH.
    for candidate in (
        os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"),
        os.path.expanduser("~/Android/Sdk/platform-tools/adb"),
        "/usr/local/bin/adb",
        "/opt/homebrew/bin/adb",
    ):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return ""  # not found — resolved lazily so importing the package never fails


ADB = _find_adb()
SERIAL = os.environ.get("ANDROID_SERIAL")


def _require_adb() -> str:
    """Return the adb path, or raise only when adb is actually needed."""
    if ADB:
        return ADB
    raise AdbError(
        "adb not found. Install Android platform-tools, or set ADB_PATH to the "
        "adb binary. (Not needed for the companion-app transport.)"
    )


@dataclass
class Device:
    serial: str
    state: str
    model: str = ""

    @property
    def ready(self) -> bool:
        return self.state == "device"


def _base_cmd() -> list[str]:
    cmd = [_require_adb()]
    if SERIAL:
        cmd += ["-s", SERIAL]
    return cmd


def list_devices() -> list[Device]:
    out = subprocess.run(
        [_require_adb(), "devices", "-l"], capture_output=True, text=True, timeout=30
    ).stdout
    devices: list[Device] = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        serial, state = parts[0], parts[1]
        model = ""
        for p in parts[2:]:
            if p.startswith("model:"):
                model = p.split(":", 1)[1]
        devices.append(Device(serial=serial, state=state, model=model))
    return devices


def _hardware_serial(transport: str) -> str:
    """Ask a transport which physical phone it leads to."""
    try:
        proc = subprocess.run(
            [_require_adb(), "-s", transport, "shell", "getprop", "ro.serialno"],
            capture_output=True, text=True, timeout=15,
        )
        return proc.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        return ""


def require_device() -> Device:
    """Return the single usable device, or raise with an actionable message."""
    devices = list_devices()
    ready = [d for d in devices if d.ready]
    if SERIAL:
        # With both a cable and Wi-Fi attached, report the transport we are
        # actually driving rather than whichever adb happens to list first.
        picked = [d for d in ready if d.serial == SERIAL]
        if picked:
            return picked[0]
        if devices and not ready:
            pass  # fall through to the diagnostics below
        elif ready:
            raise AdbError(
                f"ANDROID_SERIAL is set to {SERIAL}, but that device is not "
                f"connected. Available: {', '.join(d.serial for d in ready)}"
            )
    if not ready:
        unauthorized = [d for d in devices if d.state == "unauthorized"]
        if unauthorized:
            raise AdbError(
                "Phone is connected but not authorized. Unlock the phone and tap "
                "'Allow' on the 'Allow USB debugging?' prompt, then retry."
            )
        offline = [d for d in devices if d.state == "offline"]
        if offline:
            raise AdbError(
                "Phone is listed but offline. Unplug and replug the USB cable, "
                "or run: adb kill-server && adb devices"
            )
        raise AdbError(
            "No Android device found. Check that the phone is plugged in, USB mode is "
            "set to 'File transfer' (not charge-only), and USB debugging is enabled in "
            "Developer options."
        )
    if len(ready) > 1 and not SERIAL:
        # Right after enabling wireless the same phone is attached twice, once
        # by cable and once over Wi-Fi. That is not an ambiguous choice, so
        # resolve it instead of making the user unplug.
        by_hardware: dict[str, list[Device]] = {}
        for d in ready:
            by_hardware.setdefault(_hardware_serial(d.serial) or d.serial, []).append(d)
        if len(by_hardware) == 1:
            same = next(iter(by_hardware.values()))
            # Prefer the cable: lower latency and it cannot drop mid-task.
            usb_first = sorted(same, key=lambda d: ":" in d.serial)
            return usb_first[0]
        names = ", ".join(f"{d.serial} ({d.model})" for d in ready)
        raise AdbError(
            f"Multiple devices connected: {names}. Set ANDROID_SERIAL to pick one."
        )
    return ready[0]


def shell(command: str, timeout: int = 30) -> str:
    """Run a shell command on the device and return stdout as text."""
    proc = subprocess.run(
        _base_cmd() + ["shell", command],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise AdbError(f"adb shell {command!r} failed: {proc.stderr.strip()}")
    return proc.stdout


def shell_bytes(command: str, timeout: int = 60) -> bytes:
    """Run a shell command and return raw stdout bytes (for screencap)."""
    proc = subprocess.run(
        _base_cmd() + ["exec-out", command],
        capture_output=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise AdbError(
            f"adb exec-out {command!r} failed: {proc.stderr.decode(errors='replace').strip()}"
        )
    return proc.stdout
