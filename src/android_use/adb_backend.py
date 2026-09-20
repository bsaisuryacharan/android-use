"""Backend that drives the phone through ADB, over USB or Wi-Fi.

Wi-Fi is not a separate backend: adb makes the transport invisible, so the only
difference is that we may have to bring the connection up first.
"""
from __future__ import annotations

import time

import os

from . import actions, adb, apps, ui, wireless
from .backend import Backend, BackendError
from .models import DeviceStatus, Element, Screen


class AdbBackend(Backend):
    name = "adb"

    def __init__(self, auto_wireless: bool = True):
        self.auto_wireless = auto_wireless

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
    def get_screen(self) -> Screen:
        try:
            return ui.get_screen()
        except adb.AdbError as exc:
            raise BackendError(str(exc)) from exc

    def screenshot(self) -> bytes:
        try:
            return adb.shell_bytes("screencap -p")
        except adb.AdbError as exc:
            raise BackendError(str(exc)) from exc

    def screen_size(self) -> tuple[int, int]:
        return ui.screen_size()

    # -- actions ---------------------------------------------------------
    def tap_element(self, element: Element) -> None:
        # ADB has no handle on the node itself, so the centre point is the best
        # we can do. The client app will click the node directly instead.
        x, y = element.center
        actions.tap(x, y)

    def tap_xy(self, x: int, y: int) -> None:
        actions.tap(x, y)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        actions.swipe(x1, y1, x2, y2, duration_ms)

    def type_text(self, text: str) -> None:
        actions.type_text(text)

    def supports_unicode_text(self) -> bool:
        return False  # `input text` cannot send emoji or non-Latin scripts

    def press(self, key: str) -> None:
        try:
            actions.press(key)
        except ValueError as exc:
            raise BackendError(str(exc)) from exc

    def known_keys(self) -> list[str]:
        return sorted(actions.KEYS)

    def wake_and_unlock(self) -> str:
        return actions.wake_and_unlock()

    # -- apps and settings -----------------------------------------------
    def installed_packages(self) -> list[str]:
        try:
            return apps.installed_packages()
        except adb.AdbError as exc:
            raise BackendError(str(exc)) from exc

    def launch_package(self, package: str) -> None:
        actions.launch_package(package)

    def open_settings_page(self, page: str) -> str:
        try:
            return actions.open_settings_page(page)
        except ValueError as exc:
            raise BackendError(str(exc)) from exc

    def settings_pages(self) -> list[str]:
        return sorted(actions.SETTINGS_PAGES)

    # -- diagnostics -----------------------------------------------------
    def connectivity_report(self) -> str:
        return actions.connectivity_report()

    def device_info(self) -> str:
        return actions.device_info()


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
