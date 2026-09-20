"""Input actions and device state queries."""
from __future__ import annotations

import re
import time

from . import adb

KEYS = {
    "back": "KEYCODE_BACK",
    "home": "KEYCODE_HOME",
    "recents": "KEYCODE_APP_SWITCH",
    "enter": "KEYCODE_ENTER",
    "delete": "KEYCODE_DEL",
    "tab": "KEYCODE_TAB",
    "search": "KEYCODE_SEARCH",
    "wake": "KEYCODE_WAKEUP",
    "sleep": "KEYCODE_SLEEP",
    "volume_up": "KEYCODE_VOLUME_UP",
    "volume_down": "KEYCODE_VOLUME_DOWN",
    "mute": "KEYCODE_VOLUME_MUTE",
    "play_pause": "KEYCODE_MEDIA_PLAY_PAUSE",
    "next": "KEYCODE_MEDIA_NEXT",
    "previous": "KEYCODE_MEDIA_PREVIOUS",
    "camera": "KEYCODE_CAMERA",
    "call": "KEYCODE_CALL",
    "end_call": "KEYCODE_ENDCALL",
}

# Deep links into Settings. Jumping straight to the right page is far more
# reliable than driving the Settings app's own (OEM-specific) navigation.
SETTINGS_PAGES = {
    "wifi": "android.settings.WIFI_SETTINGS",
    "mobile_data": "android.settings.DATA_USAGE_SETTINGS",
    "network": "android.settings.WIRELESS_SETTINGS",
    "airplane_mode": "android.settings.AIRPLANE_MODE_SETTINGS",
    "hotspot": "android.settings.TETHER_SETTINGS",
    "bluetooth": "android.settings.BLUETOOTH_SETTINGS",
    "display": "android.settings.DISPLAY_SETTINGS",
    "sound": "android.settings.SOUND_SETTINGS",
    "battery": "android.intent.action.POWER_USAGE_SUMMARY",
    "storage": "android.settings.INTERNAL_STORAGE_SETTINGS",
    "apps": "android.settings.APPLICATION_SETTINGS",
    "location": "android.settings.LOCATION_SOURCE_SETTINGS",
    "security": "android.settings.SECURITY_SETTINGS",
    "accessibility": "android.settings.ACCESSIBILITY_SETTINGS",
    "date_time": "android.settings.DATE_SETTINGS",
    "language": "android.settings.LOCALE_SETTINGS",
    "privacy": "android.settings.PRIVACY_SETTINGS",
    "notifications": "android.settings.NOTIFICATION_SETTINGS",
    "about_phone": "android.settings.DEVICE_INFO_SETTINGS",
    "settings_home": "android.settings.SETTINGS",
}


def tap(x: int, y: int) -> None:
    adb.shell(f"input tap {int(x)} {int(y)}")


def swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
    adb.shell(f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(duration_ms)}")


def scroll(direction: str, width: int, height: int, amount: float = 0.6) -> None:
    """Scroll the screen. 'down' reveals content further down the page."""
    cx = width // 2
    span = int(height * amount / 2)
    mid = height // 2
    if direction == "down":
        swipe(cx, mid + span, cx, mid - span, 400)
    elif direction == "up":
        swipe(cx, mid - span, cx, mid + span, 400)
    elif direction == "left":
        swipe(int(width * 0.8), mid, int(width * 0.2), mid, 400)
    elif direction == "right":
        swipe(int(width * 0.2), mid, int(width * 0.8), mid, 400)
    else:
        raise ValueError(f"direction must be up/down/left/right, got {direction!r}")


def type_text(text: str) -> None:
    """Type into the focused field. ASCII only - `input text` cannot send
    emoji or non-Latin scripts."""
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("`", "\\`")
        .replace(" ", "%s")
    )
    adb.shell(f'input text "{escaped}"')


def press(key: str) -> None:
    keycode = KEYS.get(key.lower().strip())
    if keycode is None:
        raise ValueError(f"Unknown key {key!r}. Known keys: {', '.join(sorted(KEYS))}")
    adb.shell(f"input keyevent {keycode}")


