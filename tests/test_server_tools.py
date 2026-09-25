"""The MCP tools, driven against a fake phone."""
import asyncio

import pytest

from android_use import audit, server
from android_use.session import memory

from conftest import el, png, scr, write_config


def settings_screen():
    return scr("com.android.settings", el(0, "Wi-Fi"), el(1, "Bluetooth"), el(2, "Display"))


# -- reading ------------------------------------------------------------------

def test_get_screen_renders_and_remembers(phone):
    phone.current = settings_screen()
    out = server.get_screen()
    assert "[1] Bluetooth" in out
    assert memory.last("adb").element(1).label == "Bluetooth"


def test_claude_app_on_screen_gets_explained(phone):
    phone.current = scr("com.anthropic.claude", el(0, "Message Claude", editable=True))
    assert "the conversation you are in" in server.get_screen()


def test_sleeping_phone_is_woken(phone):
    phone.current = scr("com.android.systemui", el(0, "Clock"), screen_on=False)

    def wake(action):
        if action[0] == "wake":
            phone.current = settings_screen()
    phone.react = wake
    out = server.get_screen()
    assert ("wake",) in phone.actions
    assert "woken up" in out and "Bluetooth" in out


def test_locked_phone_says_who_must_unlock(phone):
    phone.current = scr("com.android.systemui", el(0, "Emergency call"), locked=True)
    out = server.get_screen()
    assert "Only its owner can enter" in out


# -- tapping by index -----------------------------------------------------------

def test_tap_acts_on_the_element_that_was_seen(phone):
    phone.current = settings_screen()
    server.get_screen()
    out = server.tap(1)
    assert phone.actions[-1] == ("tap", "Bluetooth", 1)
    assert "Tapped [1] 'Bluetooth'" in out


def test_tap_uses_the_new_index_when_rows_shift(phone):
    phone.current = settings_screen()
    server.get_screen()
    phone.current = scr("com.android.settings", el(0, "Update available"), el(1, "Wi-Fi"),
                        el(2, "Bluetooth"), el(3, "Display"))
    out = server.tap(1)
    # Index 1 on the screen the model saw was "Bluetooth"; it is now [2].
    assert ("tap", "Bluetooth", 2) in phone.actions
    assert "'Bluetooth' is now [2]" in out


def test_tap_refuses_when_the_page_changed(phone):
    phone.current = settings_screen()
    server.get_screen()
    phone.current = scr("com.example.other", el(0, "Name"), el(1, "Email"), el(2, "Phone"))
    out = server.tap(1)
    assert not any(a[0] == "tap" for a in phone.actions)
    assert "Did not act on [1] 'Bluetooth'" in out
    assert "[1] Email" in out  # the new screen is shown to pick again


def test_tap_without_a_read_shows_the_screen_first(phone):
    phone.current = settings_screen()
    out = server.tap(0)
    assert not phone.actions
    assert "Read the screen before acting by index" in out


def test_tap_unknown_index(phone):
    phone.current = settings_screen()
    server.get_screen()
    assert "no element [9]" in server.tap(9)


def test_risky_tap_is_held_until_confirmed(phone):
    phone.current = scr("com.example.chat", el(0, "Type a message", editable=True), el(1, "Send"))
    server.get_screen()
    held = server.tap(1)
    assert "Held back" in held and not phone.actions
    done = server.tap(1, confirm=True)
    assert phone.actions[-1] == ("tap", "Send", 1)
    assert "Tapped [1] 'Send'" in done


def test_risky_element_that_moved_is_never_tapped(phone):
    phone.current = scr("com.example.mail", el(0, "Inbox"), el(1, "Delete"))
    server.get_screen()
    phone.current = scr("com.example.mail", el(0, "Banner"), el(1, "Inbox"), el(2, "Delete"))
    out = server.tap(1, confirm=True)
    assert not any(a[0] == "tap" for a in phone.actions)
    assert "must still be exactly where you saw it" in out


