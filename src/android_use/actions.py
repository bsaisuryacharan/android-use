"""Input actions and device state queries, over ADB."""
from __future__ import annotations

import re
import shlex
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
    "lock": "KEYCODE_SLEEP",
    "volume_up": "KEYCODE_VOLUME_UP",
    "volume_down": "KEYCODE_VOLUME_DOWN",
    "mute": "KEYCODE_VOLUME_MUTE",
    "play_pause": "KEYCODE_MEDIA_PLAY_PAUSE",
    "next": "KEYCODE_MEDIA_NEXT",
    "previous": "KEYCODE_MEDIA_PREVIOUS",
    "camera": "KEYCODE_CAMERA",
    "call": "KEYCODE_CALL",
    "end_call": "KEYCODE_ENDCALL",
    "all_apps": "KEYCODE_ALL_APPS",
    "screenshot": "KEYCODE_SYSRQ",
    "up": "KEYCODE_DPAD_UP",
    "down": "KEYCODE_DPAD_DOWN",
    "left": "KEYCODE_DPAD_LEFT",
    "right": "KEYCODE_DPAD_RIGHT",
}

# Things that are not key presses but read like them, so the same press_key
# names work on both transports.
KEY_COMMANDS = {
    "notifications": "cmd statusbar expand-notifications",
    "quick_settings": "cmd statusbar expand-settings",
    "close_panels": "cmd statusbar collapse",
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
    "do_not_disturb": "android.settings.ZEN_MODE_SETTINGS",
    "nfc": "android.settings.NFC_SETTINGS",
    "vpn": "android.settings.VPN_SETTINGS",
    "developer": "android.settings.APPLICATION_DEVELOPMENT_SETTINGS",
    "default_apps": "android.settings.MANAGE_DEFAULT_APPS_SETTINGS",
    "users": "android.settings.USER_SETTINGS",
    "sync": "android.settings.SYNC_SETTINGS",
    "input_method": "android.settings.INPUT_METHOD_SETTINGS",
}


def tap(x: int, y: int) -> None:
    adb.shell(f"input tap {int(x)} {int(y)}")


def double_tap(x: int, y: int) -> None:
    # One shell invocation, so the two taps land close enough together to
    # count as a double tap rather than two separate ones.
    adb.shell(f"input tap {int(x)} {int(y)}; input tap {int(x)} {int(y)}")


def swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
    adb.shell(f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(duration_ms)}")


def drag(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 1500) -> None:
    """Drag and drop. `input draganddrop` holds before moving, which is what
    launchers and reorderable lists wait for; old builds lack it, and a slow
    swipe is the closest substitute."""
    try:
        adb.shell(
            f"input draganddrop {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(duration_ms)}"
        )
    except adb.AdbError:
        swipe(x1, y1, x2, y2, max(duration_ms, 2000))


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


def delete_chars(count: int) -> None:
    """Delete `count` characters before the cursor, after moving it to the end."""
    count = max(0, min(int(count), 400))
    if not count:
        return
    adb.shell("input keyevent KEYCODE_MOVE_END")
    # Many keycodes per invocation: one `input` process per character is
    # painfully slow over Wi-Fi.
    for start in range(0, count, 50):
        n = min(50, count - start)
        adb.shell("input keyevent " + " ".join(["KEYCODE_DEL"] * n))


def press(key: str) -> None:
    name = key.lower().strip()
    if name in KEY_COMMANDS:
        adb.shell(KEY_COMMANDS[name])
        return
    keycode = KEYS.get(name)
    if keycode is None:
        known = sorted(set(KEYS) | set(KEY_COMMANDS))
        raise ValueError(f"Unknown key {key!r}. Known keys: {', '.join(known)}")
    adb.shell(f"input keyevent {keycode}")


# -- phone state ---------------------------------------------------------------

_STATE_PROBE = (
    "dumpsys power | grep 'mWakefulness=' | head -1; "
    "dumpsys window | grep -E 'mCurrentFocus|mDreamingLockscreen|mShowingLockscreen|"
    "mKeyguardShowing|isKeyguardShowing' | head -8; "
    "dumpsys input_method | grep -E 'mInputShown=' | head -1"
)


