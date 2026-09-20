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


def _dump_xml(retries: int = 3) -> str:
    """uiautomator refuses to dump while the UI animates, so retry briefly."""
    last = ""
    for _ in range(retries):
        try:
            adb.shell("uiautomator dump /sdcard/android_use_dump.xml", timeout=45)
            xml = adb.shell("cat /sdcard/android_use_dump.xml", timeout=30)
            if "<hierarchy" in xml:
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
    try:
        out = adb.shell("wm size")
        m = re.search(r"Override size:\s*(\d+)x(\d+)", out) or re.search(
            r"Physical size:\s*(\d+)x(\d+)", out
        )
        if m:
            return int(m.group(1)), int(m.group(2))
    except adb.AdbError:
        pass
    return 0, 0


def _nearest_actionable(node, parents, actionable):
    cur = node
    while cur is not None:
        if cur in actionable:
            return cur
        cur = parents.get(cur)
    return None


def get_screen(max_elements: int = 80) -> Screen:
    root = ET.fromstring(_dump_xml())
    width, height = screen_size()
    _, activity = current_app()

    # Take the package from the dump itself rather than a separate dumpsys call,
    # so the app name always matches the elements listed below it. The two reads
    # happen at different instants, and the user may be touching the phone too.
    counts: dict[str, int] = {}
    for node in root.iter("node"):
        pkg = node.get("package") or ""
        if pkg and pkg != "com.android.systemui":
            counts[pkg] = counts.get(pkg, 0) + 1
    package = max(counts, key=counts.get) if counts else "com.android.systemui"
    if not activity.startswith(package):
        activity = ""  # stale focus info; better to say nothing than mislead

    parents: dict = {}
    for parent in root.iter():
        for child in parent:
            parents[child] = parent

    # Actionable nodes that are actually on screen.
    actionable: dict = {}
    scrollables: list[tuple[int, int, int, int]] = []
    for node in root.iter("node"):
        bounds = _parse_bounds(node.get("bounds", ""))
        if bounds is None:
            continue
        if node.get("scrollable") == "true":
            scrollables.append(bounds)
        if _is_actionable(node):
            actionable[node] = bounds

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
        owner = _nearest_actionable(node, parents, actionable)
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
        elements.append(
            Element(
                index=0,
                label=label,
                cls=_short_class(node.get("class") or ""),
                resource_id=node.get("resource-id") or "",
                editable=(node.get("class") or "").endswith("EditText"),
                scrollable=node.get("scrollable") == "true",
                enabled=node.get("enabled") != "false",
                checkable=checkable,
                checked=checked,
                bounds=bounds,
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
        activity=activity,
        elements=elements,
        texts=loose_text[:15],
        scroll_region=max(scrollables, key=_area) if scrollables else None,
        width=width,
        height=height,
        note=note,
    )
