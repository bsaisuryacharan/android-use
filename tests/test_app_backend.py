"""The phone-app transport, against a fake bridge speaking the same HTTP.

These pin down the wire protocol the Kotlin app implements: paths, request
bodies, and how errors and timeouts are interpreted.
"""
from __future__ import annotations

import json
import threading
import time as real_time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from android_use import app_backend
from android_use.app_backend import AppBackend
from android_use.backend import BackendError, UnsupportedError

from conftest import el


class Bridge:
    """A stand-in for HttpBridge.kt. `routes[path]` is (status, body, delay)."""

    def __init__(self) -> None:
        self.routes: dict[str, tuple[int, object, float]] = {}
        self.calls: list[tuple[str, str, dict]] = []
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw) if raw else {}
                path = self.path.split("?", 1)[0]
                bridge.calls.append((self.command, self.path, body))
                status, payload, delay = bridge.routes.get(
                    path, (404, {"error": f"Unknown route: {path}"}, 0))
                if callable(payload):
                    status, payload = payload(body, len(bridge.calls))
                if delay:
                    threading.Event().wait(delay)
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    pass

            do_GET = do_POST = _serve

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02},
                         daemon=True).start()

    def paths(self, method: str | None = None) -> list[str]:
        return [p.split("?", 1)[0] for m, p, _ in self.calls if method in (None, m)]

    def close(self) -> None:
        self.server.shutdown()


class NoSleep:
    @staticmethod
    def sleep(_seconds):
        return None

    @staticmethod
    def time():
        return real_time.time()


@pytest.fixture
def bridge(monkeypatch):
    monkeypatch.setattr(app_backend, "time", NoSleep)
    b = Bridge()
    b.routes["/status"] = (200, {"service_running": True, "granted": True, "protocol": 2,
                                 "manufacturer": "Google", "model": "Pixel"}, 0)
    yield b
    b.close()


def client(bridge: Bridge, protocol: int = 2, timeout: float = 2) -> AppBackend:
    backend = AppBackend("127.0.0.1", bridge.port, "tok", timeout=timeout)
    backend.protocol = protocol
    return backend


SCREEN = {
    "package": "com.android.settings", "activity": "", "app_label": "Settings",
    "width": 1080, "height": 2400, "protocol": 2, "screen_on": True, "locked": False,
    "keyboard_open": True, "toasts": ["Saved"], "other_window": "com.anthropic.claude",
    "texts": ["Network & internet"], "scroll_region": [0, 200, 1080, 2300],
    "elements": [
        {"index": 0, "label": "Wi-Fi", "cls": "LinearLayout", "resource_id": "", "editable": False,
         "scrollable": False, "enabled": True, "checkable": True, "checked": True,
         "bounds": [0, 200, 1080, 350], "focused": False, "selected": False,
         "password": False, "hint": "", "long_clickable": False, "container": [0, 200, 1080, 2300]},
        {"index": 1, "label": "", "cls": "EditText", "resource_id": "a:id/q", "editable": True,
         "scrollable": False, "enabled": True, "checkable": False, "checked": False,
         "bounds": [0, 400, 1080, 500], "focused": True, "hint": "Search settings"},
    ],
}


def test_screen_parses_every_field_and_asks_for_idle(bridge):
    bridge.routes["/screen"] = (200, SCREEN, 0)
    screen = client(bridge).get_screen(wait_idle=True, max_texts=200)
    method, path, _ = bridge.calls[-1]
    assert path.startswith("/screen?") and "idle=1" in path and "texts=200" in path
    wifi, field = screen.elements
    assert wifi.checked and wifi.container == (0, 200, 1080, 2300) and wifi.node_ref == "0"
    assert field.focused and field.hint == "Search settings"
    assert screen.keyboard_open and screen.toasts == ["Saved"]
    assert screen.other_window == "com.anthropic.claude" and screen.app_label == "Settings"
    assert screen.scroll_region == (0, 200, 1080, 2300)