def test_confirmation_can_be_switched_off(phone, isolated_config):
    write_config(isolated_config, {"confirm_risky_actions": False})
    phone.current = scr("com.example.chat", el(0, "Send"))
    server.get_screen()
    server.tap(0)
    assert phone.actions[-1][0] == "tap"


def test_network_switch_needs_confirmation(phone):
    phone.current = scr("com.android.settings", el(0, "Wi-Fi", checkable=True, checked=True))
    server.get_screen()
    assert "Held back" in server.tap(0)
    server.tap(0, confirm=True)
    assert phone.actions[-1][0] == "tap"


def test_long_press_and_double_tap(phone):
    phone.current = settings_screen()
    server.get_screen()
    assert "Long-pressed [0]" in server.tap(0, hold=True)
    assert phone.actions[-1] == ("long_press", "Wi-Fi")
    server.get_screen()
    server.tap(2, double=True)
    assert phone.actions[-1][0] == "double_tap"


def test_disabled_element(phone):
    phone.current = scr("p", el(0, "Next", enabled=False))
    server.get_screen()
    assert "disabled" in server.tap(0)
    assert not phone.actions


def test_no_change_is_reported_and_then_called_stuck(phone):
    phone.current = settings_screen()
    server.get_screen()
    first = server.tap(0)
    assert "did not change" in first
    server.tap(0)
    third = server.tap(0)
    assert "Nothing has changed for several actions" in third


def test_change_is_not_reported_as_no_change(phone):
    phone.current = settings_screen()
    server.get_screen()

    def open_wifi(action):
        if action[:2] == ("tap", "Wi-Fi"):
            phone.current = scr("com.android.settings", el(0, "Use Wi-Fi", checkable=True))
    phone.react = open_wifi
    out = server.tap(0)
    assert "did not change" not in out and "Use Wi-Fi" in out


# -- other ways to tap ------------------------------------------------------------

def test_tap_text(phone):
    phone.current = settings_screen()
    server.tap_text("display")
    assert phone.actions[-1] == ("tap", "Display", 2)


def test_tap_text_ambiguous(phone):
    phone.current = scr("p", el(0, "Delete photo"), el(1, "Delete album"))
    out = server.tap_text("delete")
    assert "matches 2 elements" in out and not phone.actions


def test_tap_coordinates_guards_what_is_underneath(phone):
    phone.current = scr("p", el(0, "Pay now", bounds=(0, 1000, 1080, 1200)))
    assert "Held back" in server.tap_coordinates(500, 1100)
    server.tap_coordinates(500, 1100, confirm=True)
    assert phone.actions[-1] == ("tap_xy", 500, 1100)
    assert "outside the screen" in server.tap_coordinates(5000, 10)


# -- sensitive apps ----------------------------------------------------------------

def test_banking_app_is_not_read(phone):
    phone.current = scr("net.one97.paytm", el(0, "Balance: 10,000"), el(1, "Send money"))
    out = server.get_screen()
    assert "banking or payment app" in out
    assert "10,000" not in out


def test_banking_app_is_not_operated(phone):
    phone.current = scr("net.one97.paytm", el(0, "Scan QR"))
    server.get_screen()
    out = server.tap(0)
    assert not phone.actions and "banking or payment app" in out
    assert "banking or payment app" in server.tap_text("Scan QR")


def test_allowlisted_banking_app_can_be_used(phone, isolated_config):
    write_config(isolated_config, {"allowed_packages": ["net.one97.paytm"]})
    phone.current = scr("net.one97.paytm", el(0, "Scan QR"))
    assert "Scan QR" in server.get_screen()


def test_home_key_still_works_in_a_banking_app(phone):
    phone.current = scr("net.one97.paytm", el(0, "Scan QR"))
    server.press_key("home")
    assert phone.actions[-1] == ("key", "home")


def test_screenshot_of_banking_app_is_refused(phone):
    phone.current = scr("com.phonepe.app", el(0, "Pay"))
    with pytest.raises(ValueError, match="banking or payment"):
        server.take_screenshot()


