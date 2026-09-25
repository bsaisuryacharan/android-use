"""Backend that drives the phone through ADB, over USB or Wi-Fi.

Wi-Fi is not a separate backend: adb makes the transport invisible, so the only
difference is that we may have to bring the connection up first.
"""
from __future__ import annotations

import os
import time

from . import actions, adb, apps, ui, wireless
from .backend import Backend, BackendError, UnsupportedError
from .models import DeviceStatus, Element, Screen


class AdbBackend(Backend):
    name = "adb"

    def __init__(self, auto_wireless: bool = True):
        self.auto_wireless = auto_wireless
        self._size: tuple[int, int] = (0, 0)

    # -- connection ------------------------------------------------------
    def status(self) -> DeviceStatus:
        try:
            device = adb.require_device()
        except adb.AdbError as exc:
            return DeviceStatus(connected=False, detail=str(exc))
        return DeviceStatus(
            connected=True,
            serial=device.serial,
            model=device.model,
            transport="wifi" if wireless.is_wireless(device.serial) else "usb",
        )

    def require_ready(self) -> DeviceStatus:
        current = self.status()
        if current.connected:
            return current
        if self.auto_wireless:
            ok, msg = wireless.ensure_connected()
            if ok:
                found = self.status()
                if found.connected:
                    return found
            raise BackendError(f"{current.detail}\n\nTried wirelessly: {msg}")
        raise BackendError(current.detail)

    # -- perception ------------------------------------------------------
    def get_screen(self, wait_idle: bool = False, max_texts: int = 15) -> Screen:
        try:
            if wait_idle:
                # uiautomator waits for the UI to go idle by itself; this only
                # gives a transition a head start so it is not caught mid-way.
                time.sleep(0.5)
            screen = ui.get_screen(max_texts=max_texts)
            state = actions.probe_state()
        except adb.AdbError as exc:
            raise BackendError(str(exc)) from exc
        screen.screen_on = state["screen_on"]
        screen.locked = state["locked"]
        screen.keyboard_open = state["keyboard_open"]
        # Only trust the focused activity when it belongs to the app the dump
        # came from; the two reads happen at different instants.
        if state["package"] == screen.package:
            screen.activity = state["activity"]
        self._size = (screen.width, screen.height)
        return screen

    def screenshot(self, max_width: int = 0) -> bytes:
        try:
            return adb.shell_bytes("screencap -p")
        except adb.AdbError as exc:
            raise BackendError(str(exc)) from exc

    def screen_size(self) -> tuple[int, int]:
        # The last read knows about rotation; `wm size` alone does not.
        return self._size if all(self._size) else ui.screen_size()

    # -- actions ---------------------------------------------------------
    def tap_element(self, element: Element) -> None:
        # ADB has no handle on the node itself, so the centre point is the best
        # we can do. The client app clicks the node directly instead.
        self.tap_xy(*element.center)

    def tap_xy(self, x: int, y: int) -> None:
        self._run(actions.tap, x, y)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        self._run(actions.swipe, x1, y1, x2, y2, duration_ms)

    def double_tap_xy(self, x: int, y: int) -> None:
        self._run(actions.double_tap, x, y)

    def drag(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 1500) -> None:
        self._run(actions.drag, x1, y1, x2, y2, duration_ms)

    def type_text(self, text: str) -> None:
        self._run(actions.type_text, text)

    def type_into(
        self, element: Element | None, text: str, clear: bool = False, submit: bool = False
    ) -> str:
        if element is not None and not element.focused:
            self.tap_element(element)
            time.sleep(0.5)
        if clear:
            if element is not None:
                existing = element.label if element.label != element.hint else ""
                count = len(existing) + 2 if existing else 0
            else:
                count = 120  # unknown contents: clear generously
            self._run(actions.delete_chars, count)
        if text:
            self._run(actions.type_text, text)
        if submit:
            self.press("enter")
        return ""

    def supports_unicode_text(self) -> bool:
        return False  # `input text` cannot send emoji or non-Latin scripts

    def press(self, key: str) -> None:
        try:
            actions.press(key)
        except ValueError as exc:
            raise BackendError(str(exc)) from exc
        except adb.AdbError as exc:
            raise BackendError(str(exc)) from exc

    def known_keys(self) -> list[str]:
        return sorted(set(actions.KEYS) | set(actions.KEY_COMMANDS))

    def wake_and_unlock(self) -> str:
        try:
            return actions.wake_and_unlock()
        except adb.AdbError as exc:
            raise BackendError(str(exc)) from exc

    # -- apps and settings -----------------------------------------------
    def installed_packages(self) -> list[str]:
        try:
            return apps.installed_packages()
        except adb.AdbError as exc:
            raise BackendError(str(exc)) from exc

    def launch_package(self, package: str) -> None:
        self._run(actions.launch_package, package)

    def open_settings_page(self, page: str) -> str:
        try:
            return actions.open_settings_page(page)
        except (ValueError, adb.AdbError) as exc:
            raise BackendError(str(exc)) from exc

    def settings_pages(self) -> list[str]:
        return sorted(actions.SETTINGS_PAGES)

    def resolve_url(self, url: str, kind: str = "web") -> str:
        return actions.resolve_url(url, kind)

    def open_url(self, url: str, kind: str = "web", package: str = "") -> str:
        self._run(actions.open_url, url, kind, package)
        return package

    def device_state(self) -> dict:
        try:
            return actions.device_state()
        except adb.AdbError as exc:
            raise BackendError(str(exc)) from exc

    def change_setting(self, name: str, value) -> tuple[str, bool]:
        if name == "flashlight":
            raise UnsupportedError(
                "ADB has no command for the flashlight. Open quick settings "
                "(press_key('quick_settings')) and tap the Flashlight tile instead."
            )
        try:
            return actions.change_setting(name, value), False
        except (ValueError, adb.AdbError) as exc:
            raise BackendError(f"Could not change {name}: {exc}") from exc

    def settable(self) -> list[str]:
        return list(actions.SETTABLE)

    # -- diagnostics -----------------------------------------------------
    def connectivity_report(self) -> str:
        return actions.connectivity_report()

    def device_info(self) -> str:
        return actions.device_info()

    def capabilities(self) -> list[str]:
        return [
            "Can: read the screen, tap/long-press/double-tap/swipe/drag, type plain "
            "text, hardware keys, open apps and links, change settings directly "
            "(volume, brightness, font size, Wi-Fi, mobile data, Bluetooth, "
            "airplane mode, Do Not Disturb), drive Chrome by DOM (chrome_* tools).",
            "Cannot: type emoji or non-Latin scripts, pinch-zoom, show messages or "
            "questions on the phone, or toggle the flashlight - those need the "
            "Android Use app.",
        ]

    def _run(self, fn, *args):
        try:
            return fn(*args)
        except adb.AdbError as exc:
            raise BackendError(str(exc)) from exc


