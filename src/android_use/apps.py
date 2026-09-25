"""App discovery, friendly-name resolution, and the safety allowlist."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import adb

CONFIG_PATH = Path(
    os.environ.get("ANDROID_USE_CONFIG", Path.home() / ".android-use" / "config.json")
)

# Apps that are safe to drive on a first run. Anything not listed here is
# refused: the phone almost certainly also has banking, payment and messaging
# apps on it, and those should never be automated by accident.
DEFAULT_ALLOWLIST = [
    "com.android.settings",
    "com.android.chrome",
    "com.google.android.youtube",
    "com.android.deskclock",
    "com.google.android.deskclock",
    "com.google.android.calculator",
    "com.google.android.calendar",
    "com.google.android.contacts",
    "com.google.android.apps.maps",
    "com.google.android.apps.photos",
    "com.google.android.keep",
    "com.google.android.apps.nbu.files",
    "com.android.camera",
    # Opening the Claude app is how a task done on the same phone ends: it
    # brings the person back to the conversation to read the answer.
    "com.anthropic.claude",
]

# Web browsers. A web link that lands in one of these is just a web page, so
# open_url allows it even when the browser itself is not on the allowlist.
BROWSERS = {
    "com.android.chrome", "org.chromium.chrome", "com.chrome.beta",
    "org.mozilla.firefox", "org.mozilla.focus", "com.sec.android.app.sbrowser",
    "com.microsoft.emmx", "com.brave.browser", "com.opera.browser",
    "com.opera.mini.native", "com.vivaldi.browser", "com.duckduckgo.mobile.android",
    "com.kiwibrowser.browser", "com.UCMobile.intl", "com.mi.globalbrowser",
    "com.android.browser", "com.heytap.browser", "com.vivo.browser",
    "com.huawei.browser",
}

# Package-name tokens that carry no meaning for a human.
_STOPWORDS = {
    "com", "org", "net", "android", "apps", "app", "google", "mobile", "client",
    "the", "co", "in", "io", "inc", "ltd", "main", "user", "customer", "vnd",
}

_KNOWN_LABELS = {
    "com.android.chrome": "Chrome",
    "com.android.settings": "Settings",
    "com.android.deskclock": "Clock",
    "com.google.android.deskclock": "Clock",
    "com.android.camera": "Camera",
    "com.android.vending": "Play Store",
    "com.google.android.youtube": "YouTube",
    "com.google.android.gm": "Gmail",
    "com.google.android.apps.maps": "Maps",
    "com.google.android.apps.photos": "Photos",
    "com.google.android.contacts": "Contacts",
    "com.google.android.dialer": "Phone",
    "com.google.android.apps.messaging": "Messages",
    "com.google.android.calendar": "Calendar",
    "com.google.android.keep": "Keep Notes",
    "com.google.android.apps.nbu.files": "Files",
    "com.google.android.googlequicksearchbox": "Google",
    "com.google.android.apps.bard": "Gemini",
    "com.whatsapp": "WhatsApp",
    "com.instagram.android": "Instagram",
    "com.netflix.mediaclient": "Netflix",
    "com.spotify.music": "Spotify",
    "com.linkedin.android": "LinkedIn",
    "com.anthropic.claude": "Claude",
}


def load_config() -> dict:
    if CONFIG_PATH.is_file():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def allowlist() -> list[str]:
    cfg = load_config()
    listed = cfg.get("allowed_packages")
    return list(listed) if isinstance(listed, list) else list(DEFAULT_ALLOWLIST)


def allow_all() -> bool:
    """Opt-out escape hatch for users who accept the risk."""
    return bool(load_config().get("allow_all_apps", False))


def is_allowed(package: str) -> bool:
    return allow_all() or package in allowlist()


def _tokens(package: str) -> list[str]:
    parts = re.split(r"[._]", package.lower())
    return [p for p in parts if p and p not in _STOPWORDS]


def friendly_label(package: str) -> str:
    if package in _KNOWN_LABELS:
        return _KNOWN_LABELS[package]
    toks = _tokens(package)
    return toks[-1].title() if toks else package


def installed_packages() -> list[str]:
    """Packages that have a launcher icon (i.e. things a person can open)."""
    out = adb.shell(
        "cmd package query-activities --brief "
        "-a android.intent.action.MAIN -c android.intent.category.LAUNCHER",
        timeout=45,
    )
    found = set()
    for line in out.splitlines():
        line = line.strip()
        m = re.match(r"^([a-zA-Z][a-zA-Z0-9_.]*)/", line)
        if m:
            found.add(m.group(1))
    return sorted(found)


def resolve(query: str, packages: list[str] | None = None) -> list[str]:
    """Map something a person would say ('youtube') to candidate packages."""
    q = query.strip().lower()
    pkgs = packages if packages is not None else installed_packages()
    if q in pkgs:
        return [q]

    exact_label, token_hit, loose = [], [], []
    for pkg in pkgs:
        label = friendly_label(pkg).lower()
        toks = _tokens(pkg)
        if label == q:
            exact_label.append(pkg)
        elif q in toks:
            token_hit.append(pkg)
        elif q in label or q.replace(" ", "") in pkg.lower():
            loose.append(pkg)
    return exact_label or token_hit or loose


def describe_apps(packages: list[str] | None = None) -> str:
    pkgs = packages if packages is not None else installed_packages()
    allowed = set(allowlist())
    everything = allow_all()
    lines = [f"{len(pkgs)} apps with a launcher icon are installed.", ""]
    lines.append("Apps this server is allowed to open:")
    usable = [p for p in pkgs if everything or p in allowed]
    if usable:
        lines += [f"  {friendly_label(p)}  ({p})" for p in sorted(usable, key=friendly_label)]
    else:
        lines.append("  (none)")
    if not everything:
        blocked = len(pkgs) - len(usable)
        lines += [
            "",
            f"{blocked} other apps are installed but blocked for safety "
            "(banking, payment and messaging apps among them).",
            f"To allow more, add their package names to {CONFIG_PATH}:",
            '  {"allowed_packages": ["com.android.chrome", "com.example.app"]}',
        ]
    return "\n".join(lines)
