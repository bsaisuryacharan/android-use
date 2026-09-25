"""Transport-neutral data model for what is on the phone screen.

Both backends produce these types: the ADB backend parses them out of
uiautomator XML, and the on-device client app builds them straight from
AccessibilityNodeInfo. Nothing here knows how the phone is reached.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

Bounds = tuple[int, int, int, int]


@dataclass
class Element:
    index: int
    label: str
    cls: str
    resource_id: str
    editable: bool
    scrollable: bool
    enabled: bool
    checkable: bool
    checked: bool
    bounds: Bounds
    # Set by backends that can act on a node directly instead of by coordinate
    # (the client app uses this; ADB leaves it None and taps the centre).
    node_ref: str | None = None
    # Richer state. All optional so an older client app, which does not send
    # them, still parses - a missing field reads as the harmless default.
    focused: bool = False
    selected: bool = False
    password: bool = False
    hint: str = ""
    long_clickable: bool = False
    # Bounds of the nearest scrollable container, so "scroll the list this
    # item is in" moves that list rather than the whole screen.
    container: Bounds | None = None

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.bounds
        return (x1 + x2) // 2, (y1 + y2) // 2

    @property
    def short_id(self) -> str:
        """'com.whatsapp:id/send_btn' -> 'send_btn'."""
        return self.resource_id.rsplit("/", 1)[-1] if self.resource_id else ""

    @property
    def display_label(self) -> str:
        """The label, or the best clue there is when the app gave none.

        Icon-only buttons are the commonest reason a model cannot tell what
        to tap. The field hint or the developer's view id usually names the
        thing ('search_button', 'Type a message') even when the label is empty.
        """
        if self.label:
            return self.label
        if self.editable and self.hint:
            return f'(empty field: "{self.hint}")'
        if self.hint:
            return f"({self.hint})"
        if self.short_id:
            return f"(no label, id: {self.short_id})"
        return "(no label)"

    def render(self) -> str:
        flags = []
        if not self.enabled:
            flags.append("disabled")
        if self.password:
            flags.append("password field")
        elif self.editable:
            flags.append("text field")
        if self.focused:
            flags.append("focused")
        if self.selected:
            flags.append("selected")
        if self.checkable:
            flags.append("ON" if self.checked else "OFF")
        cx, cy = self.center
        suffix = f" [{', '.join(flags)}]" if flags else ""
        return f"[{self.index}] {self.display_label} <{self.cls}>{suffix} @{cx},{cy}"


@dataclass
class Screen:
    package: str
    activity: str
    elements: list[Element] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    scroll_region: Bounds | None = None
    width: int = 0
    height: int = 0
    note: str = ""
    # Device state that changes what should happen next. None means the
    # transport could not tell, which is different from "no".
    screen_on: bool | None = None
    locked: bool | None = None
    keyboard_open: bool = False
    # Short pop-up messages ("Message sent", "Alarm set for 7:00") vanish in
    # a couple of seconds and are never part of the tree. They are often the
    # only confirmation an action worked, so they are carried separately.
    toasts: list[str] = field(default_factory=list)
    # A window that was deliberately not read - the Claude chat sharing a
    # split screen with the app being driven.
    other_window: str = ""
    app_label: str = ""

    def element(self, index: int) -> Element | None:
        for e in self.elements:
            if e.index == index:
                return e
        return None

    def signature(self) -> str:
        """A fingerprint of what is on screen, for "did anything change?".

        Coordinates are part of it on purpose: a scroll moves every row without
        changing any label, and that is a change worth noticing.
        """
        h = hashlib.sha1(self.package.encode())
        for e in self.elements:
            h.update(f"{e.label}|{e.cls}|{e.bounds}|{e.checked}|{e.focused}|{e.selected}".encode())
        for t in self.texts:
            h.update(t.encode())
        return h.hexdigest()

    def render(self, notes: list[str] | None = None) -> str:
        app = self.package or "unknown"
        if self.app_label and self.app_label.lower() not in app.lower():
            app = f"{self.app_label} ({app})"
        out = [
            f"App: {app}",
            f"Screen: {self.activity or 'unknown'} ({self.width}x{self.height})",
            f"Scrollable: {'yes' if self.scroll_region else 'no'}",
        ]
        state = []
        if self.screen_on is False:
            state.append("the screen is OFF")
        if self.locked:
            state.append("the phone is LOCKED")
        if self.keyboard_open:
            state.append("the keyboard is open (it may cover the lower part of the screen)")
        if state:
            out.append("Phone state: " + "; ".join(state))
        if self.note:
            out.append(f"Note: {self.note}")
        for extra in notes or []:
            out.append(f"Note: {extra}")
        if self.toasts:
            out.append("Pop-up message just shown: " + " | ".join(f'"{t}"' for t in self.toasts))
        if self.texts:
            out.append("\nOn-screen text (not tappable):")
            out += [f"  - {t}" for t in self.texts]
        out.append("\nTappable elements (act on these by index):")
        if self.elements:
            out += ["  " + e.render() for e in self.elements]
        else:
            out.append(
                "  (none found - the screen may be a video, game or secure view; "
                "use take_screenshot to look at it directly)"
            )
        return "\n".join(out)

    def find(self, query: str) -> list[Element]:
        q = query.strip().lower()
        if not q:
            return []
        exact = [e for e in self.elements if e.label.lower() == q]
        if exact:
            return exact
        partial = [e for e in self.elements if q in e.label.lower()]
        if partial:
            return partial
        # Unlabelled icons: fall back to the hint or view id the model was shown.
        return [
            e for e in self.elements
            if not e.label and (q in e.hint.lower() or q in e.short_id.lower())
        ]

    def contains_text(self, query: str) -> bool:
        """Is this text anywhere on screen, tappable or not?"""
        q = query.strip().lower()
        if not q:
            return False
        if any(q in e.label.lower() or q in e.hint.lower() for e in self.elements):
            return True
        return any(q in t.lower() for t in self.texts + self.toasts)


@dataclass
class DeviceStatus:
    """How the phone is reachable, for reporting to the user."""
    connected: bool
    serial: str = ""
    model: str = ""
    transport: str = ""          # "usb", "wifi", or "app"
    detail: str = ""

    def render(self) -> str:
        if not self.connected:
            return f"Not connected. {self.detail}"
        where = f" over {self.transport}" if self.transport else ""
        name = f"{self.serial} ({self.model})" if self.model else self.serial
        return f"Connected: {name}{where}"
