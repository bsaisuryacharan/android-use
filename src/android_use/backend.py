"""The contract every transport implements.

Today there is one backend (ADB, over USB or Wi-Fi). The on-device client app
will be a second one. The MCP tools in server.py only ever talk to this
interface, so adding the app backend does not touch them.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .models import DeviceStatus, Element, Screen


class BackendError(RuntimeError):
    """Raised when the phone cannot be reached or a command fails."""


class Backend(ABC):
    name: str = "backend"

    # -- connection ------------------------------------------------------
    @abstractmethod
    def status(self) -> DeviceStatus:
        """Report whether a phone is reachable, without raising."""

    @abstractmethod
    def require_ready(self) -> DeviceStatus:
        """Return a usable device or raise BackendError explaining why not."""

    # -- perception ------------------------------------------------------
    @abstractmethod
    def get_screen(self) -> Screen: ...

    @abstractmethod
    def screenshot(self) -> bytes:
        """Raw PNG bytes of the current screen."""

    @abstractmethod
    def screen_size(self) -> tuple[int, int]: ...

    # -- actions ---------------------------------------------------------
    @abstractmethod
    def tap_element(self, element: Element) -> None:
        """Activate an element. Backends that can click a node directly should
        do so; coordinate taps are a fallback, not the contract."""

    @abstractmethod
    def tap_xy(self, x: int, y: int) -> None: ...

    @abstractmethod
    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None: ...

    @abstractmethod
    def type_text(self, text: str) -> None: ...

    @abstractmethod
    def supports_unicode_text(self) -> bool:
        """ADB's `input text` is ASCII-only; the client app will not be."""

    @abstractmethod
    def press(self, key: str) -> None: ...

    @abstractmethod
    def known_keys(self) -> list[str]: ...

    @abstractmethod
    def wake_and_unlock(self) -> str: ...

    # -- apps and settings -----------------------------------------------
    @abstractmethod
    def installed_packages(self) -> list[str]: ...

    @abstractmethod
    def launch_package(self, package: str) -> None: ...

    @abstractmethod
    def open_settings_page(self, page: str) -> str: ...

    @abstractmethod
    def settings_pages(self) -> list[str]: ...

    # -- diagnostics -----------------------------------------------------
    @abstractmethod
    def connectivity_report(self) -> str: ...

    @abstractmethod
    def device_info(self) -> str: ...

    # -- shared helpers ---------------------------------------------------
    def scroll(self, direction: str, amount: float = 0.6) -> None:
        """Direction-based scrolling, expressed once for every backend."""
        width, height = self.screen_size()
        if not width or not height:
            raise BackendError("Could not determine the screen size.")
        cx, mid = width // 2, height // 2
        span = int(height * amount / 2)
        moves = {
            "down": (cx, mid + span, cx, mid - span),
            "up": (cx, mid - span, cx, mid + span),
            "left": (int(width * 0.8), mid, int(width * 0.2), mid),
            "right": (int(width * 0.2), mid, int(width * 0.8), mid),
        }
        if direction not in moves:
            raise BackendError(
                f"direction must be up, down, left or right - got {direction!r}"
            )
        self.swipe(*moves[direction], 400)
