"""Wireless ADB: find the phone on the LAN and connect to it without a cable.

Android 11+ advertises wireless debugging over mDNS as `_adb-tls-connect._tcp`.
The port changes every time wireless debugging is toggled, so we always
discover it rather than remembering an address.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from . import adb

CONNECT_SVC = "_adb-tls-connect._tcp"
PAIRING_SVC = "_adb-tls-pairing._tcp"
STATE_PATH = Path.home() / ".android-use" / "wireless.json"

# adb mdns services prints: <name>\t<service type>\t<host>:<port>
_SVC_RE = re.compile(r"^(\S+)\s+(_adb-tls-\w+\._tcp)\.?\s+(\S+):(\d+)\s*$")
# The service name embeds the device serial: adb-<serial>-<random>
_SERIAL_RE = re.compile(r"^adb-(.+)-[A-Za-z0-9]+$")


@dataclass
class Discovered:
    name: str
    service: str
    host: str
    port: int

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"

    @property
    def serial(self) -> str:
        m = _SERIAL_RE.match(self.name)
        return m.group(1) if m else ""

    @property
    def is_pairing(self) -> bool:
        return self.service == PAIRING_SVC


def _run(args: list[str], timeout: int = 30) -> tuple[int, str]:
    proc = subprocess.run(
        [adb._require_adb()] + args, capture_output=True, text=True, timeout=timeout
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def discover(timeout: int = 10) -> list[Discovered]:
    """List phones advertising wireless debugging on this network."""
    try:
        _, out = _run(["mdns", "services"], timeout=timeout)
    except subprocess.TimeoutExpired:
        return []
    found: list[Discovered] = []
    for line in out.splitlines():
        m = _SVC_RE.match(line.strip())
        if m:
            found.append(
                Discovered(
                    name=m.group(1),
                    service=m.group(2),
                    host=m.group(3),
                    port=int(m.group(4)),
                )
            )
    return found


def _load_state() -> dict:
    if STATE_PATH.is_file():
        try:
            return json.loads(STATE_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def _save_state(state: dict) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, indent=2))
    except OSError:
        pass  # remembering the device is a convenience, not a requirement


def remember(serial: str, address: str = "") -> None:
    state = _load_state()
    known = set(state.get("paired_serials", []))
    known.add(serial)
    state["paired_serials"] = sorted(known)
    if address:
        state["last_address"] = address
    _save_state(state)


def last_address() -> str:
    return _load_state().get("last_address", "")


def known_serials() -> list[str]:
    return list(_load_state().get("paired_serials", []))


def pair(address: str, code: str) -> str:
    """Pair with a phone using the 6-digit code from its Wireless debugging screen.

    `address` is the host:port shown *under the pairing code dialog* - it is a
    different port from the one used to connect.
    """
    rc, out = _run(["pair", address, code], timeout=45)
    if rc != 0 or "Successfully paired" not in out:
        return f"Pairing failed: {out or 'no output from adb'}"
    m = re.search(r"guid\s+([\w.-]+)", out)
    if m:
        serial = m.group(1).replace("adb-", "").rsplit("-", 1)[0]
        remember(serial)
    return f"Paired successfully. {out}"


def connect(address: str) -> tuple[bool, str]:
    rc, out = _run(["connect", address], timeout=30)
    ok = rc == 0 and "connected to" in out.lower() and "cannot" not in out.lower()
    return ok, out


def disconnect(address: str = "") -> str:
    _, out = _run(["disconnect"] + ([address] if address else []))
    return out or "disconnected"


def is_wireless(serial: str) -> bool:
    return ":" in serial and serial.count(".") >= 3


def auto_connect() -> tuple[bool, str]:
    """Find and connect to a previously paired phone on this network.

    Called when no device is attached, so a cable is never needed after the
    one-time pairing.
    """
    # A port we have actually connected on beats anything mDNS advertises: the
    # `adb tcpip` port is not advertised at all, and the advertised native
    # wireless-debugging port needs a pairing we may not have.
    previous = last_address()
    if previous:
        ok, _ = connect(previous)
        if ok and wait_authorized(previous, timeout=8.0):
            return True, f"Reconnected to {previous}"

    services = [d for d in discover() if not d.is_pairing]
    if not services:
        return False, (
            "No phone is advertising wireless debugging on this network. On the "
            "phone: Settings > Developer options > Wireless debugging > ON, and "
            "make sure it is on the same Wi-Fi as this computer."
        )

    known = set(known_serials())
    # Prefer a phone we have paired with before; otherwise try them all, since
    # adb may still hold a pairing we did not record.
    ordered = sorted(services, key=lambda d: d.serial not in known)

    failures = []
    for svc in ordered:
        ok, out = connect(svc.address)
        if ok:
            remember(svc.serial or svc.address, svc.address)
            return True, f"Connected wirelessly to {svc.address}"
        failures.append(f"{svc.address}: {out}")

    return False, (
        "Found a phone advertising wireless debugging, but could not connect. "
        "This usually means it has not been paired with this computer yet - run "
        "pair_wireless with the code from the phone's 'Pair device with pairing "
        "code' screen.\n" + "\n".join(failures)
    )


def _hw_serial(transport: str) -> str:
    try:
        _, out = _run(["-s", transport, "shell", "getprop", "ro.serialno"], timeout=10)
        return out.strip()
    except subprocess.TimeoutExpired:
        return ""


def ensure_connected(retries: int = 1) -> tuple[bool, str]:
    """Return an already-attached device, or try to bring one up wirelessly."""
    for attempt in range(retries + 1):
        attached = [d for d in adb.list_devices() if d.ready]
        if attached:
            # Record a working wireless address as we see it, so a later drop
            # can be repaired without a cable or a fresh pairing code.
            for d in attached:
                if is_wireless(d.serial) and d.serial != last_address():
                    remember(_hw_serial(d.serial) or d.serial, d.serial)
            return True, "already connected"
        ok, msg = auto_connect()
        if ok:
            time.sleep(1.0)  # let adb finish registering the transport
            if any(d.ready for d in adb.list_devices()):
                return True, msg
        if attempt >= retries:
            return False, msg
    return False, "could not connect"


def wait_authorized(address: str, timeout: float = 15.0) -> bool:
    """Poll until adb reports the address as an authorised device."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for d in adb.list_devices():
            if d.serial == address and d.ready:
                return True
        time.sleep(1.0)
    return False


