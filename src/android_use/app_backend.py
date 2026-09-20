"""Backend that talks to the client app on the phone over HTTP.

This is the transport that works from anywhere: the phone runs an
AccessibilityService behind a token-authenticated bridge, reachable over a
Tailscale address on any network, including cellular. No ADB, no developer
options, and it survives a reboot.

It is also more capable than ADB - nodes are clicked directly, text is set with
ACTION_SET_TEXT (so any script or emoji works), and gestures include long
press and pinch.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .backend import Backend, BackendError
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
}

GLOBAL_KEYS = ["back", "home", "recents", "notifications", "quick_settings", "lock"]


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
        self._labels: dict[str, str] = {}
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

    def _request(
        self, path: str, payload: dict | None = None, raw: bool = False,
        _no_repair: bool = False, _retries_left: int = 1,
    ):
        url = f"{self.base}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
        req.add_header("Authorization", f"Bearer {self.token}")
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            try:
                detail = json.loads(detail).get("error", detail)
            except json.JSONDecodeError:
                pass
            if exc.code == 401:
                raise BackendError(
                    "The phone rejected the pairing token. Open Android Use on the "
                    "phone and use 'Show pairing token' to check it."
                ) from exc
            if exc.code == 403:
                raise BackendError(
                    f"{detail}\nOpen Android Use on the phone and grant control."
                ) from exc
            # A revoked accessibility service is recoverable: repair, then retry
            # once. Guard against recursing if /repair itself reports it.
            if (
                exc.code == 503
                and not _no_repair
                and "accessibility" in detail.lower()
            ):
                try:
                    self.repair()
                except BackendError:
                    raise BackendError(detail) from exc
                return self._request(path, payload, raw, _no_repair=True)
            raise BackendError(detail) from exc
        except (urllib.error.URLError, OSError) as exc:
            # A sleeping phone drops its Tailscale path, so the first request
            # after idle has to re-establish it and can time out once. Treating
            # that single timeout as "phone offline" is wrong and alarming, so
            # give it a second chance before saying so.
            if _retries_left > 0:
                time.sleep(2.0)
                return self._request(
                    path, payload, raw, _no_repair, _retries_left - 1
                )
            raise BackendError(
                f"Could not reach the phone at {self.base}: {exc}. Check that the "
                "phone is online and, if you are away from home, that Tailscale is "
                "connected on both devices."
            ) from exc
        return body if raw else json.loads(body or b"{}")

    # -- connection ------------------------------------------------------
    def status(self) -> DeviceStatus:
        # A health probe should answer quickly. Retrying here would make
        # list_phones crawl whenever one phone is off, so probes fail fast and
        # only real actions get the second chance.
        try:
            info = self._request("/status", _retries_left=0)
        except BackendError as exc:
            return DeviceStatus(connected=False, detail=str(exc))
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
                detail="Control is not granted. Open Android Use on the phone and "
                       "grant access for a period of time.",
            )
        return DeviceStatus(
            connected=True,
            serial=self.base,
            model=f"{info.get('manufacturer','')} {info.get('model','')}".strip(),
            transport="app",
        )

    def require_ready(self) -> DeviceStatus:
        current = self.status()
        if not current.connected:
            raise BackendError(current.detail)
        return current

    # -- perception ------------------------------------------------------
    def get_screen(self) -> Screen:
        data = self._request("/screen")
        if "error" in data:
            raise BackendError(data["error"])
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
            )
            for e in data.get("elements", [])
        ]
        region = data.get("scroll_region")
        return Screen(
            package=data.get("package", ""),
            activity=data.get("activity", ""),
            elements=elements,
            texts=list(data.get("texts", [])),
            scroll_region=tuple(region) if isinstance(region, list) else None,
            width=data.get("width", 0),
            height=data.get("height", 0),
        )

    def screenshot(self) -> bytes:
        return self._request("/screenshot", raw=True)

    def screen_size(self) -> tuple[int, int]:
        screen = self.get_screen()
        return screen.width, screen.height

    # -- actions ---------------------------------------------------------
    def tap_element(self, element: Element) -> None:
        # Click the node itself, which survives layout shifts that would make a
        # coordinate tap land on the wrong thing.
        if element.node_ref is not None:
            result = self._request("/tap", {"index": int(element.node_ref)})
            if result.get("ok"):
                return
        x, y = element.center
        self.tap_xy(x, y)

    def tap_xy(self, x: int, y: int) -> None:
        self._request("/tap", {"x": x, "y": y})

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        self._request(
            "/swipe",
            {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "duration_ms": duration_ms},
        )

    def long_press(self, x: int, y: int) -> None:
        self._request("/longpress", {"x": x, "y": y})

    def pinch(self, cx: int, cy: int, from_radius: int, to_radius: int) -> None:
        self._request(
            "/pinch",
            {"cx": cx, "cy": cy, "from_radius": from_radius, "to_radius": to_radius},
        )

    def type_text(self, text: str) -> None:
        screen = self.get_screen()
        target = next((e for e in screen.elements if e.editable), None)
        if target is None:
            raise BackendError(
                "No text field is visible. Tap a text field first."
            )
        result = self._request("/text", {"index": int(target.node_ref), "text": text})
        if not result.get("ok"):
            raise BackendError(f"Could not type into '{target.label}'.")

    def type_into(self, index: int, text: str) -> bool:
        return bool(self._request("/text", {"index": index, "text": text}).get("ok"))

    def supports_unicode_text(self) -> bool:
        return True  # ACTION_SET_TEXT carries any script, unlike `adb input text`

    def press(self, key: str) -> None:
        key = key.lower().strip()
        if key not in GLOBAL_KEYS:
            raise BackendError(
                f"'{key}' is not available through the phone app. "
                f"Available: {', '.join(GLOBAL_KEYS)}"
            )
        if not self._request("/key", {"key": key}).get("ok"):
            raise BackendError(f"The phone refused the '{key}' action.")

    def known_keys(self) -> list[str]:
        return list(GLOBAL_KEYS)

    def wake_and_unlock(self) -> str:
        # The app cannot wake the screen; that needs a real power keypress.
        return (
            "The phone app cannot wake the screen. Ask the user to wake and unlock "
            "the phone."
        )

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

    # -- diagnostics -----------------------------------------------------
    def connectivity_report(self) -> str:
        # Without a shell there is no ping; report what the phone can see and be
        # honest that this is a narrower check than the ADB one.
        info = self._request("/status")
        return (
            f"Phone: {info.get('manufacturer','')} {info.get('model','')} "
            f"(Android {info.get('android','?')})\n"
            "Reached the phone over the network, so it has working connectivity.\n"
            "Open the Wi-Fi settings page to inspect or change the connection - "
            "this transport cannot run ping or read radio state directly."
        )

    def device_info(self) -> str:
        info = self._request("/status")
        remaining = int(info.get("grant_remaining_ms", 0) / 60000)
        return (
            f"Manufacturer: {info.get('manufacturer','')}\n"
            f"Model: {info.get('model','')}\n"
            f"Android version: {info.get('android','')}\n"
            f"SDK: {info.get('sdk','')}\n"
            f"Control granted for about {remaining} more minute(s)"
        )

    def revoke(self) -> None:
        self._request("/revoke", {})
