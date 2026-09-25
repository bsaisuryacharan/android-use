"""Shared fixtures: an isolated config directory and a scriptable fake phone."""
from __future__ import annotations

import copy
import io
import json
from typing import Callable

import pytest

from android_use import adb_backend, apps, audit, devices, http_server, server, session, watchdog
from android_use import auth
from android_use.backend import Backend, BackendError
from android_use.models import DeviceStatus, Element, Screen


class FakeClock:
    """Stands in for the time module inside server.py: sleeping advances the
    clock instantly, so timeouts and polling loops run in no real time."""

    def __init__(self) -> None:
        self.now = 1_000_000.0

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, float(seconds))


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Every test gets its own empty ~/.android-use, and no memory of the last."""
    cfg = tmp_path / "config.json"
    cfg.write_text("{}")
    for module in (apps, devices, http_server):
        monkeypatch.setattr(module, "CONFIG_PATH", cfg)
    monkeypatch.setattr(audit, "LOG_PATH", tmp_path / "audit.jsonl")
    monkeypatch.setattr(watchdog, "STATE_PATH", tmp_path / "watchdog.json")
    monkeypatch.setattr(auth, "ACCOUNTS_PATH", tmp_path / "accounts.json")
    session.memory.forget()
    server._last_wake.clear()
    adb_backend.reset_backend()
    # Keep tests fast and deterministic: no real settling delays.
    monkeypatch.setattr(server, "time", FakeClock())
    yield cfg


def write_config(cfg_path, data: dict) -> None:
    cfg_path.write_text(json.dumps(data))


def el(index: int, label: str = "", *, bounds=None, cls: str = "Button", **kw) -> Element:
    if bounds is None:
        top = 200 + index * 150
        bounds = (0, top, 1080, top + 140)
    return Element(
        index=index, label=label, cls=cls, resource_id=kw.pop("resource_id", ""),
        editable=kw.pop("editable", False), scrollable=kw.pop("scrollable", False),
        enabled=kw.pop("enabled", True), checkable=kw.pop("checkable", False),
        checked=kw.pop("checked", False), bounds=bounds, node_ref=str(index), **kw,
    )


def scr(package: str = "com.example.app", *elements: Element, **kw) -> Screen:
    kw.setdefault("width", 1080)
    kw.setdefault("height", 2400)
    return Screen(package=package, activity="", elements=list(elements), **kw)


def png(width: int = 1080, height: int = 2400, colour=(40, 120, 200)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buf, format="PNG")
    return buf.getvalue()


class FakePhone(Backend):
    """A phone whose screen tests control directly.

    `current` is what get_screen returns. Every action is appended to
    `actions` as a tuple, and `react(action_tuple)`, if set, may change
    `current` - that is how a test says "tapping this opens that".
    """

    def __init__(self, current: Screen, name: str = "app", unicode: bool = True):
        self.name = name
        self.current = current
        self.actions: list[tuple] = []
        self.react: Callable[[tuple], None] | None = None
        self.unicode = unicode
        self.packages = ["com.android.settings", "com.google.android.youtube",
                         "com.anthropic.claude", "com.whatsapp", "net.one97.paytm"]
        self.links: dict[str, str] = {}
        self.state: dict = {}
        self.settable_list: list[str] = ["volume_ring", "brightness"]
        self.setting_result: tuple[str, bool] = ("done", False)
        self.shot = png()
        self.answer: str | None = "Yes"
        self.transport = "app"

    def _do(self, *action) -> None:
        self.actions.append(action)
        if self.react:
            self.react(action)

    # connection
    def status(self) -> DeviceStatus:
        return DeviceStatus(connected=True, serial="fake", model="Fake", transport=self.transport)

    def require_ready(self) -> DeviceStatus:
        return self.status()

    # perception
    def get_screen(self, wait_idle: bool = False, max_texts: int = 15) -> Screen:
        return copy.deepcopy(self.current)

    def screenshot(self, max_width: int = 0) -> bytes:
        return self.shot

    def screen_size(self):
        return self.current.width, self.current.height

    # actions
    def tap_element(self, element: Element) -> None:
        self._do("tap", element.label, element.index)

    def tap_xy(self, x, y) -> None:
        self._do("tap_xy", x, y)

    def swipe(self, x1, y1, x2, y2, duration_ms=300) -> None:
        self._do("swipe", x1, y1, x2, y2)

    def long_press_element(self, element: Element) -> None:
        self._do("long_press", element.label)

    def double_tap_xy(self, x, y) -> None:
        self._do("double_tap", x, y)

    def scroll(self, direction, amount=0.6, element=None) -> None:
        self._do("scroll", direction, element.index if element else None)

    def type_text(self, text: str) -> None:
        self._do("type", text)

    def type_into(self, element, text, clear=False, submit=False) -> str:
        self._do("type_into", element.label if element else None, text, clear, submit)
        return ""

    def supports_unicode_text(self) -> bool:
        return self.unicode

    def press(self, key: str) -> None:
        if key not in ("back", "home", "enter", "call", "recents"):
            raise BackendError(f"Unknown key {key!r}")
        self._do("key", key)

    def known_keys(self):
        return ["back", "home", "enter", "recents"]

    def wake_and_unlock(self) -> str:
        self._do("wake")
        return "awake and unlocked"

    # apps
    def installed_packages(self):
        return list(self.packages)

    def launch_package(self, package: str) -> None:
        self._do("launch", package)

    def open_settings_page(self, page: str) -> str:
        if page not in ("wifi", "display"):
            raise BackendError(f"Unknown settings page {page!r}.")
        self._do("settings", page)
        return page

    def settings_pages(self):
        return ["display", "wifi"]

    def resolve_url(self, url: str, kind: str = "web") -> str:
        return self.links.get(url, "com.android.chrome")

    def open_url(self, url: str, kind: str = "web", package: str = "") -> str:
        self._do("open_url", url, kind, package)
        return package

    def device_state(self) -> dict:
        return copy.deepcopy(self.state)

    def change_setting(self, name, value):
        self._do("setting", name, value)
        return self.setting_result

    def settable(self):
        return list(self.settable_list)

    def say(self, text, speak=False) -> None:
        self._do("say", text, speak)

    def ask(self, question, options, timeout_s=60, speak=False):
        self._do("ask", question, tuple(options))
        return self.answer

    def connectivity_report(self) -> str:
        return "fine"

    def device_info(self) -> str:
        return "Fake phone"


@pytest.fixture
def phone(monkeypatch):
    """A FakePhone wired in as the only phone the server can see."""
    fake = FakePhone(scr())
    monkeypatch.setattr(server, "backend_for", lambda device="": (fake, ""))
    return fake