# -- typing -------------------------------------------------------------------------

def test_type_goes_to_the_focused_field(phone):
    phone.current = scr("p", el(0, "", editable=True, hint="Name"),
                        el(1, "", editable=True, hint="Email", focused=True))
    out = server.type_text("a@b.c", submit=True)
    assert phone.actions[-1] == ("type_into", "", "a@b.c", False, True)
    assert "pressed enter" in out


def test_type_asks_which_field_when_unclear(phone):
    phone.current = scr("p", el(0, "", editable=True, hint="Name"),
                        el(1, "", editable=True, hint="Email"))
    out = server.type_text("x")
    assert "Several text fields" in out and not phone.actions


def test_type_into_a_named_field(phone):
    phone.current = scr("p", el(0, "", editable=True, hint="Name"),
                        el(1, "old", editable=True, hint="Email"))
    server.get_screen()
    server.type_text("new", index=1, clear=True)
    assert phone.actions[-1] == ("type_into", "old", "new", True, False)


def test_type_into_something_that_is_not_a_field(phone):
    phone.current = scr("p", el(0, "Button"))
    server.get_screen()
    assert "not a text field" in server.type_text("x", index=0)


def test_passwords_are_not_echoed(phone):
    phone.current = scr("p", el(0, "", editable=True, password=True, focused=True))
    out = server.type_text("hunter2")
    assert "hunter2" not in out and "7 characters" in out
    assert "hunter2" not in audit.LOG_PATH.read_text()


def test_unicode_refused_over_adb(phone):
    phone.unicode = False
    phone.current = scr("p", el(0, "", editable=True, focused=True))
    out = server.type_text("నమస్తే")
    assert "plain English" in out and not phone.actions


# -- scrolling -----------------------------------------------------------------------

def test_scroll_reports_the_end_of_the_list(phone):
    phone.current = settings_screen()
    server.get_screen()
    out = server.scroll("down")
    assert phone.actions[-1] == ("scroll", "down", None)
    assert "end of this list" in out


def test_scroll_inside_a_list(phone):
    phone.current = settings_screen()
    server.get_screen()
    server.scroll("right", index=2)
    assert phone.actions[-1] == ("scroll", "right", 2)


def test_scroll_to_finds_an_item_further_down(phone):
    pages = [settings_screen(),
             scr("com.android.settings", el(0, "Sound"), el(1, "Storage")),
             scr("com.android.settings", el(0, "Accessibility"), el(1, "About phone"))]

    def next_page(action):
        if action[0] == "scroll":
            phone.current = pages[min(len(pages) - 1, 1 + sum(a[0] == "scroll" for a in phone.actions) - 1)]
    phone.current = pages[0]
    phone.react = next_page
    out = server.scroll_to("about phone")
    assert "Found 'about phone' [1] 'About phone' after 2 scroll(s)" in out


def test_scroll_to_stops_at_the_end(phone):
    phone.current = settings_screen()
    out = server.scroll_to("Zebra")
    assert "Reached the end of the list without finding 'Zebra'" in out


def test_bad_direction(phone):
    phone.current = settings_screen()
    assert "direction must be" in server.scroll("sideways")


# -- swipe, drag, pinch, keys ---------------------------------------------------------

def test_swipe_directions_and_coordinates(phone):
    phone.current = settings_screen()
    server.swipe("left")
    kind, x1, y1, x2, y2 = phone.actions[-1]
    assert kind == "swipe" and x1 > x2 and y1 == y2
    server.swipe(x1=10, y1=20, x2=30, y2=40)
    assert phone.actions[-1] == ("swipe", 10, 20, 30, 40)


def test_call_key_needs_confirmation(phone):
    phone.current = settings_screen()
    assert "Held back" in server.press_key("call")
    server.press_key("call", confirm=True)
    assert phone.actions[-1] == ("key", "call")


def test_unknown_key_lists_the_known_ones(phone):
    phone.current = settings_screen()
    assert "Known keys" in server.press_key("banana")