def probe_state() -> dict:
    """Screen on/off, lock and keyboard state, and the focused app - in one
    round trip, because every extra adb call is felt over Wi-Fi."""
    state: dict = {"screen_on": None, "locked": None, "keyboard_open": False,
                   "package": "", "activity": ""}
    try:
        out = adb.shell(_STATE_PROBE, timeout=20)
    except adb.AdbError:
        return state
    m = re.search(r"mWakefulness=(\w+)", out)
    if m:
        state["screen_on"] = m.group(1).lower() == "awake"
    lock_lines = [
        line for line in out.splitlines()
        if re.search(r"Lockscreen|KeyguardShowing", line)
    ]
    if lock_lines:
        state["locked"] = any(
            re.search(r"(mDreamingLockscreen|mShowingLockscreen|mKeyguardShowing|"
                      r"isKeyguardShowing)=true", line)
            for line in lock_lines
        )
    state["keyboard_open"] = "mInputShown=true" in out
    focus = re.search(r"mCurrentFocus=.*?\s([A-Za-z0-9_.]+)/([A-Za-z0-9_.$]+)", out)
    if focus:
        state["package"], state["activity"] = focus.group(1), focus.group(2)
    return state


def wake_and_unlock() -> str:
    """Wake the screen and dismiss a swipe-only lock screen.
    A PIN/pattern/fingerprint lock cannot be bypassed - the user must unlock."""
    adb.shell("input keyevent KEYCODE_WAKEUP")
    time.sleep(0.6)
    state = probe_state()
    if state["locked"]:
        # `wm dismiss-keyguard` removes a swipe lock and, on a secure lock,
        # brings up the PIN pad for the owner; KEYCODE_MENU is the older trick.
        for cmd in ("wm dismiss-keyguard", "input keyevent KEYCODE_MENU"):
            try:
                adb.shell(cmd)
            except adb.AdbError:
                continue
            time.sleep(0.8)
            state = probe_state()
            if not state["locked"]:
                break
        if state["locked"]:
            return "locked: the phone needs a PIN, pattern or fingerprint to unlock"
    return "awake and unlocked"


def open_settings_page(page: str) -> str:
    action = SETTINGS_PAGES.get(page.lower().strip())
    if action is None:
        raise ValueError(
            f"Unknown settings page {page!r}. Available: {', '.join(sorted(SETTINGS_PAGES))}"
        )
    adb.shell(f"am start -a {action}")
    time.sleep(0.4)
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


def _intent_action(kind: str) -> str:
    # ACTION_DIAL for phone numbers: it fills the number into the dialer and
    # stops. ACTION_CALL would place the call, and is never used.
    return "android.intent.action.DIAL" if kind == "dial" else "android.intent.action.VIEW"


def _intent_args(url: str, kind: str) -> str:
    # BROWSABLE limits a link to what a web page could open anyway. The
    # dialler's DIAL filter does not declare it, so it is left off there.
    category = "" if kind == "dial" else " -c android.intent.category.BROWSABLE"
    return f"-a {_intent_action(kind)}{category} -d {shlex.quote(url)}"


def resolve_url(url: str, kind: str = "web") -> str:
    """Which app would open this link? "" if none, "android" for a chooser."""
    try:
        out = adb.shell(f"cmd package resolve-activity --brief {_intent_args(url, kind)}")
    except adb.AdbError:
        return ""
    components = [line.strip() for line in out.splitlines() if "/" in line]
    if not components:
        return ""
    package, activity = components[-1].split("/", 1)
    if "ResolverActivity" in activity or "ChooserActivity" in activity:
        return "android"
    return package


def open_url(url: str, kind: str = "web", package: str = "") -> None:
    target = f" -p {shlex.quote(package)}" if package else ""
    adb.shell(f"am start {_intent_args(url, kind)}{target}")


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
    lines.append(f"Wi-Fi radio:   {'on' if wifi_on in ('1', '2') else 'OFF'}")
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


# -- device state and direct settings --------------------------------------------