def test_old_apps_screens_still_parse(bridge):
    old = {k: v for k, v in SCREEN.items() if k in ("package", "elements", "width", "height")}
    old["elements"] = [{"index": 0, "label": "Wi-Fi", "bounds": [0, 0, 10, 10]}]
    bridge.routes["/screen"] = (200, old, 0)
    backend = client(bridge, protocol=2)
    screen = backend.get_screen()
    assert backend.protocol == 1          # no "protocol" key means version 1
    assert screen.elements[0].label == "Wi-Fi" and screen.screen_on is None


def test_type_sends_clear_and_submit(bridge):
    bridge.routes["/text"] = (200, {"ok": True, "submitted": True}, 0)
    note = client(bridge).type_into(el(3, "old", editable=True), "new", clear=True, submit=True)
    assert bridge.calls[-1][2] == {"index": 3, "text": "new", "clear": True, "submit": True}
    assert note == ""


def test_type_on_an_old_app_appends_by_hand(bridge):
    bridge.routes["/text"] = (200, {"ok": True}, 0)
    note = client(bridge, protocol=1).type_into(el(3, "Hello", editable=True), " world",
                                                submit=True)
    assert bridge.calls[-1][2] == {"index": 3, "text": "Hello world"}
    assert "cannot press enter" in note


def test_type_reports_a_field_without_enter(bridge):
    bridge.routes["/text"] = (200, {"ok": True, "submitted": False}, 0)
    note = client(bridge).type_into(el(3, "", editable=True), "x", submit=True)
    assert "no enter action" in note


def test_unknown_route_means_update_the_app(bridge):
    with pytest.raises(UnsupportedError, match="too old"):
        client(bridge).device_state()


def test_features_are_not_asked_of_an_old_app(bridge):
    with pytest.raises(UnsupportedError, match="newer version"):
        client(bridge, protocol=1).open_url("https://example.com")
    assert "/open_url" not in bridge.paths()


def test_refusals_are_reported_as_they_are(bridge):
    bridge.routes["/tap"] = (403, {"error": "That is a banking app.", "sensitive": True}, 0)
    with pytest.raises(BackendError) as exc:
        client(bridge).tap_xy(1, 2)
    assert "banking app" in str(exc.value) and "request_control" not in str(exc.value)

    bridge.routes["/tap"] = (403, {"error": "Control is not currently granted."}, 0)
    with pytest.raises(BackendError, match="request_control"):
        client(bridge).tap_xy(1, 2)


def test_a_slow_action_is_never_repeated(bridge):
    bridge.routes["/swipe"] = (200, {"ok": True}, 1.5)
    with pytest.raises(BackendError, match="may or may not have happened"):
        client(bridge, timeout=0.5).swipe(1, 2, 3, 4)
    real_time.sleep(1.2)  # let the slow handler finish before counting
    assert bridge.paths("POST").count("/swipe") == 1


def test_taps_wait_long_enough_for_the_owner(bridge, monkeypatch):
    # The phone may hold a risky tap while its owner decides; a short reply
    # timeout would report a tap the owner then allows as a failure.
    monkeypatch.setattr(app_backend, "OWNER_CONFIRM_TIMEOUT", 3)
    bridge.routes["/tap"] = (200, {"ok": True}, 1.0)
    client(bridge, timeout=0.5).tap_xy(5, 5)
    assert bridge.paths("POST").count("/tap") == 1


def test_a_slow_read_is_retried_once(bridge):
    calls = {"n": 0}

    def maybe_slow(body, n):
        calls["n"] += 1
        if calls["n"] == 1:
            threading.Event().wait(1.0)
        return 200, SCREEN
    bridge.routes["/screen"] = (200, maybe_slow, 0)
    screen = client(bridge, timeout=0.5).get_screen()
    assert screen.package == "com.android.settings"
    assert bridge.paths("GET").count("/screen") == 2