# -- apps, links and settings -----------------------------------------------------------

def test_open_app_waits_for_it(phone):
    phone.current = settings_screen()

    def launched(action):
        if action[0] == "launch":
            phone.current = scr(action[1], el(0, "Search"))
    phone.react = launched
    out = server.open_app("youtube")
    assert phone.actions[-1] == ("launch", "com.google.android.youtube")
    assert "com.google.android.youtube" in out


def test_open_app_respects_the_allowlist(phone):
    phone.current = settings_screen()
    out = server.open_app("whatsapp")
    assert "not on the allowlist" in out and not phone.actions


def test_open_claude_is_allowed(phone):
    phone.current = settings_screen()
    server.open_app("claude")
    assert ("launch", "com.anthropic.claude") in phone.actions


def test_open_url_blocks_dangerous_schemes(phone):
    phone.current = settings_screen()
    assert "refused" in server.open_url("upi://pay?pa=a@b")
    assert not phone.actions


def test_open_url_refuses_payment_apps(phone):
    phone.current = settings_screen()
    phone.links["https://p.paytm.me/x"] = "net.one97.paytm"
    assert "banking or payment" in server.open_url("https://p.paytm.me/x")
    assert not phone.actions


def test_open_url_sends_unallowed_apps_to_the_browser(phone):
    phone.current = settings_screen()
    phone.links["https://instagram.com/x"] = "com.instagram.android"
    out = server.open_url("https://instagram.com/x")
    assert phone.actions[-1] == ("open_url", "https://instagram.com/x", "web", "com.android.chrome")
    assert "in the browser" in out


def test_open_url_dial_never_calls(phone):
    phone.current = settings_screen()
    phone.links["tel:123"] = "com.google.android.dialer"
    out = server.open_url("tel:123")
    assert phone.actions[-1] == ("open_url", "tel:123", "dial", "")
    assert "nothing is dialled" in out


def test_device_status_explains_the_problem(phone):
    phone.current = settings_screen()
    phone.state = {
        "battery": {"level": 9, "charging": False},
        "sound": {"ringer": "silent", "dnd": "off", "volume": {"ring": 50, "media": 0}},
        "network": {"active": "wifi", "internet": True, "wifi": True, "airplane": False},
        "storage": {"free_gb": 0.4, "total_gb": 64},
    }
    out = server.device_status()
    assert "ringer is on SILENT" in out
    assert "Battery is low (9%)" in out
    assert "Media volume is at 0" in out
    assert "Storage is almost full" in out


@pytest.mark.parametrize("setting,value,name,parsed", [
    ("ring volume", "80%", "volume_ring", 80),
    ("volume", 40, "volume_media", 40),
    ("text size", "large", "font_size", 1.15),
    ("font_size", "1.3x", "font_size", 1.3),
    ("brightness", "auto", "brightness", "auto"),
    ("timeout", "2m", "screen_timeout", 120),
    ("dnd", True, "do_not_disturb", True),
    ("torch", "off", "flashlight", False),
    ("ringer", "vibrate", "ringer", "vibrate"),
])
def test_parse_setting(setting, value, name, parsed):
    assert server.parse_setting(setting, value) == (name, parsed, "")


@pytest.mark.parametrize("setting,value", [
    ("volume_ring", "150"), ("brightness", "dim"), ("font_size", "9"),
    ("screen_timeout", "2s"), ("wifi", "maybe"), ("warp_drive", "on"),
])
def test_parse_setting_rejects(setting, value):
    assert server.parse_setting(setting, value)[2]


def test_change_setting_applies(phone):
    phone.current = settings_screen()
    phone.setting_result = ("Ring volume set to 80%", False)
    out = server.change_setting("volume_ring", "80")
    assert phone.actions[-1] == ("setting", "volume_ring", 80)
    assert "Ring volume set to 80%" in out