# Android audio stream ids.
STREAMS = {"media": 3, "ring": 2, "alarm": 4, "notification": 5}

SETTABLE = [
    "volume_media", "volume_ring", "volume_alarm", "volume_notification",
    "brightness", "font_size", "screen_timeout", "auto_rotate", "ringer",
    "do_not_disturb", "wifi", "mobile_data", "bluetooth", "airplane_mode", "location",
]


def _int(value: str, default: int | None = None) -> int | None:
    try:
        return int(float(value.strip()))
    except (ValueError, AttributeError):
        return default


def _volume(stream: int) -> tuple[int, int] | None:
    """(current, max) for a stream, or None when this build cannot say."""
    for cmd in (f"cmd media_session volume --stream {stream} --get",
                f"media volume --stream {stream} --get"):
        try:
            out = adb.shell(cmd, timeout=15)
        except adb.AdbError:
            continue
        m = re.search(r"volume is (\d+) in range \[(\d+)\.\.(\d+)\]", out)
        if m:
            return int(m.group(1)), int(m.group(3))
    return None


def _set_volume(stream: int, percent: int) -> str:
    current = _volume(stream)
    if current is None:
        raise adb.AdbError("this phone does not report volume over adb")
    index = round(max(0, min(100, percent)) / 100 * current[1])
    for cmd in (f"cmd media_session volume --stream {stream} --set {index}",
                f"media volume --stream {stream} --set {index}"):
        try:
            adb.shell(cmd, timeout=15)
            return f"{index}/{current[1]}"
        except adb.AdbError:
            continue
    raise adb.AdbError("could not set the volume over adb")


def device_state() -> dict:
    """Everything worth knowing for "why is my phone silent / slow / offline"."""
    state: dict = {}

    batt = ""
    try:
        batt = adb.shell("dumpsys battery", timeout=15)
    except adb.AdbError:
        pass
    level = re.search(r"level:\s*(\d+)", batt)
    powered = re.findall(r"(AC|USB|Wireless) powered: true", batt)
    status = re.search(r"status:\s*(\d+)", batt)
    if level:
        state["battery"] = {
            "level": int(level.group(1)),
            # status 2 = charging, 5 = full
            "charging": bool(powered) or (status is not None and status.group(1) in ("2", "5")),
        }

    probe = probe_state()
    state["screen"] = {"on": probe["screen_on"], "locked": probe["locked"]}

    volumes = {}
    for name, stream in STREAMS.items():
        v = _volume(stream)
        if v and v[1]:
            volumes[name] = round(v[0] * 100 / v[1])
    ringer = ""
    try:
        audio = adb.shell("dumpsys audio | grep -i 'ringer mode'", timeout=15)
        m = re.search(r"ringer mode \(external\)\s*=\s*(\w+)", audio, re.I) or re.search(
            r"ringer mode.*?=\s*(\w+)", audio, re.I
        )
        if m:
            ringer = m.group(1).lower()
    except adb.AdbError:
        pass
    zen = {"0": "off", "1": "priority only", "2": "total silence", "3": "alarms only"}.get(
        _get_setting("global", "zen_mode"), "unknown"
    )
    state["sound"] = {"ringer": ringer or "unknown", "dnd": zen, "volume": volumes}

    brightness = _int(_get_setting("system", "screen_brightness"))
    timeout_ms = _int(_get_setting("system", "screen_off_timeout"))
    font = _get_setting("system", "font_scale")
    state["display"] = {
        "brightness": round(brightness * 100 / 255) if brightness is not None else None,
        "auto_brightness": _get_setting("system", "screen_brightness_mode") == "1",
        "font_scale": float(font) if re.match(r"^\d+(\.\d+)?$", font) else 1.0,
        "screen_timeout_s": timeout_ms // 1000 if timeout_ms else None,
        "auto_rotate": _get_setting("system", "accelerometer_rotation") == "1",
    }

    active = ""
    try:
        route = adb.shell("ip route get 8.8.8.8 2>&1", timeout=10)
        dev = re.search(r"\bdev\s+(\S+)", route)
        if dev:
            name = dev.group(1)
            active = ("wifi" if name.startswith("wlan") else
                      "vpn" if name.startswith("tun") else "cellular")
        elif "unreachable" in route.lower():
            active = "none"
    except adb.AdbError:
        pass
    state["network"] = {
        "active": active,
        "wifi": _get_setting("global", "wifi_on") in ("1", "2"),
        "mobile_data": _get_setting("global", "mobile_data") == "1",
        "airplane": _get_setting("global", "airplane_mode_on") == "1",
        "bluetooth": _get_setting("global", "bluetooth_on") == "1",
        "location": _get_setting("secure", "location_mode") not in ("0", "unknown", "null"),
    }

    try:
        df = adb.shell("df -k /data | tail -1", timeout=15).split()
        # Filesystem 1K-blocks Used Available Use% Mounted-on
        if len(df) >= 4:
            state["storage"] = {
                "total_gb": round(int(df[1]) / 1024 / 1024, 1),
                "free_gb": round(int(df[3]) / 1024 / 1024, 1),
            }
    except (adb.AdbError, ValueError):
        pass
    return state


