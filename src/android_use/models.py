"""Transport-neutral data model for what is on the phone screen.

Both backends produce these types: the ADB backend parses them out of
uiautomator XML, and the on-device client app will build them straight from
AccessibilityNodeInfo. Nothing here knows how the phone is reached.
"""
from __future__ import annotations

from dataclasses import dataclass, field


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
    bounds: tuple[int, int, int, int]
    # Set by backends that can act on a node directly instead of by coordinate
    # (the client app will use this; ADB leaves it None and taps the centre).
    node_ref: str | None = None

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.bounds
        return (x1 + x2) // 2, (y1 + y2) // 2

    def render(self) -> str:
        flags = []
        if not self.enabled:
            flags.append("disabled")
        if self.editable:
            flags.append("text field")
        if self.checkable:
            flags.append("ON" if self.checked else "OFF")
        cx, cy = self.center
        suffix = f" [{', '.join(flags)}]" if flags else ""
        return f"[{self.index}] {self.label or '(no label)'} <{self.cls}>{suffix} @{cx},{cy}"


@dataclass
class Screen:
    package: str
    activity: str
    elements: list[Element] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    scroll_region: tuple[int, int, int, int] | None = None
    width: int = 0
    height: int = 0
    note: str = ""

    def render(self) -> str:
        out = [
            f"App: {self.package or 'unknown'}",
            f"Screen: {self.activity or 'unknown'} ({self.width}x{self.height})",
            f"Scrollable: {'yes' if self.scroll_region else 'no'}",
        ]
        if self.note:
            out.append(f"Note: {self.note}")
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
        exact = [e for e in self.elements if e.label.lower() == q]
        return exact or [e for e in self.elements if q in e.label.lower()]


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