def wake_and_unlock() -> str:
    """Wake the screen and dismiss a swipe-only lock screen.
    A PIN/pattern/fingerprint lock cannot be bypassed - the user must unlock."""
    adb.shell("input keyevent KEYCODE_WAKEUP")
    time.sleep(0.6)
    out = adb.shell("dumpsys window | grep -E 'mDreamingLockscreen' | head -1")
    if "mDreamingLockscreen=true" in out:
        adb.shell("input keyevent KEYCODE_MENU")  # dismisses a swipe-only lock
        time.sleep(0.6)
        out = adb.shell("dumpsys window | grep -E 'mDreamingLockscreen' | head -1")
        if "mDreamingLockscreen=true" in out:
            return "locked: the phone needs a PIN, pattern or fingerprint to unlock"
    return "awake and unlocked"


def open_settings_page(page: str) -> str:
    action = SETTINGS_PAGES.get(page.lower().strip())
    if action is None:
        raise ValueError(
            f"Unknown settings page {page!r}. Available: {', '.join(sorted(SETTINGS_PAGES))}"
        )
    adb.shell(f"am start -a {action}")
    time.sleep(1.2)
    return action


def launch_package(package: str) -> None:
    """Start an app by package name.

    `monkey` is the usual one-liner for this but it is unreliable - it fails
    outright on emulators with no physical keys. Resolving the launcher
    activity and starting it directly works everywhere.
    """
    component = ""
    try:
        out = adb.shell(
            f"cmd package resolve-activity --brief "
            f"-c android.intent.category.LAUNCHER {package}"
        )
        for line in out.splitlines():
            line = line.strip()
            if "/" in line and line.startswith(package):
                component = line
                break
    except adb.AdbError:
        pass

    if component:
        adb.shell(f"am start -n {component}")
    else:
        # Fall back to monkey where the activity could not be resolved.
        adb.shell(
            f"monkey -p {package} -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1"
        )
    time.sleep(1.5)


def _get_setting(scope: str, key: str) -> str:
    try:
        return adb.shell(f"settings get {scope} {key}").strip()
    except adb.AdbError:
        return "unknown"


def connectivity_report() -> str:
    """Everything needed to answer 'why is my internet not working?'"""
    lines: list[str] = []
    airplane = _get_setting("global", "airplane_mode_on")
    wifi_on = _get_setting("global", "wifi_on")
    mobile = _get_setting("global", "mobile_data")
    lines.append(f"Airplane mode: {'ON' if airplane == '1' else 'off'}")
    lines.append(f"Wi-Fi radio:   {'on' if wifi_on == '1' else 'OFF'}")
    lines.append(f"Mobile data:   {'on' if mobile == '1' else 'OFF'}")

    try:
        status = adb.shell("cmd wifi status", timeout=15).strip()
        first = status.splitlines()[0] if status else ""
        lines.append(f"Wi-Fi status:  {first[:160]}")
    except adb.AdbError:
        pass

    # Does traffic actually flow? Separates 'no signal' from 'DNS is broken'.
    try:
        ping = adb.shell("ping -c 2 -W 2 8.8.8.8 2>&1 | tail -3", timeout=20)
        ok = "0% packet loss" in ping or " 0.0% packet loss" in ping
        lines.append(f"Ping 8.8.8.8:  {'reachable' if ok else 'FAILED'}")
    except adb.AdbError:
        lines.append("Ping 8.8.8.8:  could not test")
    try:
        dns = adb.shell("ping -c 1 -W 2 google.com 2>&1 | tail -3", timeout=20)
        dns_ok = "0% packet loss" in dns or " 0.0% packet loss" in dns
        lines.append(f"DNS (google.com): {'resolves' if dns_ok else 'FAILED'}")
    except adb.AdbError:
        lines.append("DNS (google.com): could not test")
    return "\n".join(lines)


def device_info() -> str:
    props = {
        "Manufacturer": "ro.product.manufacturer",
        "Model": "ro.product.model",
        "Android version": "ro.build.version.release",
        "SDK": "ro.build.version.sdk",
    }
    lines = []
    for label, prop in props.items():
        try:
            lines.append(f"{label}: {adb.shell(f'getprop {prop}').strip()}")
        except adb.AdbError:
            pass
    try:
        batt = adb.shell("dumpsys battery | grep 'level:' | tail -1")
        level = re.search(r"level:\s*(\d+)", batt)
        if level:
            lines.append(f"Battery: {level.group(1)}%")
    except adb.AdbError:
        pass
    return "\n".join(lines)
