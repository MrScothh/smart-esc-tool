"""Draw the application icon. Run at build time; the result is shipped.

Pillow is a build dependency only - the icon is written once, embedded in the
executable and loaded from a file at runtime, so the packaged application does
not carry an imaging library it would use exactly never.

The mark has to survive 16 pixels in a taskbar, which rules out anything with
fine detail. It is a machined plate with a bolt cut out of it and one open arc
for the throttle sweep - the same vernacular as the window, which is bench
instruments rather than dashboards, and the same copper the accent comes from:
motor windings and bus bars. Everything is drawn at 1024 and downsampled,
because scaling a large drawing down is the cheapest antialiasing there is.
"""

import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
SIZES = (16, 24, 32, 48, 64, 128, 256)

DEEP = (15, 17, 19)
PLATE_TOP = (58, 62, 68)          # anodised aluminium, lit from above
PLATE_BOTTOM = (33, 36, 41)
COPPER = (201, 123, 62)
COPPER_LIT = (226, 150, 84)


def _lerp(a, b, t):
    return tuple(int(round(x + (y - x) * t)) for x, y in zip(a, b))


def draw(size=1024):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # badge: a rounded square with a vertical gradient, drawn as a stack of
    # rounded rectangles so the corners stay clean at every row
    radius = int(size * 0.16)          # machined, not a rounded app tile
    inset = int(size * 0.045)
    band = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bd = ImageDraw.Draw(band)
    for y in range(inset, size - inset):
        t = (y - inset) / float(size - 2 * inset)
        bd.line([(inset, y), (size - inset, y)],
                fill=_lerp(PLATE_TOP, PLATE_BOTTOM, t))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [inset, inset, size - inset, size - inset], radius=radius, fill=255)
    img.paste(band, (0, 0), mask)

    # the throttle arc: open at the bottom, like a gauge
    pad = int(size * 0.185)
    d.arc([pad, pad, size - pad, size - pad], start=145, end=35,
          fill=COPPER, width=int(size * 0.058))

    # the bolt, slightly off centre so it does not look like a letter
    w, h = size, size
    bolt = [(0.545, 0.215), (0.335, 0.545), (0.470, 0.545),
            (0.408, 0.805), (0.650, 0.455), (0.505, 0.455)]
    d.polygon([(x * w, y * h) for x, y in bolt], fill=COPPER_LIT)

    # a soft inner edge, so the badge reads as raised rather than printed
    # a lit top edge and a dark bottom one: a plate has thickness
    d.rounded_rectangle([inset, inset, size - inset, size - inset],
                        radius=radius, outline=(255, 255, 255, 38),
                        width=max(2, int(size * 0.007)))
    return img


def main():
    master = draw()
    frames = [master.resize((s, s), Image.LANCZOS) for s in SIZES]
    ico = os.path.join(HERE, "icon.ico")
    frames[-1].save(ico, format="ICO",
                    sizes=[(s, s) for s in SIZES])
    png = os.path.join(HERE, "icon.png")
    master.resize((256, 256), Image.LANCZOS).save(png)
    print("wrote %s and %s" % (ico, png))
    # A dark-background preview of the small sizes, to check it still reads.
    strip = Image.new("RGBA", (sum(s + 8 for s in SIZES) + 8, 72), DEEP + (255,))
    x = 8
    for f in frames:
        strip.paste(f, (x, (72 - f.size[1]) // 2), f)
        x += f.size[0] + 8
    strip.save(os.path.join(HERE, "icon-preview.png"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
