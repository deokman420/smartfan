"""
Filter frame for Smartfan V2 -- the tray that holds the lint pad on the room
side of the ceiling line, where the model puts it on purpose.

Dimensions from hardware/v2/model-v2.html, in its own names:

    GRILLE_W = 210            the visible plate in the room
    FIL_Y0 = -18, FIL_H = 14  the pad's slot, room side of y = 0
    plate                     box(GRILLE_W - 24, 4, GRILLE_W - 24) at y = -6
                              -> 186 square, 4 thick, spanning -8 .. -4
    pad                       box(GRILLE_W - 40, FIL_H - 4, GRILLE_W - 40)
                              -> 170 square, 10 thick, spanning -18 .. -8

Part coordinates are the print: Z up, z = 0 at the plate's upper face, so the
model's y = -4 .. -18 reads as z = 0 .. 14 here. It prints plate-down with no
overhang anywhere, which is why it is drawn this way round.

Why the model's one box became a tray: at 4 mm thick the plate is a retainer,
not a container -- the 10 mm pad has to sit somewhere. So the plate keeps its
186 square and gains a 10 mm skirt at 170.6 inside, which is the pad's pocket.
The skirt's outside lands at 175.4, well inside the 186, so the plate still
presents a flange all the way round for the grille to close against.

The window and the bars are additions the model does not draw. A plate with
no window is a blank-off; the window is the air path. The bars are there
because a 158 mm span of 10 PPI foam will bow into the fan without them, and
they cost about 2 % of the open area.

Filter arithmetic, since it decides whether this part should exist at all:
the fan has 0.155 in w.g. in total and the easy-duct case already spends 0.10
for about 20 CFM. A clean pad costs 0.02 - 0.05, a loaded one more. If this
frame stays in the stack, the fan wants to be the iPPC-3000 rather than the
2000 -- same 120 x 25 pocket, same 105 screws, 0.30 in w.g. and 110 CFM free
air, at 43.5 dBA against 29.7. That trade is the model's, not this script's.

    python -I filter_frame.py [out.stl]
"""

import sys
from pathlib import Path

from build123d import Box, Pos, export_stl

# ---- dimensions, all in one place (mm) ----
PLATE = 186.0            # GRILLE_W - 24, as modelled
PLATE_T = 4.0            # as modelled

PAD = 170.0              # GRILLE_W - 40: the pad, as modelled
PAD_H = 10.0             # FIL_H - 4
FIT = 0.3                # a side, so the pad drops in
POCKET = PAD + 2 * FIT   # 170.6
SKIRT = 2.4              # 6 perimeters at 0.4

WINDOW = 158.0           # the air path; leaves 6 mm of ledge over the pad
BARS = 2                 # each way, so the pad cannot bow into the fan
BAR_W = 4.0


def frame():
    """Plate with its window and bars, plus the skirt that pockets the pad."""
    plate = Pos(0, 0, -PLATE_T / 2) * Box(PLATE, PLATE, PLATE_T)
    window = Pos(0, 0, -PLATE_T / 2) * Box(WINDOW, WINDOW, PLATE_T + 2)
    plate = plate.cut(window)

    # bars across the window, evenly spaced, in both directions
    step = WINDOW / (BARS + 1)
    bars = []
    for i in range(1, BARS + 1):
        at = -WINDOW / 2 + i * step
        bars.append(Pos(at, 0, -PLATE_T / 2) * Box(BAR_W, WINDOW, PLATE_T))
        bars.append(Pos(0, at, -PLATE_T / 2) * Box(WINDOW, BAR_W, PLATE_T))

    outer = POCKET + 2 * SKIRT
    skirt = Pos(0, 0, PAD_H / 2) * Box(outer, outer, PAD_H)
    skirt = skirt.cut(Pos(0, 0, PAD_H / 2 + 1) * Box(POCKET, POCKET, PAD_H + 2))

    body = plate.fuse(*bars, skirt)
    bb = body.bounding_box()
    return (Pos(0, 0, -bb.min.Z) * body).clean()


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "filter-frame-v2.stl")
    part = frame()
    bb = part.bounding_box()
    export_stl(part, str(out), tolerance=0.02, angular_tolerance=0.1)
    print(f"wrote {out}")
    print(f"  plate {PLATE:.0f} square   window {WINDOW:.0f}   pocket {POCKET:.1f}")
    print(f"  envelope {bb.size.X:.1f} x {bb.size.Y:.1f} x {bb.size.Z:.1f} mm")
    print(f"  volume {part.volume / 1000:.1f} cm3")