def test_revoked_service_is_repaired_then_retried(bridge):
    state = {"repaired": False}

    def screen(body, n):
        if not state["repaired"]:
            return 503, {"error": "The accessibility service is not running."}
        return 200, SCREEN

    def repair(body, n):
        state["repaired"] = True
        return 200, {"ok": True, "service_running": True}

    bridge.routes["/screen"] = (200, screen, 0)
    bridge.routes["/repair"] = (200, repair, 0)
    assert client(bridge).get_screen().package == "com.android.settings"
    assert bridge.paths() == ["/screen", "/repair", "/screen"]


def test_readiness_is_cached_briefly(bridge):
    backend = client(bridge)
    backend.require_ready()
    backend.require_ready()
    assert bridge.paths().count("/status") == 1


def test_not_granted_suggests_request_control(bridge):
    bridge.routes["/status"] = (200, {"service_running": True, "granted": False, "protocol": 2}, 0)
    status = client(bridge).status()
    assert not status.connected and "request_control" in status.detail


def test_keys_depend_on_the_app_version(bridge):
    bridge.routes["/key"] = (200, {"ok": True}, 0)
    with pytest.raises(BackendError, match="update the phone app"):
        client(bridge, protocol=1).press("enter")
    client(bridge).press("enter")
    assert bridge.calls[-1][2] == {"key": "enter"}


def test_scroll_prefers_accessibility_then_falls_back_to_a_swipe(bridge):
    bridge.routes["/scroll"] = (200, {"ok": False}, 0)
    bridge.routes["/swipe"] = (200, {"ok": True}, 0)
    backend = client(bridge)
    backend._size = (1080, 2400)
    backend.scroll("down", element=el(4, "Row", container=(0, 400, 1080, 2000)))
    assert bridge.calls[-2][1] == "/scroll" and bridge.calls[-2][2] == {"direction": "down", "index": 4}
    assert bridge.calls[-1][1] == "/swipe"
    swipe = bridge.calls[-1][2]
    assert swipe["y1"] > swipe["y2"] and 400 <= swipe["y2"] <= 2000


def test_settings_that_open_a_panel(bridge):
    bridge.routes["/set_setting"] = (200, {"ok": False, "opened": "wifi_panel",
                                           "message": "Opened the Wi-Fi panel."}, 0)
    assert client(bridge).change_setting("wifi", True) == ("Opened the Wi-Fi panel.", True)
    bridge.routes["/set_setting"] = (200, {"ok": False, "message": "Not allowed."}, 0)
    with pytest.raises(BackendError, match="Not allowed"):
        client(bridge).change_setting("brightness", 50)


def test_wake_reports_a_secure_lock(bridge):
    bridge.routes["/wake"] = (200, {"ok": True, "screen_on": True, "locked": True, "secure": True}, 0)
    assert client(bridge).wake_and_unlock().startswith("locked: the phone needs a PIN")


def test_ask_and_request_control(bridge):
    bridge.routes["/ask"] = (200, {"answer": "Yes"}, 0)
    assert client(bridge).ask("OK?", ["Yes", "No"], timeout_s=10) == "Yes"
    assert bridge.calls[-1][2]["options"] == ["Yes", "No"]
    bridge.routes["/request_grant"] = (200, {"ok": True, "pending": True}, 0)
    assert client(bridge).request_control(60, "Fix Wi-Fi") == "asked"


def test_links_and_screenshots(bridge):
    bridge.routes["/resolve_url"] = (200, {"package": "com.android.chrome"}, 0)
    bridge.routes["/open_url"] = (200, {"ok": True, "package": "com.android.chrome"}, 0)
    bridge.routes["/screenshot"] = (200, b"\xff\xd8jpeg", 0)
    backend = client(bridge)
    assert backend.resolve_url("https://x.org") == "com.android.chrome"
    assert backend.open_url("https://x.org", package="com.android.chrome") == "com.android.chrome"
    assert bridge.calls[-1][2]["package"] == "com.android.chrome"
    assert backend.screenshot(max_width=1080) == b"\xff\xd8jpeg"
    assert bridge.calls[-1][1] == "/screenshot?max_width=1080"
