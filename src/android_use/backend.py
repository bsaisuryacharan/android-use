"""The contract every transport implements.

There are two backends: ADB (over USB or Wi-Fi) and the on-device client app.
The MCP tools in server.py only ever talk to this interface, so a transport
can be added or improved without touching them.

The abstract methods are what every transport must do. The rest have default
implementations built from those - a long press is a swipe that does not
move - so a capability one transport lacks degrades to something reasonable
instead of breaking the tool, and anything truly impossible says so plainly.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod

from .models import DeviceStatus, Element, Screen


class BackendError(RuntimeError):
    """Raised when the phone cannot be reached or a command fails."""


class UnsupportedError(BackendError):
    """This transport, or this version of the phone app, cannot do that."""


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
    def get_screen(self, wait_idle: bool = False, max_texts: int = 15) -> Screen:
        """Read the screen. `wait_idle` lets the UI finish animating first;
        `max_texts` caps the non-tappable text (raise it to read an article)."""

    @abstractmethod
    def screenshot(self, max_width: int = 0) -> bytes:
        """Encoded image bytes of the current screen (PNG or JPEG).
        `max_width` is a hint: 0 means whatever the transport sends by default."""

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
        """ADB's `input text` is ASCII-only; the client app is not."""

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

    # -- gestures, with defaults built from the primitives above ----------
    def long_press_xy(self, x: int, y: int) -> None:
        self.swipe(x, y, x, y, 800)

    def long_press_element(self, element: Element) -> None:
        self.long_press_xy(*element.center)

    def double_tap_xy(self, x: int, y: int) -> None:
        self.tap_xy(x, y)
        time.sleep(0.05)
        self.tap_xy(x, y)

    def drag(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 1500) -> None:
        """Press, hold, move, release - moving an icon, a slider, a list row."""
        self.swipe(x1, y1, x2, y2, duration_ms)

    def pinch(self, cx: int, cy: int, zoom_in: bool) -> None:
        raise UnsupportedError(
            "Pinch-to-zoom needs the Android Use app on the phone; ADB can only "
            "send one finger. Try a double tap, which zooms many maps and photos."
        )

    def scroll(self, direction: str, amount: float = 0.6, element: Element | None = None) -> None:
        """Scroll so that content further in `direction` comes into view.

        With an element, the swipe stays inside the list that element belongs
        to - a carousel or a settings panel - rather than the whole screen.
        """
        region = None
        if element is not None:
            region = element.container or (element.bounds if element.scrollable else None)
        if region is None:
            width, height = self.screen_size()
            if not width or not height:
                raise BackendError("Could not determine the screen size.")
            region = (0, 0, width, height)
        x1, y1, x2, y2 = region
        w, h = x2 - x1, y2 - y1
        cx, cy = x1 + w // 2, y1 + h // 2
        span_y, span_x = int(h * amount / 2), int(w * amount / 2)
        moves = {
            "down": (cx, cy + span_y, cx, cy - span_y),
            "up": (cx, cy - span_y, cx, cy + span_y),
            "right": (cx + span_x, cy, cx - span_x, cy),
            "left": (cx - span_x, cy, cx + span_x, cy),
        }
        if direction not in moves:
            raise BackendError(
                f"direction must be up, down, left or right - got {direction!r}"
            )
        self.swipe(*moves[direction], 400)

    # -- typing ----------------------------------------------------------
    def type_into(
        self, element: Element | None, text: str, clear: bool = False, submit: bool = False
    ) -> str:
        """Type into a specific field (or the focused one when None).

        The default focuses the field with a tap, deletes what is there if
        asked, types, and presses enter to submit. Returns a note for the
        model when something only partly worked, else "".
        """
        if element is not None and not element.focused:
            self.tap_element(element)
            time.sleep(0.4)
        if clear and element is not None:
            existing = element.label if element.label and element.label != element.hint else ""
            for _ in range(len(existing) + 2 if existing else 0):
                self.press("delete")
        if text:
            self.type_text(text)
        if submit:
            self.press("enter")
        return ""

    # -- links, state and settings ----------------------------------------
    def resolve_url(self, url: str, kind: str = "web") -> str:
        """Which app would open this link: a package name, "android" when the
        system would ask which app to use, or "" when nothing can open it."""
        raise UnsupportedError("Opening links is not available on this connection.")

    def open_url(self, url: str, kind: str = "web", package: str = "") -> str:
        """Open a link; `package` forces a specific app (used to send a web
        link to the browser instead of an app that is not allowed). Returns
        the package it opened in, when known."""
        raise UnsupportedError("Opening links is not available on this connection.")

    def device_state(self) -> dict:
        """Battery, sound, display, network and storage, as far as known.
        See server.render_device_state for the shape."""
        raise UnsupportedError("Reading device state is not available on this connection.")

    def change_setting(self, name: str, value) -> tuple[str, bool]:
        """Apply one setting. Returns (message, opened_ui): opened_ui is True
        when the phone could not change it silently and instead put the right
        panel on screen for someone to tap."""
        raise UnsupportedError("Changing settings directly is not available on this connection.")

    def settable(self) -> list[str]:
        """Settings this transport can change directly, without the UI."""
        return []

    # -- talking to the person holding the phone ---------------------------
    def say(self, text: str, speak: bool = False) -> None:
        raise UnsupportedError(
            "Showing a message on the phone needs the Android Use app on it; "
            "ADB has no way to draw on the screen."
        )

    def ask(self, question: str, options: list[str], timeout_s: int = 60,
            speak: bool = False) -> str | None:
        raise UnsupportedError(
            "Asking the phone's owner a question needs the Android Use app on the "
            "phone; ADB has no way to show buttons on the screen."
        )

    def request_control(self, minutes: int, reason: str = "") -> str:
        raise UnsupportedError(
            "Requesting control is part of the Android Use app. Over ADB, control "
            "is whatever the USB debugging authorisation allows."
        )

    def capabilities(self) -> list[str]:
        """Plain-language list of what this transport can and cannot do."""
        return []
