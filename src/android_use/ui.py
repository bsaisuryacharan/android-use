"""Parse uiautomator's XML dump into the shared Screen/Element model.

This is the heart of android-use. Rather than asking the model to guess pixel
coordinates from a screenshot, we read the real accessibility tree and return a
numbered list of things that can actually be tapped. The model acts by index.

The tricky part is that Android rows are split across nodes: the tappable
ViewGroup carries no label, and the TextView carrying the label isn't tappable.
So we fold every label into its nearest clickable ancestor, giving one entry per
row the user would recognise.
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET

from . import adb
from .models import Element, Screen

BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
_CLASS_NOISE = ("android.widget.", "android.view.", "androidx.", "android.webkit.")
_MAX_LABEL = 110

# Some builds refuse to dump to /dev/tty; remember that rather than paying
# for a failed attempt on every read.
_tty_dump_works: bool | None = None
_size_cache: dict[str, tuple[float, tuple[int, int]]] = {}


def _short_class(cls: str) -> str:
    for prefix in _CLASS_NOISE:
        if cls.startswith(prefix):
            cls = cls[len(prefix):]
            break
    else:
        cls = cls.rsplit(".", 1)[-1] if "." in cls else cls
    return cls or "item"  # some OEM widgets report a bare package prefix


def _parse_bounds(raw: str) -> tuple[int, int, int, int] | None:
    m = BOUNDS_RE.match(raw or "")
    if not m:
        return None
    x1, y1, x2, y2 = (int(g) for g in m.groups())
    if x2 <= x1 or y2 <= y1:
        return None  # zero-area, not actually on screen
    return x1, y1, x2, y2


def _area(b: tuple[int, int, int, int]) -> int:
    return (b[2] - b[0]) * (b[3] - b[1])


def _is_actionable(node: ET.Element) -> bool:
    return node.get("clickable") == "true" or (node.get("class") or "").endswith("EditText")


def _extract(raw: str) -> str:
    if "<hierarchy" in raw and "</hierarchy>" in raw:
        return raw[raw.index("<hierarchy"): raw.rindex("</hierarchy>") + len("</hierarchy>")]
    return ""


def _dump_xml(retries: int = 3) -> str:
    """uiautomator refuses to dump while the UI animates, so retry briefly.

    Dumping straight to stdout returns the XML in one adb round trip instead
    of two (dump to a file, then cat it), which is a real share of the cost of
    reading the screen over Wi-Fi.
    """
    global _tty_dump_works
    last = ""
    for _ in range(retries):
        direct_failed = False
        if _tty_dump_works is not False:
            try:
                raw = adb.shell_bytes("uiautomator dump /dev/stdout", timeout=45)
                xml = _extract(raw.decode("utf-8", errors="replace"))
                if xml:
                    _tty_dump_works = True
                    return xml
                direct_failed = True
                last = raw.decode("utf-8", errors="replace").strip()[:200]
            except adb.AdbError as exc:
                direct_failed = True
                last = str(exc)
        try:
            adb.shell("uiautomator dump /sdcard/android_use_dump.xml", timeout=45)
            xml = adb.shell("cat /sdcard/android_use_dump.xml", timeout=30)
            if "<hierarchy" in xml:
                # The file route worked where the direct one did not, so this
                # build cannot dump to stdout. A failure of both is just the UI
                # not being idle, and says nothing about stdout.
                if direct_failed and _tty_dump_works is None:
                    _tty_dump_works = False
                return xml[xml.index("<hierarchy"):]
            last = xml.strip()[:200]
        except adb.AdbError as exc:
            last = str(exc)
        time.sleep(0.8)
    raise adb.AdbError(f"Could not read the screen: {last}")


def current_app() -> tuple[str, str]:
    try:
        out = adb.shell("dumpsys window displays | grep mCurrentFocus | head -1")
    except adb.AdbError:
        return "", ""
    m = re.search(r"([A-Za-z0-9_.]+)/([A-Za-z0-9_.$]+)", out)
    return (m.group(1), m.group(2)) if m else ("", "")


def screen_size() -> tuple[int, int]:
    """Portrait size in pixels, cached: it only changes if someone changes
    the display resolution, and asking costs a round trip every read."""
    key = adb.SERIAL or ""
    cached = _size_cache.get(key)
    if cached and time.time() - cached[0] < 300:
        return cached[1]
    try:
        out = adb.shell("wm size")
        m = re.search(r"Override size:\s*(\d+)x(\d+)", out) or re.search(
            r"Physical size:\s*(\d+)x(\d+)", out
        )
        if m:
            size = (int(m.group(1)), int(m.group(2)))
            _size_cache[key] = (time.time(), size)
            return size
    except adb.AdbError:
        pass
    return 0, 0


def _nearest(node, parents, predicate):
    cur = node
    while cur is not None:
        if predicate(cur):
            return cur
        cur = parents.get(cur)
    return None


def parse(xml: str, width: int = 0, height: int = 0,
          max_elements: int = 80, max_texts: int = 15) -> Screen:
    """Turn a uiautomator dump into a Screen. Pure, so it is testable."""
    root = ET.fromstring(xml)

    # `wm size` is the portrait size; in landscape the axes swap, and every
    # coordinate the model sends back must be in the rotated frame.
    rotation = int(root.get("rotation", "0") or 0)
    if rotation in (1, 3) and width and height and width < height:
        width, height = height, width

    # Take the package from the dump itself rather than a separate dumpsys call,
    # so the app name always matches the elements listed below it. The two reads
    # happen at different instants, and the user may be touching the phone too.
    counts: dict[str, int] = {}
    for node in root.iter("node"):
        pkg = node.get("package") or ""
        if pkg and pkg != "com.android.systemui":
            counts[pkg] = counts.get(pkg, 0) + 1
    package = max(counts, key=counts.get) if counts else "com.android.systemui"

    parents: dict = {}
    for parent in root.iter():
        for child in parent:
            parents[child] = parent

    # Actionable nodes that are actually on screen.
    actionable: dict = {}
    scrollables: list[tuple[int, int, int, int]] = []
    max_x = max_y = 0
    for node in root.iter("node"):
        bounds = _parse_bounds(node.get("bounds", ""))
        if bounds is None:
            continue
        max_x, max_y = max(max_x, bounds[2]), max(max_y, bounds[3])
        if node.get("scrollable") == "true":
            scrollables.append(bounds)
        if _is_actionable(node):
            actionable[node] = bounds
    if not width or not height:
        width, height = max_x, max_y

    # Fold every label into the nearest clickable ancestor.
    labels: dict = {n: [] for n in actionable}
    loose_text: list[str] = []
    for node in root.iter("node"):
        if _parse_bounds(node.get("bounds", "")) is None:
            continue
        text = (node.get("text") or "").strip()
        desc = (node.get("content-desc") or "").strip()
        if not text and not desc:
            continue
        owner = _nearest(node, parents, lambda n: n in actionable)
        if owner is None:
            if text and text not in loose_text:
                loose_text.append(text)
            continue
        # Real on-screen text beats content-desc, which is often a long a11y blurb.
        labels[owner].append((text, desc))

    elements: list[Element] = []
    seen: set = set()
    for node, bounds in actionable.items():
        parts = labels[node]
        texts = [t for t, _ in parts if t]
        if texts:
            chosen = texts
        else:
            chosen = [d for _, d in parts if d][:1]
        label = " / ".join(dict.fromkeys(chosen))[:_MAX_LABEL]

        # A checkable descendant (the actual Switch widget) carries the state.
        checkable = checked = False
        for sub in node.iter("node"):
            if sub.get("checkable") == "true":
                checkable = True
                checked = sub.get("checked") == "true"
                break

        key = (label, bounds)
        if key in seen:
            continue
        seen.add(key)
        cls = node.get("class") or ""
        editable = cls.endswith("EditText")
        hint = (node.get("hint") or "").strip()
        if editable and hint and label == hint:
            label = ""  # an empty field showing its placeholder, not real text
        scroller = _nearest(
            parents.get(node), parents, lambda n: n.get("scrollable") == "true"
        )
        container = _parse_bounds(scroller.get("bounds", "")) if scroller is not None else None
        elements.append(
            Element(
                index=0,
                label=label,
                cls=_short_class(cls),
                resource_id=node.get("resource-id") or "",
                editable=editable,
                scrollable=node.get("scrollable") == "true",
                enabled=node.get("enabled") != "false",
                checkable=checkable,
                checked=checked,
                bounds=bounds,
                focused=node.get("focused") == "true",
                selected=node.get("selected") == "true",
                password=node.get("password") == "true",
                hint=hint,
                long_clickable=node.get("long-clickable") == "true",
                container=container,
            )
        )

    # Stacked nodes (a ripple layer over a real button) land on the same spot.
    # Keep whichever one carries the label so the model never sees a blank twin.
    by_center: dict = {}
    for e in elements:
        best = by_center.get(e.center)
        if best is None or (not best.label and e.label):
            by_center[e.center] = e
    elements = list(by_center.values())

    # Top-to-bottom, left-to-right: matches how a person reads the screen.
    elements.sort(key=lambda e: (e.bounds[1], e.bounds[0]))
    note = ""
    if len(elements) > max_elements:
        note = f"Showing {max_elements} of {len(elements)} elements; scroll for more."
        elements = elements[:max_elements]
    for i, e in enumerate(elements):
        e.index = i

    return Screen(
        package=package,
        activity="",
        elements=elements,
        texts=loose_text[:max_texts],
        scroll_region=max(scrollables, key=_area) if scrollables else None,
        width=width,
        height=height,
        note=note,
    )


def get_screen(max_elements: int = 80, max_texts: int = 15) -> Screen:
    xml = _dump_xml()
    width, height = screen_size()
    return parse(xml, width, height, max_elements=max_elements, max_texts=max_texts)
