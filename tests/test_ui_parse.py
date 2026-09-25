"""The uiautomator parser: one entry per row a person would recognise."""
from pathlib import Path

from android_use import ui

XML = (Path(__file__).parent / "fixtures" / "clock_alarms.xml").read_text()


def parse(**kw):
    return ui.parse(XML, 1080, 2400, **kw)


def test_labels_fold_into_the_clickable_row():
    screen = parse()
    labels = [e.label for e in screen.elements]
    assert "7:00 AM / gym" in labels
    assert "10:00 AM" in labels
    assert screen.package == "com.android.deskclock"


def test_switch_state_is_carried_by_the_row():
    screen = parse()
    gym = next(e for e in screen.elements if e.label == "7:00 AM / gym")
    ten = next(e for e in screen.elements if e.label == "10:00 AM")
    assert gym.checkable and gym.checked
    assert ten.checkable and not ten.checked
    assert "[ON]" in gym.render() or "ON" in gym.render()


def test_focus_selection_and_hint_are_parsed():
    screen = parse()
    field = next(e for e in screen.elements if e.editable)
    assert field.focused
    assert field.label == ""                      # empty, not its placeholder
    assert field.hint == "Search alarms"
    assert 'empty field: "Search alarms"' in field.render()
    tab = next(e for e in screen.elements if e.label == "Alarm")
    assert tab.selected


def test_rows_know_their_scrolling_list():
    screen = parse()
    gym = next(e for e in screen.elements if e.label == "7:00 AM / gym")
    assert gym.container == (0, 440, 1080, 2200)
    assert gym.long_clickable
    tab = next(e for e in screen.elements if e.label == "Alarm")
    assert tab.container is None
    assert screen.scroll_region == (0, 440, 1080, 2200)


def test_stacked_blank_twin_is_dropped():
    screen = parse()
    at_fab = [e for e in screen.elements if e.center == (540, 2330)]
    assert len(at_fab) == 1 and at_fab[0].label == "Add alarm"


def test_offscreen_nodes_are_ignored_and_loose_text_kept():
    screen = parse()
    assert not any(e.password for e in screen.elements)   # the zero-size PIN field
    assert "Next alarm in 9 hours" in screen.texts


def test_reading_order_and_indexes():
    screen = parse()
    tops = [e.bounds[1] for e in screen.elements]
    assert tops == sorted(tops)
    assert [e.index for e in screen.elements] == list(range(len(screen.elements)))


def test_landscape_swaps_the_axes():
    rotated = XML.replace('<hierarchy rotation="0">', '<hierarchy rotation="1">')
    screen = ui.parse(rotated, 1080, 2400)
    assert (screen.width, screen.height) == (2400, 1080)


def test_unknown_size_falls_back_to_the_tree_extent():
    screen = ui.parse(XML, 0, 0)
    assert (screen.width, screen.height) == (1080, 2400)


def test_element_cap_leaves_a_note():
    screen = parse(max_elements=3)
    assert len(screen.elements) == 3
    assert "scroll for more" in screen.note
