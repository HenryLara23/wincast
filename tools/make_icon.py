#!/usr/bin/env python3
"""
Render the Wincast icon ("W as a graph") into src/wincast/resources/icon/.

    pip install cairosvg pillow
    python tools/make_icon.py

Writes wincast.svg (the master), wincast-<size>.png for each size and
wincast.ico (all sizes, for the .exe and the installer). The outputs are
committed, so building the app doesn't need cairosvg. Sizes up to 24 px use a
bolder variant: a thin line turns to mush in the tray.
"""

import io
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "src" / "wincast" / "resources" / "icon"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)

LOSE, EVEN, WIN, BG = "#ff5d5d", "#c9ced8", "#3ddc97", "#10141b"


def svg(small: bool) -> str:
    """64x64 master. `small`: less margin, thicker stroke, W fills more of the tile."""
    if small:
        tile, rx, width, dot = (0, 64), 12, 9, 6.5
        path, end = "M9 17 L20.5 49 L32 26 L43.5 49 L55 12", (55, 12)
    else:
        tile, rx, width, dot = (2, 60), 14, 6.5, 5
        path, end = "M11 20 L21 47 L32 27 L43 47 L53 15", (53, 15)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">
  <defs>
    <linearGradient id="g" x1="0" x2="1">
      <stop offset="0" stop-color="{LOSE}"/><stop offset=".5" stop-color="{EVEN}"/><stop offset="1" stop-color="{WIN}"/>
    </linearGradient>
  </defs>
  <rect x="{tile[0]}" y="{tile[0]}" width="{tile[1]}" height="{tile[1]}" rx="{rx}" fill="{BG}"/>
  <path d="{path}" fill="none" stroke="url(#g)" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="{end[0]}" cy="{end[1]}" r="{dot}" fill="{WIN}"/>
</svg>
"""


def main():
    import cairosvg
    from PIL import Image

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "wincast.svg").write_text(svg(False), encoding="utf-8", newline="\n")
    images = []
    for n in SIZES:
        png = cairosvg.svg2png(bytestring=svg(n <= 24).encode(), output_width=n, output_height=n)
        (OUT / f"wincast-{n}.png").write_bytes(png)
        images.append(Image.open(io.BytesIO(png)).convert("RGBA"))
    big = images[-1]
    big.save(OUT / "wincast.ico", format="ICO", sizes=[(n, n) for n in SIZES],
             append_images=images[:-1])
    print(f"wrote {len(SIZES)} PNGs, wincast.ico and wincast.svg to {OUT}")


if __name__ == "__main__":
    main()
