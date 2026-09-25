"""Screen model helpers and the stale-index matcher."""
from android_use.models import Screen
from android_use.session import iou, locate, memory

from conftest import el, scr


def test_display_label_falls_back_to_hint_then_id():
    assert el(0, "", resource_id="com.x:id/send_btn").display_label == "(no label, id: send_btn)"
    assert el(0, "", editable=True, hint="Message").display_label == '(empty field: "Message")'
    assert el(0, "").display_label == "(no label)"


def test_render_flags():
    field = el(2, "hunter2", editable=True, password=True, focused=True)
    text = field.render()
    assert "password field" in text and "focused" in text


def test_find_prefers_exact_then_partial_then_ids():
    screen = scr("p", el(0, "Wi-Fi"), el(1, "Wi-Fi calling"), el(2, "", resource_id="a:id/search"))
    assert [e.index for e in screen.find("wi-fi")] == [0]
    assert [e.index for e in screen.find("calling")] == [1]
    assert [e.index for e in screen.find("search")] == [2]


def test_signature_notices_movement_and_state():
    a = scr("p", el(0, "Row"))
    b = scr("p", el(0, "Row", bounds=(0, 300, 1080, 440)))
    c = scr("p", el(0, "Row", checkable=True, checked=True))
    assert a.signature() != b.signature()
    assert a.signature() != c.signature()
    assert a.signature() == scr("p", el(0, "Row")).signature()


def test_contains_text_looks_everywhere():
    screen = scr("p", el(0, "Save"), texts=["Loading..."], toasts=["Saved"])
    assert screen.contains_text("loading")
    assert screen.contains_text("saved")
    assert not screen.contains_text("missing")


def test_iou():
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0


def test_locate_same_place():
    before = scr("p", el(0, "Wi-Fi"), el(1, "Bluetooth"))
    found, how = locate(before.elements[1], before, before)
    assert found.index == 1 and how == "same"


def test_locate_follows_a_unique_element_that_moved():
    before = scr("p", el(0, "Wi-Fi"), el(1, "Bluetooth"), el(2, "Display"))
    # A banner pushed everything down one slot.
    now = scr("p", el(0, "Update available"), el(1, "Wi-Fi"), el(2, "Bluetooth"), el(3, "Display"))
    found, how = locate(before.elements[1], before, now)
    assert found.label == "Bluetooth" and found.index == 2 and how == "moved"


def test_locate_refuses_duplicates_that_moved():
    before = scr("p", el(0, "Delete"), el(1, "Delete"))
    now = scr("p", el(0, "New"), el(1, "Delete"), el(2, "Delete"))
    found, how = locate(before.elements[0], before, now)
    assert found is None and how == "ambiguous"


def test_locate_strict_refuses_any_movement():
    before = scr("p", el(0, "Wi-Fi"), el(1, "Send"))
    now = scr("p", el(0, "Banner"), el(1, "Wi-Fi"), el(2, "Send"))
    found, how = locate(before.elements[1], before, now, strict=True)
    assert found is None and how == "moved"


def test_locate_refuses_on_a_different_page():
    before = scr("p", el(0, "Wi-Fi"), el(1, "Bluetooth"), el(2, "OK"))
    now = scr("q", el(0, "Name"), el(1, "Email"), el(2, "Phone"), el(3, "OK", bounds=(0, 900, 1080, 1000)))
    found, how = locate(before.elements[2], before, now)
    assert found is None and how == "context"


def test_locate_matches_a_text_field_whose_contents_changed():
    before = scr("p", el(0, "", editable=True, resource_id="a:id/q"))
    now = scr("p", el(0, "lo-fi", editable=True, resource_id="a:id/q"))
    found, how = locate(before.elements[0], before, now)
    assert found is not None and how == "same"


def test_locate_gone():
    before = scr("p", el(0, "Wi-Fi"))
    found, how = locate(before.elements[0], before, scr("p", el(0, "Other")))
    assert found is None and how == "gone"


def test_memory_streaks():
    memory.forget()
    assert memory.note_outcome("k", changed=False) == 1
    assert memory.note_outcome("k", changed=False) == 2
    assert memory.note_outcome("k", changed=True) == 0
    memory.remember("k", Screen(package="p", activity=""))
    assert memory.last("k").package == "p"
    memory.forget("k")
    assert memory.last("k") is None