def _first_working(commands: list[str]) -> None:
    last = ""
    for cmd in commands:
        try:
            out = adb.shell(cmd, timeout=20)
        except adb.AdbError as exc:
            last = str(exc)
            continue
        low = out.lower()
        # Several of these print usage instead of failing on older builds.
        if "unknown command" in low or "usage:" in low or "exception" in low:
            last = out.strip()[:120]
            continue
        return
    raise adb.AdbError(last or "not supported on this phone")


def change_setting(name: str, value) -> str:
    """Apply one setting directly. `value` is already normalised by the caller:
    ints for levels and seconds, a float for font scale, bools for switches,
    "auto" for brightness, and normal/vibrate/silent for the ringer."""
    onoff = "enable" if value else "disable"
    if name.startswith("volume_"):
        level = _set_volume(STREAMS[name.split("_", 1)[1]], int(value))
        return f"{name.replace('_', ' ')} set to {value}% ({level})"
    if name == "brightness":
        if value == "auto":
            adb.shell("settings put system screen_brightness_mode 1")
            return "Brightness set to automatic"
        adb.shell("settings put system screen_brightness_mode 0; "
                  f"settings put system screen_brightness {round(int(value) * 255 / 100)}")
        return f"Brightness set to {value}%"
    if name == "font_size":
        adb.shell(f"settings put system font_scale {float(value)}")
        return f"Font size set to {float(value):g}x"
    if name == "screen_timeout":
        adb.shell(f"settings put system screen_off_timeout {int(value) * 1000}")
        return f"Screen turns off after {int(value)} seconds of inactivity"
    if name == "auto_rotate":
        adb.shell(f"settings put system accelerometer_rotation {1 if value else 0}")
        return f"Auto-rotate {'on' if value else 'off'}"
    if name == "do_not_disturb":
        _first_working([f"cmd notification set_dnd {'priority' if value else 'off'}"])
        return f"Do Not Disturb {'on' if value else 'off'}"
    if name == "ringer":
        _first_working([f"cmd audio set-ringer-mode {str(value).upper()}"])
        return f"Ringer set to {value}"
    if name == "wifi":
        _first_working([f"svc wifi {onoff}",
                        f"cmd wifi set-wifi-enabled {'enabled' if value else 'disabled'}"])
        return f"Wi-Fi turned {'on' if value else 'off'}"
    if name == "mobile_data":
        _first_working([f"svc data {onoff}"])
        return f"Mobile data turned {'on' if value else 'off'}"
    if name == "bluetooth":
        _first_working([f"cmd bluetooth_manager {onoff}", f"svc bluetooth {onoff}"])
        return f"Bluetooth turned {'on' if value else 'off'}"
    if name == "airplane_mode":
        _first_working([f"cmd connectivity airplane-mode {onoff}"])
        return f"Airplane mode turned {'on' if value else 'off'}"
    if name == "location":
        _first_working([f"cmd location set-location-enabled {'true' if value else 'false'}"])
        return f"Location turned {'on' if value else 'off'}"
    raise ValueError(f"'{name}' cannot be changed over adb.")
