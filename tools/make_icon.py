"""Generate ``assets/icon.ico`` (and a PNG preview) with Pillow.

Run once with:  python tools/make_icon.py
The icon is a blue rounded tile with a plate and a leaf, drawn with simple
geometry so the result is deterministic and needs no design tools.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
SIZE = 512


def rounded_background() -> Image.Image:
    """Blue rounded square with a soft vertical gradient."""
    base = Image.new("RGB", (SIZE, SIZE), (37, 99, 235))
    draw = ImageDraw.Draw(base)
    top = (59, 130, 246)
    bottom = (29, 78, 216)
    for y in range(SIZE):
        ratio = y / (SIZE - 1)
        colour = tuple(
            int(top[channel] + (bottom[channel] - top[channel]) * ratio)
            for channel in range(3)
        )
        draw.line([(0, y), (SIZE, y)], fill=colour)

    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=110, fill=255)
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    canvas.paste(base, (0, 0), mask)
    return canvas


def leaf_layer() -> Image.Image:
    """A leaf built from the intersection of two circles, then rotated."""
    first = Image.new("L", (SIZE, SIZE), 0)
    second = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(first).ellipse([80, 60, 432, 300], fill=255)
    ImageDraw.Draw(second).ellipse([80, 212, 432, 452], fill=255)
    mask = ImageChops.multiply(first, second)

    leaf = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    leaf.paste(Image.new("RGBA", (SIZE, SIZE), (34, 197, 94, 255)), (0, 0), mask)
    leaf = leaf.rotate(-32, resample=Image.BICUBIC)

    rib = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(rib).line([(256, 140), (256, 372)], fill=(21, 128, 61, 255), width=10)
    rib = rib.rotate(-32, resample=Image.BICUBIC)
    return Image.alpha_composite(leaf, rib)


def build_icon() -> Image.Image:
    canvas = rounded_background()

    plate = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(plate)
    draw.ellipse([96, 132, 416, 452], fill=(255, 255, 255, 235))
    draw.ellipse([140, 176, 372, 408], fill=(226, 236, 252, 255))
    canvas = Image.alpha_composite(canvas, plate)

    return Image.alpha_composite(canvas, leaf_layer())


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    icon = build_icon()
    ico_path = ASSETS / "icon.ico"
    png_path = ASSETS / "icon.png"
    icon.save(
        ico_path,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    icon.resize((256, 256), Image.LANCZOS).save(png_path, format="PNG")
    print(f"wrote {ico_path}")
    print(f"wrote {png_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
