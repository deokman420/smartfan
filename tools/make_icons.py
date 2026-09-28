"""Render app/static/icon.svg to the PNGs iOS and Android actually use.

iOS gets a FULL-BLEED square: it applies its own squircle mask, so shipping
an already-rounded icon rounds it twice and leaves the corners clipped in.
The manifest icons keep the rounding, because Android composites them as-is.
"""
import os
from playwright.sync_api import sync_playwright

SVG = os.path.abspath("app/static/icon.svg")
OUT = os.path.abspath("app/static")
svg = open(SVG, encoding="utf-8").read()
square = svg.replace('rx="42"', 'rx="0"')

JOBS = [
    ("apple-touch-icon.png", 180, square),
    ("icon-192.png", 192, svg),
    ("icon-512.png", 512, svg),
]

with sync_playwright() as pw:
    b = pw.chromium.launch()
    for name, size, body in JOBS:
        pg = b.new_page(viewport={"width": size, "height": size},
                        device_scale_factor=1)
        pg.set_content(
            "<style>html,body{margin:0;padding:0;background:transparent}"
            "svg{display:block;width:%dpx;height:%dpx}</style>%s"
            % (size, size, body))
        pg.wait_for_timeout(200)
        path = os.path.join(OUT, name)
        pg.screenshot(path=path)
        pg.close()
        print("%-24s %4dx%-4d %6d bytes" % (name, size, size,
                                            os.path.getsize(path)))
    b.close()