def device_ip(serial: str = "") -> str:
    """The phone's own Wi-Fi address, asked of the phone itself."""
    target = ["-s", serial] if serial else []
    for probe in ("ip -f inet addr show wlan0", "ip route"):
        try:
            _, out = _run(target + ["shell", probe], timeout=15)
        except subprocess.TimeoutExpired:
            continue
        m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", out) or re.search(
            r"src (\d+\.\d+\.\d+\.\d+)", out
        )
        if m:
            return m.group(1)
    return ""


def enable_via_usb(port: int = 5555) -> tuple[bool, str]:
    """Switch a cabled phone over to Wi-Fi, using the cable itself to set it up.

    Avoids the pairing code entirely, so it needs no interaction with the phone.
    The trade-off is that it does not survive a reboot - after one, either run
    this again with the cable in, or pair properly with pair_wireless.
    """
    usb = [
        d for d in adb.list_devices() if d.ready and not is_wireless(d.serial)
    ]
    if not usb:
        return False, (
            "No cabled phone found. Plug the phone in over USB for this one step, "
            "or use pair_wireless with the code from its Wireless debugging screen."
        )
    serial = usb[0].serial
    ip = device_ip(serial)
    if not ip:
        return False, "Could not read the phone's Wi-Fi address; is it on Wi-Fi?"

    rc, out = _run(["-s", serial, "tcpip", str(port)], timeout=30)
    if rc != 0:
        return False, f"Could not switch the phone to TCP mode: {out}"
    time.sleep(2.0)  # adbd restarts on the new port

    address = f"{ip}:{port}"
    # Restarting adbd drops the existing authorisation, so the phone re-shows
    # its "Allow USB debugging?" prompt. Wait for someone to accept it rather
    # than reporting a failure that fixes itself a second later.
    ok, msg = connect(address)
    if not ok or not wait_authorized(address):
        if not wait_authorized(address, timeout=20.0):
            return False, (
                f"Switched to TCP mode but {address} is not authorised yet "
                f"({msg}). Unlock the phone and accept the 'Allow USB debugging?' "
                "prompt, then run connect_wireless."
            )
    remember(serial, address)
    return True, (
        f"Wireless enabled: {address}. You can unplug the cable now.\n"
        "Note: this resets if the phone reboots - run it again with the cable in, "
        "or pair_wireless for a pairing that survives reboots."
    )
