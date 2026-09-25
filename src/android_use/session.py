"""What the model last saw on each phone, and the checks built on it.

The model picks an index from a screen it read a few seconds ago. By the time
the tap arrives the screen may have moved: a notification slid in, a list
loaded more rows, the owner touched the phone. Tapping whatever now sits at
that index is how "turn off the alarm" becomes "delete the alarm". So every
action by index is checked against the screen the model actually saw, and is
refused when the element it meant cannot be found with confidence.
"""
from __future__ import annotations

import threading

from .models import Bounds, Element, Screen


class ScreenMemory:
    """The last screen shown to the model, per phone."""

    def __init__(self) -> None:
        self._screens: dict[str, Screen] = {}
        self._still: dict[str, int] = {}
        self._lock = threading.Lock()

    def remember(self, key: str, screen: Screen) -> None:
        with self._lock:
            self._screens[key] = screen

    def last(self, key: str) -> Screen | None:
        with self._lock:
            return self._screens.get(key)

    def forget(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._screens.clear()
                self._still.clear()
            else:
                self._screens.pop(key, None)
                self._still.pop(key, None)

    def note_outcome(self, key: str, changed: bool) -> int:
        """Count actions in a row that left the screen exactly as it was.

        One no-op tap is normal (a toggle that animates slowly, a field that
        was already focused). Several in a row means the model is stuck, and
        saying so is cheaper than letting it keep trying the same thing.
        """
        with self._lock:
            streak = 0 if changed else self._still.get(key, 0) + 1
            self._still[key] = streak
            return streak


memory = ScreenMemory()


def iou(a: Bounds, b: Bounds) -> float:
    """Intersection over union of two rectangles, 0..1."""
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    if not inter:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / float(area_a + area_b - inter)


def identity(e: Element) -> tuple[str, str, str]:
    """What makes two elements "the same thing" across two reads.

    A text field's label is its contents, which change as soon as anyone
    types, so fields are matched by kind and id alone.
    """
    return (e.cls, e.resource_id, "" if e.editable else e.label)


def overlap(before: Screen, now: Screen) -> float:
    """Share of the earlier screen's elements still present now, 0..1."""
    old = [identity(e) for e in before.elements]
    if not old:
        return 0.0
    current = {identity(e) for e in now.elements}
    return sum(1 for i in old if i in current) / len(old)


def locate(
    want: Element, before: Screen, now: Screen, strict: bool = False
) -> tuple[Element | None, str]:
    """Find, in the screen as it is now, the element the model picked earlier.

    Returns (element, how) where `how` is "same" when it has not moved, or
    "moved" when it has; or (None, reason) when it cannot be found safely.

    `strict` is for actions that cannot be undone (send, pay, delete): the
    element must still be where the model saw it. A moved "Delete" button may
    well belong to a different row.
    """
    same = [e for e in now.elements if identity(e) == identity(want)]
    if not same:
        return None, "gone"
    near = sorted(
        (e for e in same if iou(e.bounds, want.bounds) > 0.5),
        key=lambda e: iou(e.bounds, want.bounds),
        reverse=True,
    )
    if near:
        return near[0], "same"
    if strict:
        return None, "moved"
    # A unique element that moved is still the same element - but only if the
    # screen around it is broadly the same page. On a different page, an
    # identically labelled button is a different button.
    if len(same) == 1 and overlap(before, now) >= 0.5:
        return same[0], "moved"
    if len(same) > 1:
        return None, "ambiguous"
    return None, "context"
