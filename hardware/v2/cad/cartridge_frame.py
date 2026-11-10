"""
Cartridge frame for Smartfan V2 -- the square carrier the 120 x 25 fan sits
in, which slides out of the ceiling downward when the fan needs changing.

Dimensions from hardware/v2/model-v2.html, in its own names:

    FR = 120, FT = 25     the fan
    CART_Y0 = 6           the frame's underside, 6 mm into the void
    CART_H  = 38          its height, so y = 6 .. 44
    walls                 box(FR + 12, CART_H, 10) at z = -+61, and
                          box(10, CART_H, FR + 2) at x = +-61
                          -> 132 square outside
    FY = CART_Y0 + 6 + FT / 2     the fan's mid-height, so the fan's underside
                          sits 6 mm above the frame's
    rails                 box(6, 4, BOXW - 20) at x = +-71, y = 46
    latch                 box(16, 5, 8) at (0, 8, -67)

Part coordinates are the print: Z up, z = 0 at the frame's underside, so the
model's y = 6 .. 44 reads as z = 0 .. 38 here, and the model's x and z are x
and y. It prints standing exactly like this.

Where the model had to become a part:

  * The walls are 5.7, not 10. This one is not a preference: 132 outside with
    10 mm walls leaves a 112 opening, and the fan is 120 square. The viewer
    draws the fan interpenetrating the frame and nothing complains, but a
    printed frame has to actually hold it. The outside stays 132, because
    that is the dimension everything else is spaced against -- the rails at
    +-71 and the air box at +-75 -- so the walls give way instead. 120.6
    leaves 0.3 a side on the fan.

  * The fan lands on four corner seats rather than a continuous ledge. Its
    screw square is 105, which falls well inboard of the walls, so there is
    nothing at the right radius to screw into. Four pads at the cavity
    corners put material under each screw, and keep the room-side opening at
    114.6 rather than choking it down to the ledge.

  * Pilot holes, not clearance holes: 3.4 for M4 self-tappers into plastic.

  * The rails are 8 wide, not the modelled 6, and carry a 45 degree gusset
    underneath. At 6 they float 2 mm clear of a 132 wall and attach to
    nothing; at 8 they meet it. The gusset is what lets the frame print
    standing up with no support under them.

    python -I cartridge_frame.py [out.stl]
"""

import sys
from pathlib import Path

from build123d import (
    Box,
    Cylinder,
    Plane,
    Polyline,
    Pos,
    export_stl,
    extrude,
    make_face,
    mirror,
)

# ---- dimensions, all in one place (mm) ----
OUT = 132.0              # FR + 12: the outside, as modelled
HEIGHT = 38.0            # CART_H
FAN = 120.0              # FR
FIT = 0.3                # a side, on the fan
CAVITY = FAN + 2 * FIT   # 120.6
WALL = (OUT - CAVITY) / 2    # 5.7, see the note above

SEAT_H = 6.0             # FY puts the fan's underside 6 above the frame's
SEAT = 22.0              # square pad at each cavity corner
FIX_PITCH = 105.0        # the fan's own screw square
PILOT_D = 3.4            # M4 self-tapper into plastic

RAIL_X0 = OUT / 2        # 66: the rail starts at the wall face
RAIL_X1 = 74.0           # and reaches 71 +- 3, as modelled
RAIL_T = 4.0             # its thickness, as modelled
RAIL_LEN = 130.0         # BOXW - 20
GUSSET = 8.0             # 45 degrees under the rail, so it prints

LATCH_W, LATCH_H, LATCH_OUT = 16.0, 5.0, 5.0   # the tab that holds it up


def _rail():
    """One rail: a bar along the top edge with a 45 degree underside.

    Drawn as a section in the XZ plane and extruded along the frame, which is
    the only way to get the gusset without chasing edges to chamfer.
    """
    z_top = HEIGHT
    z_bot = z_top - RAIL_T
    pts = [
        (RAIL_X0, 0, z_bot - GUSSET),   # the gusset's toe, on the wall
        (RAIL_X1, 0, z_bot),            # out to the rail, at 45 degrees
        (RAIL_X1, 0, z_top),
        (RAIL_X0, 0, z_top),
        (RAIL_X0, 0, z_bot - GUSSET),
    ]
    section = make_face(Polyline(*pts))
    bar = extrude(section, RAIL_LEN)
    bb = bar.bounding_box()
    return Pos(0, -bb.center().Y, 0) * bar


def frame():
    """Walls, corner seats, rails and latch, less the cavity and the pilots."""
    body = Pos(0, 0, HEIGHT / 2) * Box(OUT, OUT, HEIGHT)
    cavity = Pos(0, 0, HEIGHT / 2 + 1) * Box(CAVITY, CAVITY, HEIGHT + 2)
    body = body.cut(cavity)

    # four seats, tucked into the cavity corners, under the fan's screws
    inner = CAVITY / 2
    seats = [
        Pos(sx * (inner - SEAT / 2), sy * (inner - SEAT / 2), SEAT_H / 2)
        * Box(SEAT, SEAT, SEAT_H)
        for sx in (-1, 1) for sy in (-1, 1)
    ]

    rail = _rail()
    rails = [rail, mirror(rail, about=Plane.YZ)]

    latch = Pos(0, -(OUT / 2 + LATCH_OUT / 2 - 0.5), LATCH_H / 2 + 0.5) * Box(
        LATCH_W, LATCH_OUT + 1, LATCH_H
    )

    body = body.fuse(*seats, *rails, latch)

    pilots = [
        Pos(sx * FIX_PITCH / 2, sy * FIX_PITCH / 2, SEAT_H / 2)
        * Cylinder(PILOT_D / 2, SEAT_H * 3)
        for sx in (-1, 1) for sy in (-1, 1)
    ]
    return body.cut(*pilots).clean()


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "cartridge-frame-v2.stl")
    part = frame()
    bb = part.bounding_box()
    export_stl(part, str(out), tolerance=0.02, angular_tolerance=0.1)
    print(f"wrote {out}")
    print(f"  outside {OUT:.0f} square   wall {WALL:.1f}   cavity {CAVITY:.1f}")
    print(f"  envelope {bb.size.X:.1f} x {bb.size.Y:.1f} x {bb.size.Z:.1f} mm")
    print(f"  volume {part.volume / 1000:.1f} cm3")
