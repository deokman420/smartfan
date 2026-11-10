"""
Duct collar for Smartfan V2 -- the printed part that carries the 100 mm
flex duct out of the steel air box.

Every dimension here is taken from hardware/v2/model-v2.html, which models the
assembly at real size in millimetres. The model's numbers, in its own names:

    DUCT_R  = 50      bore radius      -> bore 100
    SPIGOT  = 50.8    2 in proud of the air box wall
    BOX_Z1  = 75      the air box downstream wall (BOXW/2)
    flange            R + 22 = 72 outer, 4 thick, at BOX_Z1 + 3
    bolts             4 off, radius R + 13 = 63, at 45 deg to the axes
    bead              ring at R + 3, tube 3, at BOX_Z1 + SPIGOT - 14

Part-local coordinates: the axis is +Z, z = 0 is the inboard end of the
spigot (inside the air box), and the steel wall sits at z = WALL_Z. That
makes the model's 49 .. 125.8 span read as 0 .. 76.8 here.

Two places where the viewer's zero-thickness surfaces had to become a real
part, and the reasoning, because neither is free:

  * Wall thickness goes *outward*. The model draws the bore as a surface at
    R50 with nothing either side of it. Thickening inward would hand back
    bore area, and this fan cannot spare it: the whole static-pressure
    budget is 0.155 in w.g. and the easy-duct case already spends 0.10 of
    it. So the bore stays 100.0 and the outside diameter becomes 104.8.
    100 mm aluminium flex goes over that with the clamp, which is what the
    clamp is for.

  * The bead protrudes, it is not half-buried. The model's torus has its
    centreline at R53, so half of it sits inside the surface. Here the
    centreline is on the outside diameter and the bead stands 3 mm proud --
    a clamp stop under the flex, which is the job the model gives it.

Printing: stand it on the proud end, bead near the bed, flange face up, and
run a brim -- the first layer is only a 2.4 mm wide ring. The flange's
bed-facing side is a 19 mm annular overhang; that is inherent to a flange
in the middle of a tube and wants support on that face alone. The
alternative, if you would rather print with no support at all, is to set
STUB = 0 and clamp a printed backing ring to the inside of the steel
through the same four bolts.

    python -I duct_collar.py [out.stl]
"""

import sys
from pathlib import Path

from build123d import (
    Rot,
    Axis,
    Cylinder,
    PolarLocations,
    Polyline,
    Pos,
    Torus,
    export_stl,
    make_face,
    revolve,
)

# ---- dimensions, all in one place (mm), named as the model names them ----
BORE_R = 50.0           # DUCT_R: the bore stays 100, see the note above
WALL = 2.4              # 6 perimeters at 0.4 -- material goes outward
OUT_R = BORE_R + WALL   # 52.4

# What reaches inside the air box. It must be 0 if the turning elbow is
# fitted: that part's tail runs to 6 mm past the wall at this same radius, so
# the two want the same 22 mm of bore. The model never shows the clash because
# the elbow is a toggle there (`shroud.visible = false`) and the two air paths
# are alternatives, not neighbours. With --no-stub the flange lands on the
# wall and the elbow's own flange takes the other side of the steel.
STUB = 0.0 if "--no-stub" in sys.argv else 26.0
FLANGE_T = 4.0          # as modelled
FLANGE_R = BORE_R + 22  # 72: as modelled
PROUD = 46.8            # past the flange, so 50.8 clear of the wall
LEN = STUB + FLANGE_T + PROUD        # 76.8, the model's tube length
WALL_Z = STUB - 1.0                  # where the steel sits, as the model draws it

BOLT_R = BORE_R + 13    # 63: as modelled
BOLT_D = 6.5            # M6 clearance
BOLT_N = 4

BEAD_Z = STUB + FLANGE_T + 31.8      # 2 in of engagement ahead of the bead
BEAD_R = 3.0                         # stands proud, see the note above

CH = 1.0                # edge chamfers
LEAD = 1.2              # lead-in at the proud end, so the flex starts easily


def collar():
    """One solid: revolved cross-section, bead fused on, four bolts removed."""
    # (x, y, z) around the cross-section in the XZ plane: up the bore, back
    # down the outside, out over the flange, and home.
    pts = [
        (BORE_R, 0, 0.0),
        (BORE_R, 0, LEN - LEAD),
        (BORE_R + LEAD, 0, LEN),
        (OUT_R, 0, LEN),
        (OUT_R, 0, STUB + FLANGE_T),
        (FLANGE_R - CH, 0, STUB + FLANGE_T),
        (FLANGE_R, 0, STUB + FLANGE_T - CH),
        (FLANGE_R, 0, STUB + CH),
        (FLANGE_R - CH, 0, STUB),
        (OUT_R, 0, STUB),
        (OUT_R, 0, 0.0),
        (BORE_R, 0, 0.0),
    ]

    # With STUB = 0 the flange lands on the tube's end and two of the points
    # above coincide; a zero-length edge is what OCC refuses to build.
    pts = [p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]]

    body = revolve(make_face(Polyline(*pts)), Axis.Z)
    bead = Pos(0, 0, BEAD_Z) * Torus(major_radius=OUT_R, minor_radius=BEAD_R)
    bolts = Pos(0, 0, STUB) * (
        PolarLocations(BOLT_R, BOLT_N, start_angle=45)
        * Cylinder(BOLT_D / 2, FLANGE_T * 3)
    )
    return (body + bead) - bolts


def for_printing(solid):
    """Turn it end for end so it stands on the proud end, and sit it on z = 0.

    The flange has an overhanging face whichever way up it goes. This way it
    is the outboard one, where the bolt heads are and support scars do not
    matter, rather than the face that has to seal against the steel."""
    turned = Rot(180, 0, 0) * solid
    bb = turned.bounding_box()
    return Pos(0, 0, -bb.min.Z) * turned


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    default = "duct-collar-v2-nostub.stl" if STUB == 0 else "duct-collar-v2.stl"
    out = Path(args[0] if args else default)
    part = for_printing(collar())
    bb = part.bounding_box()
    export_stl(part, str(out), tolerance=0.02, angular_tolerance=0.1)
    print(f"wrote {out}")
    print(f"  bore {BORE_R * 2:.1f}  outside {OUT_R * 2:.1f}  flange {FLANGE_R * 2:.0f}")
    print(f"  envelope {bb.size.X:.1f} x {bb.size.Y:.1f} x {bb.size.Z:.1f} mm")
    print(f"  volume {part.volume / 1000:.1f} cm3   steel wall at z = {WALL_Z:.0f}")