_backends: dict[str, Backend] = {}
_adb_fallback: Backend | None = None


def backend_for(device: str = "") -> tuple[Backend | None, str]:
    """Resolve a phone name to a backend, or explain why it cannot be resolved.

    Returns (backend, error). Callers must check the error rather than assume a
    backend - acting on the wrong phone is worse than doing nothing.
    """
    from . import devices

    forced = os.environ.get("ANDROID_USE_BACKEND", "").strip().lower()
    if forced == "adb" and not device:
        return _adb(), ""

    phone, err = devices.resolve(device)
    if phone is None:
        # No phones configured at all: fall back to adb so a cabled phone still
        # works. A genuinely ambiguous name is an error, not a fallback.
        if not devices.load_phones():
            return _adb(), ""
        return None, err

    cached = _backends.get(phone.name)
    if cached is not None:
        return cached, ""

    from .app_backend import AppBackend

    backend = AppBackend(phone.host, phone.port, phone.token, proxy=phone.proxy)
    _backends[phone.name] = backend
    return backend, ""


def _adb() -> Backend:
    global _adb_fallback
    if _adb_fallback is None:
        _adb_fallback = AdbBackend()
    return _adb_fallback


def adb_fallback() -> Backend:
    """The ADB backend, regardless of which phones are registered - for the
    wireless-debugging setup tools, which are about adb and nothing else."""
    return _adb()


def get_backend(device: str = "") -> Backend:
    """Backwards-compatible accessor: returns a backend, falling back to adb."""
    backend, err = backend_for(device)
    if backend is None:
        return _adb()
    return backend


def reset_backend() -> None:
    """Forget cached backends so the next call re-reads the config."""
    global _adb_fallback
    _backends.clear()
    _adb_fallback = None
