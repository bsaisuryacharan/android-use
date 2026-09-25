"""Registry of phones this server can drive.

One person helping one relative needs one phone. Someone helping a parent and
a grandparent needs several, and they must never be confused with each other -
sending "turn off the alarm" to the wrong phone is the kind of mistake that
destroys trust in the whole thing. So phones are named, and a name is always
resolved explicitly.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path(
    os.environ.get("ANDROID_USE_CONFIG", Path.home() / ".android-use" / "config.json")
)
DEFAULT_PORT = 8765


@dataclass
class Phone:
    name: str
    host: str
    port: int = DEFAULT_PORT
    token: str = ""
    label: str = ""
    proxy: str = ""

    @property
    def display(self) -> str:
        return self.label or self.name


def _config() -> dict:
    if CONFIG_PATH.is_file():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def _save(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


def load_phones() -> dict[str, Phone]:
    """All configured phones, keyed by name.

    A single-phone config written before multi-device support still works: its
    bridge_* keys are read as one phone.
    """
    cfg = _config()
    phones: dict[str, Phone] = {}

    for name, entry in (cfg.get("devices") or {}).items():
        if not isinstance(entry, dict) or not entry.get("host"):
            continue
        phones[name] = Phone(
            name=name,
            host=entry["host"],
            port=int(entry.get("port") or DEFAULT_PORT),
            token=entry.get("token", ""),
            label=entry.get("label", ""),
            proxy=entry.get("proxy", ""),
        )

    legacy_host, legacy_token = cfg.get("bridge_host"), cfg.get("bridge_token")
    if legacy_host and legacy_token:
        name = cfg.get("default_device") or "phone"
        phones.setdefault(
            name,
            Phone(
                name=name,
                host=legacy_host,
                port=int(cfg.get("bridge_port") or DEFAULT_PORT),
                token=legacy_token,
                label=cfg.get("bridge_label", ""),
                proxy=cfg.get("bridge_proxy", ""),
            ),
        )
    return phones


def default_name() -> str:
    """The phone explicitly set as the default, or "" if none is.

    Deliberately no fallback to "the first one": with several phones and no
    default, the first in the file is an accident of ordering, and acting on
    it is exactly the wrong-phone mistake this module exists to prevent.
    """
    cfg = _config()
    named = cfg.get("default_device")
    phones = load_phones()
    if named and named in phones:
        return named
    return ""


def set_default(name: str) -> None:
    cfg = _config()
    cfg["default_device"] = name
    _save(cfg)


def locked_name() -> str:
    """The phone everything is pinned to, if any."""
    return _config().get("locked_device", "")


def lock(name: str) -> None:
    cfg = _config()
    cfg["locked_device"] = name
    _save(cfg)


def unlock() -> None:
    cfg = _config()
    cfg.pop("locked_device", None)
    _save(cfg)


def add_phone(name: str, host: str, token: str, port: int = DEFAULT_PORT,
              label: str = "", proxy: str = "") -> None:
    cfg = _config()
    devices = cfg.setdefault("devices", {})
    devices[name] = {
        "host": host, "port": port, "token": token,
        "label": label, "proxy": proxy,
    }
    cfg.setdefault("default_device", name)
    _save(cfg)


def remove_phone(name: str) -> bool:
    cfg = _config()
    devices = cfg.get("devices") or {}
    if name not in devices:
        return False
    devices.pop(name)
    if cfg.get("default_device") == name:
        # Do not promote whichever phone happens to be next in the file: the
        # user never chose it. With one phone left, it is used anyway; with
        # several, they are asked to name one.
        cfg.pop("default_device", None)
    if cfg.get("locked_device") == name:
        cfg.pop("locked_device", None)
    _save(cfg)
    return True


def resolve(name: str = "") -> tuple[Phone | None, str]:
    """Turn a name (or nothing) into a phone, or explain why not.

    Ambiguity is refused rather than guessed: with several phones configured
    and no name given, acting on the 'first' one is exactly the failure mode
    worth preventing.
    """
    phones = load_phones()
    if not phones:
        return None, (
            "No phones are configured. Run the setup on a phone, then add it "
            "with add_phone, or call discover_phones to find one on your tailnet."
        )

    # A lock is a hard stop, not a preference. If something asks for a
    # different phone while locked, refuse loudly rather than quietly
    # redirecting - a silent redirect hides the very mistake the lock exists
    # to catch.
    pinned = locked_name()
    if pinned and pinned in phones:
        if not name:
            return phones[pinned], ""
        target = phones[pinned]
        wanted = name.strip().lower()
        if (
            wanted == pinned.lower()
            or wanted == target.display.lower()
            or wanted in pinned.lower()
            or wanted in target.display.lower()
        ):
            return target, ""
        return None, (
            f"This server is locked to '{pinned}' ({target.display}), so it will "
            f"not act on '{name}'. Call unlock_phone first if you meant to "
            "switch."
        )

    if name:
        wanted = name.strip().lower()

        # Exact first: a name that matches outright is never ambiguous.
        for key, phone in phones.items():
            if key.lower() == wanted or phone.display.lower() == wanted:
                return phone, ""

        # Then partial, so "grandma" finds a phone labelled "Grandma's phone"
        # and people can talk the way they normally would.
        partial = [
            phone for key, phone in phones.items()
            if wanted in key.lower() or wanted in phone.display.lower()
        ]
        if len(partial) == 1:
            return partial[0], ""
        if len(partial) > 1:
            names = ", ".join(sorted(p.name for p in partial))
            return None, (
                f"'{name}' matches more than one phone ({names}). "
                "Say which one you mean."
            )

        # Generic words people use for "the phone" should land on the obvious
        # one rather than reading as an unknown device.
        GENERIC = {"phone", "my phone", "the phone", "mobile", "my mobile",
                   "device", "my device", "it", "this phone"}
        if wanted in GENERIC:
            if len(phones) == 1:
                return next(iter(phones.values())), ""
            chosen = default_name()
            if chosen and chosen in phones:
                return phones[chosen], ""

        available = ", ".join(
            f"{k} ({v.display})" if v.label else k for k, v in sorted(phones.items())
        )
        return None, f"No phone called '{name}'. Configured phones: {available}"

    if len(phones) == 1:
        return next(iter(phones.values())), ""

    chosen = default_name()
    if chosen and chosen in phones:
        return phones[chosen], ""
    return None, (
        "Several phones are configured and none is set as the default. Name the "
        f"one you mean: {', '.join(sorted(phones))}"
    )


# -- discovery ---------------------------------------------------------------

def _tailscale_bin() -> str:
    for candidate in ("/usr/local/bin/tailscale", "/opt/homebrew/bin/tailscale"):
        if os.path.isfile(candidate):
            return candidate
    return "tailscale"


def tailnet_android_peers() -> list[dict]:
    """Android devices on the tailnet, which is where phones running the app live."""
    try:
        out = subprocess.run(
            [_tailscale_bin(), "status", "--json"],
            capture_output=True, text=True, timeout=25,
        ).stdout
        data = json.loads(out)
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError):
        return []

    peers = []
    for peer in (data.get("Peer") or {}).values():
        if (peer.get("OS") or "").lower() != "android":
            continue
        ipv4 = next((ip for ip in peer.get("TailscaleIPs") or [] if "." in ip), "")
        if not ipv4:
            continue
        peers.append(
            {
                "hostname": peer.get("HostName", ""),
                "ip": ipv4,
                "online": bool(peer.get("Online")),
            }
        )
    return peers


def probe(host: str, port: int = DEFAULT_PORT, timeout: int = 6) -> bool:
    """Is the client app answering here? /ping needs no token by design."""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(
            f"http://{host}:{port}/ping", timeout=timeout
        ) as resp:
            return b"android-use" in resp.read()
    except (urllib.error.URLError, OSError):
        return False


def discover() -> list[dict]:
    """Android peers on the tailnet, flagged with whether the app answers."""
    known = {p.host for p in load_phones().values()}
    found = []
    for peer in tailnet_android_peers():
        entry = dict(peer)
        entry["running_app"] = peer["online"] and probe(peer["ip"])
        entry["already_configured"] = peer["ip"] in known
        found.append(entry)
    return found