def test_change_setting_that_could_lock_you_out(phone):
    phone.current = settings_screen()
    phone.state = {"network": {"active": "cellular", "mobile_data": True}}
    out = server.change_setting("mobile_data", "off")
    assert "Held back" in out and not phone.actions
    server.change_setting("mobile_data", "off", confirm=True)
    assert phone.actions[-1] == ("setting", "mobile_data", False)


def test_change_setting_that_opened_a_panel_shows_it(phone):
    phone.current = settings_screen()
    phone.state = {"network": {"active": "cellular", "mobile_data": True}}
    phone.setting_result = ("Opened the Wi-Fi panel - tap the switch.", True)
    out = server.change_setting("wifi", "on")
    assert "Opened the Wi-Fi panel" in out and "[0] Wi-Fi" in out


# -- the owner ----------------------------------------------------------------------------

def test_say_and_ask(phone):
    phone.current = settings_screen()
    assert "Shown on the phone and read aloud" in server.say_to_owner("Hello", speak=True)
    assert phone.actions[-1] == ("say", "Hello", True)
    assert "answered: Yes" in server.ask_owner("Is this your Wi-Fi?", ["Yes", "No"])
    phone.answer = None
    assert "No answer within" in server.ask_owner("Still there?")


def test_request_control_only_for_the_app(phone):
    assert "Android Use app" in server.request_control()


# -- steps and waiting ----------------------------------------------------------------------

def test_run_steps_validates_before_doing_anything(phone):
    phone.current = settings_screen()
    out = server.run_steps([{"tap_text": "Wi-Fi"}, {"tap": 2}])
    assert "only allowed in the first step" in out and not phone.actions
    assert "exactly one of" in server.run_steps([{"tap_text": "a", "key": "back"}])
    assert "At most 15" in server.run_steps([{"key": "back"}] * 16)


def test_run_steps_runs_in_order_and_stops_when_held(phone):
    phone.current = scr("com.example.chat", el(0, "Search", editable=True, focused=True),
                        el(1, "Send"))
    out = server.run_steps([
        {"type": "hello"},
        {"key": "back"},
        {"tap_text": "Send"},
        {"key": "home"},
    ])
    kinds = [a[0] for a in phone.actions]
    assert kinds == ["type_into", "key"]
    assert "3. STOPPED: Held back" in out


def test_wait_for_text(phone):
    phone.current = settings_screen()
    assert "'bluetooth' appeared" in server.wait_for("bluetooth")
    assert "has not appeared" in server.wait_for("Zebra", timeout=2)
    assert "'Zebra' disappeared" in server.wait_for("Zebra", gone=True)


def test_wait_for_settle(phone):
    phone.current = settings_screen()
    assert "settled" in server.wait_for()


# -- screenshots ----------------------------------------------------------------------------

def test_annotated_screenshot_returns_image_and_listing(phone):
    phone.current = settings_screen()
    result = server.take_screenshot(annotate=True)
    assert len(result) == 2
    assert "[0] Wi-Fi" in result[1]
    assert memory.last("adb") is not None


def test_zoomed_screenshot(phone):
    phone.current = settings_screen()
    result = server.take_screenshot(index=1)
    assert "Close-up of [1] 'Bluetooth'" in result[1]


def test_black_screenshot_is_explained(phone):
    phone.current = settings_screen()
    phone.shot = png(colour=(0, 0, 0))
    result = server.take_screenshot()
    assert "blocks screenshots" in result[-1]


# -- logging ----------------------------------------------------------------------------------

def test_actions_are_logged(phone):
    phone.current = settings_screen()
    server.get_screen()
    server.tap(1)
    log = server.activity_log()
    assert "tapped - [1] Bluetooth" in log


def test_tool_listing_has_no_duplicate_structured_output():
    tools = asyncio.run(server.mcp.list_tools())
    assert len(tools) >= 50
    for tool in tools:
        dumped = tool.model_dump(by_alias=True, exclude_none=True)
        assert "outputSchema" not in dumped, tool.name
        assert dumped.get("annotations"), tool.name
