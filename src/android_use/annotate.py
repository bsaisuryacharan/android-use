"""Screenshots the model can act on: numbered boxes, zoomed crops, sane sizes.

The accessibility tree names most things but not everything - icon-only
buttons, images, canvas-drawn apps. A screenshot shows them, but on its own
gives no way to act on them except guessing pixels. Drawing the same numbers
get_screen uses onto the picture joins the two up: the model sees the
unlabelled icon, reads its number, and taps it by index like anything else.
"""
from __future__ import annotations

import io

from .models import Bounds, Screen

# High-contrast colours that stay distinct from each other on photos and on
# both light and dark app themes.
_PALETTE = [
    (230, 57, 70), (29, 110, 205), (32, 150, 60), (235, 125, 0),
    (150, 40, 175), (0, 140, 130), (200, 30, 120), (90, 90, 90),
]


def _open(image_bytes: bytes):
    from PIL import Image

    return Image.open(io.BytesIO(image_bytes)).convert("RGB")


def _jpeg(img, quality: int = 80) -> bytes:
    buf = io.BytesIO()
    # JPEG, not PNG: phone screens carry photo wallpaper and thumbnails, and
    # PNG encodes those ~10x larger for no gain in readability.
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def _fit(img, max_width: int):
    from PIL import Image

    if max_width and img.width > max_width:
        ratio = max_width / img.width
        img = img.resize((max_width, max(1, int(img.height * ratio))), Image.LANCZOS)
    return img


def _font(size: int):
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow older than 10.1 has one fixed-size font
        return ImageFont.load_default()


def to_jpeg(image_bytes: bytes, max_width: int = 800) -> bytes:
    return _jpeg(_fit(_open(image_bytes), max_width))


def is_blank(image_bytes: bytes) -> bool:
    """Is this screenshot solid black?

    Apps that set FLAG_SECURE (banking, some video players) come back as a
    black frame. Saying so saves the model from squinting at nothing.
    """
    try:
        img = _open(image_bytes)
    except Exception:  # noqa: BLE001 - an unreadable image is not "blank"
        return False
    img.thumbnail((64, 64))
    extrema = img.getextrema()
    return all(high <= 8 for _low, high in extrema)


def _scale(img, screen: Screen) -> tuple[float, float]:
    """Screen coordinates -> image pixels. The phone app downscales its
    screenshots before sending them, so the two rarely match."""
    sx = img.width / screen.width if screen.width else 1.0
    sy = img.height / screen.height if screen.height else sx
    return sx, sy


def annotate(image_bytes: bytes, screen: Screen, max_width: int = 800) -> bytes:
    """Outline every element and label it with its get_screen index."""
    from PIL import ImageDraw

    img = _fit(_open(image_bytes), max_width)
    sx, sy = _scale(img, screen)
    draw = ImageDraw.Draw(img)
    size = max(12, img.width // 42)
    font = _font(size)
    for e in screen.elements:
        colour = _PALETTE[e.index % len(_PALETTE)]
        x1, y1, x2, y2 = e.bounds
        box = (int(x1 * sx), int(y1 * sy), int(x2 * sx) - 1, int(y2 * sy) - 1)
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        draw.rectangle(box, outline=colour, width=2)
        tag = str(e.index)
        left, top, right, bottom = draw.textbbox((0, 0), tag, font=font)
        tw, th = right - left, bottom - top
        # The tag sits inside the top-left corner, so neighbouring boxes do not
        # cover each other's numbers as easily as tags drawn above the box.
        tx, ty = box[0] + 1, box[1] + 1
        draw.rectangle((tx, ty, tx + tw + 6, ty + th + 6), fill=colour)
        draw.text((tx + 3 - left, ty + 3 - top), tag, fill=(255, 255, 255), font=font)
    return _jpeg(img)


def crop(image_bytes: bytes, screen: Screen, bounds: Bounds, max_width: int = 800) -> bytes:
    """A close-up of one region - for small text, icons, or a detail in a photo.

    The region is padded so its surroundings stay recognisable, and small
    regions are enlarged, which is the whole point of zooming.
    """
    from PIL import Image

    img = _open(image_bytes)
    sx, sy = _scale(img, screen)
    x1, y1, x2, y2 = bounds
    pad_x = max(24, int((x2 - x1) * 0.15))
    pad_y = max(24, int((y2 - y1) * 0.15))
    box = (
        max(0, int((x1 - pad_x) * sx)),
        max(0, int((y1 - pad_y) * sy)),
        min(img.width, int((x2 + pad_x) * sx)),
        min(img.height, int((y2 + pad_y) * sy)),
    )
    if box[2] <= box[0] or box[3] <= box[1]:
        raise ValueError("That region is not on the screen.")
    region = img.crop(box)
    target = min(max_width, region.width * 3)
    if region.width < target:
        ratio = target / region.width
        region = region.resize((int(target), max(1, int(region.height * ratio))), Image.LANCZOS)
    return _jpeg(_fit(region, max_width), quality=88)
