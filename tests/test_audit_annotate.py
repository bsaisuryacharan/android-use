"""The activity log and screenshot helpers."""
import io

from PIL import Image

from android_use import annotate, audit

from conftest import el, png, scr


def test_audit_records_newest_first_and_filters():
    audit.record("tap", "mum", "[3] Wi-Fi")
    audit.record("type", "dad", "hello")
    audit.record("open app", "mum", "YouTube", ok=False)
    entries = audit.recent(10)
    assert [e["action"] for e in entries] == ["open app", "type", "tap"]
    assert [e["action"] for e in audit.recent(10, "mum")] == ["open app", "tap"]
    text = audit.render(audit.recent(10))
    assert "[mum] open app - YouTube  (failed)" in text


def test_audit_rolls_over(monkeypatch):
    monkeypatch.setattr(audit, "MAX_BYTES", 200)
    for i in range(30):
        audit.record("tap", "p", f"entry {i}")
    assert audit.LOG_PATH.with_suffix(".jsonl.1").exists()
    assert audit.LOG_PATH.stat().st_size < 1000


def test_audit_empty():
    assert "empty" in audit.render(audit.recent())


def _image(data: bytes):
    return Image.open(io.BytesIO(data))


def test_annotate_scales_to_max_width_and_draws():
    screen = scr("p", el(0, "A", bounds=(0, 0, 540, 300)), el(1, "B", bounds=(540, 0, 1080, 300)))
    out = annotate.annotate(png(), screen, max_width=400)
    img = _image(out)
    assert img.format == "JPEG" and img.width == 400
    # Something was drawn: the top-left corner is no longer the flat fill.
    assert img.convert("RGB").getpixel((3, 3)) != (40, 120, 200)


def test_annotate_handles_a_downscaled_screenshot():
    screen = scr("p", el(0, "A", bounds=(100, 100, 400, 400)))
    out = annotate.annotate(png(800, 1778), screen, max_width=800)
    assert _image(out).width == 800


def test_crop_enlarges_a_small_region():
    screen = scr("p")
    out = annotate.crop(png(), screen, (500, 500, 580, 540), max_width=800)
    assert _image(out).width > 80


def test_blank_detection():
    assert annotate.is_blank(png(colour=(0, 0, 0)))
    assert not annotate.is_blank(png())
    assert not annotate.is_blank(b"not an image")
