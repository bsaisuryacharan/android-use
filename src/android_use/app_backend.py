"""Backend that talks to the client app on the phone over HTTP.

This is the transport that works from anywhere: the phone runs an
AccessibilityService behind a token-authenticated bridge, reachable over a
Tailscale address on any network, including cellular. No ADB, no developer
options, and it survives a reboot.

It is also more capable than ADB - nodes are clicked directly, text is set with
ACTION_SET_TEXT (so any script or emoji works), gestures include long press,
drag and pinch, and the phone can talk to the person holding it.

Protocol versions: the app reports `protocol` in /status and /screen. Version
1 (the first release) knows only the original routes; anything newer is only
asked of an app that says it understands it, so an old app on the phone gets
a clear "update the app" instead of a misread request.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

from .backend import Backend, BackendError, UnsupportedError
from .models import DeviceStatus, Element, Screen

# Settings deep links, same names the ADB backend uses so tools behave alike.
SETTINGS_ACTIONS = {
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
    "default_apps": "android.settings.MANAGE_DEFAULT_APPS_SETTINGS",
    "users": "android.settings.USER_SETTINGS",
    "sync": "android.settings.SYNC_SETTINGS",
    "input_method": "android.settings.INPUT_METHOD_SETTINGS",
}

GLOBAL_KEYS = ["back", "home", "recents", "notifications", "quick_settings", "lock"]
# What a protocol-2 app adds: IME enter and delete on the focused field, audio
# and media keys, and a few system actions ADB has no key for.
KEYS_V2 = GLOBAL_KEYS + [
    "enter", "delete", "volume_up", "volume_down", "mute", "play_pause", "next",
    "previous", "power_menu", "screenshot", "split_screen", "all_apps", "wake",
    "close_panels",
]

# Reads are safe to repeat. Anything else might have reached the phone even
# though the reply did not come back, and a repeated "Send" is not a retry.
_IDEMPOTENT = {"/status", "/screen", "/screenshot", "/apps", "/device_state", "/ping"}

# A tap on something that sends, pays or deletes may wait on the phone for its
# owner to say yes (up to a minute). The reply must be allowed to take that long,
# or a tap the owner then approves would be reported to the model as failed.
OWNER_CONFIRM_TIMEOUT = 75

_APP_SETTABLE = [
    "volume_media", "volume_ring", "volume_alarm", "volume_notification", "ringer",
    "do_not_disturb", "flashlight", "brightness", "font_size", "screen_timeout",
    "auto_rotate",
]


class AppBackend(Backend):
    name = "app"

    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        timeout: int = 15,
        proxy: str = "",
    ):
        self.base = f"http://{host}:{port}"
        self.token = token
        self.timeout = timeout
        self.protocol = 1
        self._labels: dict[str, str] = {}
        self._size: tuple[int, int] = (0, 0)
        self._ready_cache: tuple[float, DeviceStatus] | None = None
        # Tailscale in userspace mode has no system route to the 100.x network;
        # it exposes the tailnet through a local proxy instead. A normal
        # Tailscale install needs no proxy and leaves this empty.
        self._opener = (
            urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy})
            )
            if proxy
            else urllib.request.build_opener(urllib.request.ProxyHandler({}))
        )

    # -- transport -------------------------------------------------------
    def repair(self) -> dict:
        """Ask the phone to re-enable its own accessibility service.

        Android revokes it from time to time. The bridge survives that, and
        holds WRITE_SECURE_SETTINGS, so the phone can fix itself without a
        cable - which is the only way this works when the phone is far away.
        """
        return self._request("/repair", {}, _no_repair=True)

    def _note_protocol(self, data: dict) -> None:
        try:
            self.protocol = max(1, int(data.get("protocol", 1)))
        except (TypeError, ValueError):
            self.protocol = 1

    def _needs(self, version: int, what: str) -> None:
        if self.protocol < version:
            raise UnsupportedError(
                f"{what} needs a newer version of the Android Use app on the phone. "
                "Update the app (install the latest APK over the old one), then "
                "try again."
            )

    def _request(
        self, path: str, payload: dict | None = None, raw: bool = False,
        _no_repair: bool = False, _retries_left: int = 1, timeout: float | None = None,
    ):
        url = f"{self.base}{path}"
        route = path.split("?", 1)[0]
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
        req.add_header("Authorization", f"Bearer {self.token}")
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with self._opener.open(req, timeout=timeout or self.timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403, 503):
                self._ready_cache = None  # whatever was fine a moment ago is not
            text = exc.read().decode(errors="replace")
            info: dict = {}
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    info = parsed
            except json.JSONDecodeError:
                pass
            detail = info.get("error", text)
            if exc.code == 401:
                raise BackendError(
                    "The phone rejected the pairing token. Open Android Use on the "
                    "phone and use 'Show the code on screen' to check it."
                ) from exc
            if exc.code == 403:
                if info.get("sensitive") or info.get("declined") or info.get("blocked"):
                    raise BackendError(detail) from exc
                raise BackendError(
                    f"{detail}\nAsk the phone's owner to open Android Use and allow "
                    "help, or call request_control to send them a request."
                ) from exc
            if exc.code == 404 and "unknown route" in str(detail).lower():
                raise UnsupportedError(
                    "The Android Use app on the phone is too old for this "
                    f"({route}). Update the app, then try again."
                ) from exc
            # A revoked accessibility service is recoverable: repair, then retry
            # once. Guard against recursing if /repair itself reports it.
            if (
                exc.code == 503
                and not _no_repair
                and "accessibility" in str(detail).lower()
            ):
                try:
                    self.repair()
                except BackendError:
                    raise BackendError(detail) from exc
                return self._request(path, payload, raw, _no_repair=True, timeout=timeout)
            raise BackendError(detail) from exc
        except urllib.error.URLError as exc:
            # urllib wraps failures that happen while connecting and sending,
            # so the phone never saw this request and repeating it is safe. A
            # sleeping phone drops its Tailscale path, and the first request
            # after idle often fails exactly like this - once.
            if _retries_left > 0:
                time.sleep(2.0)
                return self._request(
                    path, payload, raw, _no_repair, _retries_left - 1, timeout
                )
            self._ready_cache = None
            raise BackendError(self._unreachable(exc)) from exc
        except OSError as exc:
            # A timeout or reset while waiting for the reply: the request may
            # well have been carried out. Only reads are repeated.
            if _retries_left > 0 and route in _IDEMPOTENT:
                time.sleep(1.0)
                return self._request(
                    path, payload, raw, _no_repair, _retries_left - 1, timeout
                )
            if route in _IDEMPOTENT:
                raise BackendError(self._unreachable(exc)) from exc
            raise BackendError(
                f"The phone did not answer in time ({exc}). The action may or may "
                "not have happened - read the screen before trying again."
            ) from exc
        if raw:
            return body
        parsed = json.loads(body or b"{}")
        return parsed if isinstance(parsed, dict) else {}

    def _unreachable(self, exc: Exception) -> str:
        return (
            f"Could not reach the phone at {self.base}: {exc}. Check that the "
            "phone is online and, if you are away from home, that Tailscale is "
            "connected on both devices."
        )

    # -- connection ------------------------------------------------------
    def status(self) -> DeviceStatus:
        # A health probe should answer quickly. Retrying here would make
        # list_phones crawl whenever one phone is off, so probes fail fast and
        # only real actions get the second chance.
        try:
            info = self._request("/status", _retries_left=0)
        except BackendError as exc:
            return DeviceStatus(connected=False, detail=str(exc))
        self._note_protocol(info)
        if not info.get("service_running"):
            repaired = False
            try:
                repaired = bool(self.repair().get("service_running"))
            except BackendError:
                repaired = False
            if not repaired:
                return DeviceStatus(
                    connected=False,
                    detail="The accessibility service is off and could not be "
                           "repaired remotely. Enable Android Use under "
                           "Settings > Accessibility on the phone.",
                )
        if not info.get("granted"):
            return DeviceStatus(
                connected=False,
                detail="Control is not granted. Ask the phone's owner to open "
                       "Android Use and allow help, or call request_control to "
                       "send them a request they can accept with one tap.",
            )
        return DeviceStatus(
            connected=True,
            serial=self.base,
            model=f"{info.get('manufacturer','')} {info.get('model','')}".strip(),
            transport="app",
        )

    def require_ready(self) -> DeviceStatus:
        # Every tool call starts here, and over cellular each /status costs a
        # noticeable round trip. A phone that was fine seconds ago almost
        # certainly still is - and if it is not, the action itself fails with
        # the real reason (grant lapsed, service revoked), so nothing is hidden.
        if self._ready_cache and time.time() - self._ready_cache[0] < 10:
            return self._ready_cache[1]
        current = self.status()
        if not current.connected:
            self._ready_cache = None
            raise BackendError(current.detail)
        self._ready_cache = (time.time(), current)
        return current

    # -- perception ------------------------------------------------------
    def get_screen(self, wait_idle: bool = False, max_texts: int = 15) -> Screen:
        query = urlencode({"idle": 1 if wait_idle else 0, "texts": max_texts})
        data = self._request(f"/screen?{query}")
        if "error" in data:
            raise BackendError(data["error"])
        self._note_protocol(data)

        def box(value):
            return tuple(value) if isinstance(value, list) and len(value) == 4 else None

        elements = [
            Element(
                index=e["index"],
                label=e.get("label", ""),
                cls=e.get("cls", "item"),
                resource_id=e.get("resource_id", ""),
                editable=e.get("editable", False),
                scrollable=e.get("scrollable", False),
                enabled=e.get("enabled", True),
                checkable=e.get("checkable", False),
                checked=e.get("checked", False),
                bounds=tuple(e.get("bounds", [0, 0, 0, 0])),
                node_ref=str(e["index"]),  # the app can act on the node itself
                focused=e.get("focused", False),
                selected=e.get("selected", False),
                password=e.get("password", False),
                hint=e.get("hint", "") or "",
                long_clickable=e.get("long_clickable", False),
                container=box(e.get("container")),
            )
            for e in data.get("elements", [])
        ]
        screen = Screen(
            package=data.get("package", ""),
            activity=data.get("activity", ""),
            elements=elements,
            texts=list(data.get("texts", [])),
            scroll_region=box(data.get("scroll_region")),
            width=data.get("width", 0),
            height=data.get("height", 0),
            screen_on=data.get("screen_on"),
            locked=data.get("locked"),
            keyboard_open=bool(data.get("keyboard_open", False)),
            toasts=[str(t) for t in data.get("toasts", [])],
            other_window=data.get("other_window", "") or "",
            app_label=data.get("app_label", "") or "",
        )
        self._size = (screen.width, screen.height)
        return screen

    def screenshot(self, max_width: int = 0) -> bytes:
        path = "/screenshot"
        if max_width and self.protocol >= 2:
            path += "?" + urlencode({"max_width": int(max_width)})
        return self._request(path, raw=True, timeout=max(self.timeout, 25))

    def screen_size(self) -> tuple[int, int]:
        # Re-reading the screen just for its size would also renumber the
        # phone's node index, so use what the last read already said.
        if all(self._size):
            return self._size
        screen = self.get_screen()
        return screen.width, screen.height

    # -- actions ---------------------------------------------------------
    def tap_element(self, element: Element) -> None:
        # Click the node itself, which survives layout shifts that would make a
        # coordinate tap land on the wrong thing.
        if element.node_ref is not None:
            result = self._request("/tap", {"index": int(element.node_ref)},
                                   timeout=max(self.timeout, OWNER_CONFIRM_TIMEOUT))
            if result.get("ok"):
                return
        x, y = element.center
        self.tap_xy(x, y)

    def tap_xy(self, x: int, y: int) -> None:
        self._ok(self._request("/tap", {"x": x, "y": y},
                               timeout=max(self.timeout, OWNER_CONFIRM_TIMEOUT)), "tap")

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        self._ok(self._request(
            "/swipe",
            {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "duration_ms": duration_ms},
        ), "swipe")

    def long_press_xy(self, x: int, y: int) -> None:
        self._ok(self._request("/longpress", {"x": x, "y": y}), "long press")

    def long_press_element(self, element: Element) -> None:
        if self.protocol >= 2 and element.node_ref is not None:
            if self._request("/longpress", {"index": int(element.node_ref)}).get("ok"):
                return
        self.long_press_xy(*element.center)

    def double_tap_xy(self, x: int, y: int) -> None:
        if self.protocol >= 2:
            self._ok(self._request("/doubletap", {"x": x, "y": y},
                                   timeout=max(self.timeout, OWNER_CONFIRM_TIMEOUT)), "double tap")
        else:
            super().double_tap_xy(x, y)

    def drag(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 1500) -> None:
        self._needs(2, "Dragging")
        self._ok(self._request(
            "/drag",
            {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "duration_ms": duration_ms},
        ), "drag")

    def pinch(self, cx: int, cy: int, zoom_in: bool) -> None:
        near, far = 80, 380
        self._ok(self._request(
            "/pinch",
            {"cx": cx, "cy": cy, "from_radius": near if zoom_in else far,
             "to_radius": far if zoom_in else near},
        ), "pinch")

    def scroll(self, direction: str, amount: float = 0.6, element: Element | None = None) -> None:
        if direction not in ("up", "down", "left", "right"):
            raise BackendError(f"direction must be up, down, left or right - got {direction!r}")
        if self.protocol >= 2:
            # Accessibility scroll actions move the list itself - no risk of a
            # swipe landing as a tap, or of pull-to-refresh at the top.
            index = int(element.node_ref) if element is not None and element.node_ref else -1
            if self._request("/scroll", {"direction": direction, "index": index}).get("ok"):
                return
        super().scroll(direction, amount, element)

    def _focused_field(self) -> Element:
        screen = self.get_screen()
        fields = [e for e in screen.elements if e.editable]
        focused = [e for e in fields if e.focused]
        if focused:
            return focused[0]
        if len(fields) == 1:
            return fields[0]
        if not fields:
            raise BackendError("No text field is visible. Tap a text field first.")
        raise BackendError(
            "Several text fields are on screen and none is focused - say which one "
            "by its index."
        )

    def type_text(self, text: str) -> None:
        self.type_into(None, text)

    def type_into(
        self, element: Element | None, text: str, clear: bool = False, submit: bool = False
    ) -> str:
        target = element if element is not None else self._focused_field()
        if target.node_ref is None:
            raise BackendError("That field cannot be typed into directly.")
        payload: dict = {"index": int(target.node_ref), "text": text}
        note = ""
        if self.protocol >= 2:
            payload.update({"clear": clear, "submit": submit})
        else:
            # The first app version always replaced the field's contents.
            if not clear and target.label and target.label != target.hint:
                payload["text"] = target.label + text
            if submit:
                note = ("This version of the phone app cannot press enter; tap the "
                        "search or send button instead (or update the app).")
        result = self._request("/text", payload)
        if not result.get("ok"):
            raise BackendError(f"Could not type into '{target.display_label}'.")
        if submit and self.protocol >= 2 and result.get("submitted") is False:
            note = ("Typed it, but the field has no enter action. Tap the search or "
                    "send button to submit.")
        return note

    def supports_unicode_text(self) -> bool:
        return True  # ACTION_SET_TEXT carries any script, unlike `adb input text`

    def press(self, key: str) -> None:
        key = key.lower().strip()
        allowed = KEYS_V2 if self.protocol >= 2 else GLOBAL_KEYS
        if key not in allowed:
            hint = " (update the phone app for more keys)" if self.protocol < 2 else ""
            raise BackendError(
                f"'{key}' is not available through the phone app{hint}. "
                f"Available: {', '.join(allowed)}"
            )
        if key == "wake":
            self.wake_and_unlock()
            return
        if not self._request("/key", {"key": key}).get("ok"):
            raise BackendError(f"The phone refused the '{key}' action.")

    def known_keys(self) -> list[str]:
        return list(KEYS_V2 if self.protocol >= 2 else GLOBAL_KEYS)

    def wake_and_unlock(self) -> str:
        if self.protocol < 2:
            # The first app version could not wake the screen at all.
            return (
                "The phone app cannot wake the screen. Ask the user to wake and unlock "
                "the phone."
            )
        state = self._request("/wake", {})
        if state.get("screen_on") is False:
            return "The screen did not turn on. Ask the user to press the power button."
        if state.get("locked"):
            if state.get("secure"):
                return ("locked: the phone needs a PIN, pattern or fingerprint to unlock "
                        "(the unlock screen is showing for the owner)")
            return "locked: the lock screen is still showing"
        return "awake and unlocked"

    def _ok(self, result: dict, what: str) -> None:
        if not result.get("ok", False):
            raise BackendError(f"The phone could not perform the {what}.")

    # -- apps and settings -----------------------------------------------
    def _apps(self) -> list[dict]:
        entries = self._request("/apps").get("apps", [])
        self._labels = {a["package"]: a.get("label", "") for a in entries}
        return entries

    def installed_packages(self) -> list[str]:
        return sorted({a["package"] for a in self._apps()})

    def app_label(self, package: str) -> str:
        return self._labels.get(package, "")

    def launch_package(self, package: str) -> None:
        self._request("/launch", {"package": package})

    def open_settings_page(self, page: str) -> str:
        action = SETTINGS_ACTIONS.get(page.lower().strip())
        if action is None:
            raise BackendError(f"Unknown settings page {page!r}.")
        self._request("/settings", {"action": action})
        return action

    def settings_pages(self) -> list[str]:
        return sorted(SETTINGS_ACTIONS)

    def resolve_url(self, url: str, kind: str = "web") -> str:
        self._needs(2, "Opening links")
        return str(self._request("/resolve_url", {"url": url, "kind": kind}).get("package", ""))

    def open_url(self, url: str, kind: str = "web", package: str = "") -> str:
        self._needs(2, "Opening links")
        result = self._request("/open_url", {"url": url, "kind": kind, "package": package})
        if not result.get("ok"):
            raise BackendError(result.get("error", "The phone could not open that link."))
        return str(result.get("package", ""))

    def device_state(self) -> dict:
        self._needs(2, "Reading battery, sound and network state")
        return self._request("/device_state")

    def change_setting(self, name: str, value) -> tuple[str, bool]:
        self._needs(2, "Changing settings directly")
        result = self._request("/set_setting", {"name": name, "value": value})
        message = str(result.get("message") or result.get("error") or "")
        if result.get("ok"):
            return message or f"{name} updated", False
        if result.get("opened"):
            return message or f"Opened the {name} controls on the phone.", True
        raise BackendError(message or f"The phone could not change {name}.")

    def settable(self) -> list[str]:
        return list(_APP_SETTABLE) if self.protocol >= 2 else []

    # -- talking to the person holding the phone ---------------------------
    def say(self, text: str, speak: bool = False) -> None:
        self._needs(2, "Showing a message on the phone")
        self._ok(self._request("/message", {"text": text, "speak": speak}), "message")

    def ask(self, question: str, options: list[str], timeout_s: int = 60,
            speak: bool = False) -> str | None:
        self._needs(2, "Asking the phone's owner")
        result = self._request(
            "/ask",
            {"question": question, "options": options, "timeout_s": timeout_s,
             "speak": speak},
            timeout=timeout_s + 15,
        )
        answer = result.get("answer")
        return str(answer) if answer else None

    def request_control(self, minutes: int, reason: str = "") -> str:
        # Deliberately not gated on a grant - asking for one is the point.
        try:
            result = self._request("/request_grant", {"minutes": minutes, "reason": reason})
        except UnsupportedError:
            raise UnsupportedError(
                "This version of the phone app cannot receive requests. Ask the "
                "owner to open Android Use and allow help, and update the app."
            ) from None
        if result.get("already_granted"):
            return "already granted"
        if result.get("throttled"):
            return "throttled"
        return "asked"

    def is_granted(self) -> bool:
        try:
            return bool(self._request("/status", _retries_left=0).get("granted"))
        except BackendError:
            return False

    # -- diagnostics -----------------------------------------------------
    def connectivity_report(self) -> str:
        info = self._request("/status")
        head = (
            f"Phone: {info.get('manufacturer','')} {info.get('model','')} "
            f"(Android {info.get('android','?')})"
        )
        if self.protocol < 2:
            # Without a shell there is no ping; be honest that this is a
            # narrower check than the ADB one.
            return (
                f"{head}\n"
                "Reached the phone over the network, so it has working connectivity.\n"
                "Open the Wi-Fi settings page to inspect or change the connection - "
                "this transport cannot run ping or read radio state directly."
            )
        net = self.device_state().get("network", {})
        internet = net.get("internet")
        lines = [
            head,
            f"Active connection: {net.get('active') or 'none'}"
            + ("" if internet is None else
               f" ({'internet works' if internet else 'NO internet - connected but not getting through'})"),
            f"Wi-Fi radio:   {'on' if net.get('wifi') else 'OFF'}",
            f"Mobile data:   {'on' if net.get('mobile_data') else 'OFF'}",
            f"Airplane mode: {'ON' if net.get('airplane') else 'off'}",
            "The phone answered over the network, so its connection to you works; "
            "the lines above are what the phone itself reports.",
        ]
        return "\n".join(lines)

    def device_info(self) -> str:
        info = self._request("/status")
        remaining = int(info.get("grant_remaining_ms", 0) / 60000)
        version = info.get("app_version") or ("0.1.x" if self.protocol < 2 else "?")
        return (
            f"Manufacturer: {info.get('manufacturer','')}\n"
            f"Model: {info.get('model','')}\n"
            f"Android version: {info.get('android','')}\n"
            f"SDK: {info.get('sdk','')}\n"
            f"Android Use app: {version} (protocol {self.protocol})\n"
            f"Control granted for about {remaining} more minute(s)"
        )

    def capabilities(self) -> list[str]:
        if self.protocol < 2:
            return [
                "Can: read the screen, tap, long-press, swipe, type any language, "
                "open apps and settings pages.",
                "This phone runs an old version of the Android Use app - update it "
                "for waking the screen, enter/submit, direct settings, links, "
                "messages to the owner and more.",
            ]
        return [
            "Can: read the screen, tap/long-press/double-tap/swipe/drag/pinch, type "
            "any language (and submit with enter), open apps and links, wake the "
            "screen, change volume/ringer/Do Not Disturb/flashlight directly (and "
            "brightness/font size once 'Modify system settings' is allowed), show "
            "messages and ask the owner questions on the phone.",
            "Cannot: switch Wi-Fi, mobile data or airplane mode directly (Android "
            "forbids apps from doing it - it opens the right panel instead), or use "
            "the chrome_* tools (those need ADB).",
        ]

    def revoke(self) -> None:
        self._request("/revoke", {})
