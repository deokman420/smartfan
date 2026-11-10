"""
Turning elbow for Smartfan V2 -- the printed bend that takes the fan's
vertical discharge round to the horizontal duct, inside the steel air box.

Dimensions from hardware/v2/model-v2.html, in its own names:

    SHR_R   = DUCT_Y - (CART_Y0 + CART_H) = 53    ring radius, "as big as
                                                   the box allows"
    DUCT_R  = 50          bore radius -> bore 100, so R/D = 0.53
    bend                  quarter torus, in over the fan at y = 44 facing
                          +Y, out at y = 97 facing +Z
    tail                  FANZ + SHR_R .. BOX_Z1 + 6, i.e. z = 53 .. 81
    lip                   128 square, 4 thick, at y = 46 -- "the fan is 120
                          square, the bend is 100 round, and the air that
                          misses the mouth is air that recirculates"

Part coordinates are the model's own: millimetres, Y up, y = 0 the ceiling
line, the fan on the axis at x = z = 0.

Where the viewer's zero-thickness surfaces had to become a real part:

  * Wall goes outward, 2.4 mm, bore stays 100 -- the same call as the duct
    collar and for the same reason: this fan has 0.155 in w.g. in total and
    cannot buy back bore area. Outward is also what the envelope allows,
    but only just. The bend's crown lands at y = 149.4 and the air box's
    inside is 151, so there is 1.6 mm over the top. That is the model's
    own margin, not something introduced here.

  * R/D 0.53 means the inside of the bend nearly pinches. At the throat
    the outer wall passes 0.6 mm from the ring axis. It closes, but it is
    the reason this elbow is as short as it is, and it is why the model
    labels the ratio rather than hiding it.

  * The lip gets four M4 clearance holes on a 105 mm square. The model
    draws it as a plain plate, but 105 is the iPPC's own screw spacing, so
    the shroud can share the fan's fixings instead of needing its own.

Printing. A 90 degree bend will not print in one piece without a great
deal of support, so the part is symmetric about the x = 0 plane and is
cut there: HALF is one half, printed twice, flat face on the bed. The two
halves are mirror images, so one STL does both -- print it, then print it
again and turn it over. Glue the flat faces (CA or a solvent weld; this
seam is not structural and is not the fire barrier, the steel box is).

The one honest blemish in that orientation: the bore's crown on each half
is a concave ceiling, so the last ~10 mm before the top exceeds 45 degrees
and will droop a little. It is inside the duct and out of sight. Turn on
support everywhere if you would rather have it crisp.

    python -I turning_elbow.py            -> both STLs
    python -I turning_elbow.py --whole    -> the one-piece part only
"""

import sys
from pathlib import Path

from build123d import (
    Axis,
    Box,
    Circle,
    Cylinder,
    Plane,
    PolarLocations,
    Pos,
    Rot,
    export_stl,
    revolve,
)

# ---- dimensions, all in one place (mm), named as the model names them ----
BORE_R = 50.0            # DUCT_R
WALL = 2.4               # material goes outward, see the note above
OUT_R = BORE_R + WALL    # 52.4

RING_R = 53.0            # SHR_R: ring radius of the bend centreline
IN_Y = 44.0              # CART_Y0 + CART_H: the bend's mouth, over the fan
DUCT_Y = 97.0            # the bore centreline on the way out
TAIL_Z0 = 53.0           # FANZ + SHR_R: where the bend ends

# The model runs the tail to BOX_Z1 + 6 = 81, i.e. 6 mm through the wall. That
# is fine while the elbow is a toggle, which is all it is in the viewer
# (`shroud.visible = false`): the plain spigot and this are alternatives drawn
# in the same space, never both. As parts they foul each other over 22 mm of
# bore. --with-collar is the pair that actually builds: the tail stops on the
# wall and gains a flange, the duct collar is built --no-stub, and the same
# four M6 clamp elbow, steel and collar together.
WITH_COLLAR = "--with-collar" in sys.argv
TAIL_Z1 = 75.0 if WITH_COLLAR else 81.0        # BOX_Z1, or 6 past it

# This flange is square, 106, where the collar's is a 144 disc -- because it
# is the one inside the box. A 144 disc centred on the bore would reach
# y = 169 and the box's inside is 151. A 106 square clears the four bolts at
# R63 with 8 mm to spare and lands on y = 44 .. 150: the cartridge's top plane
# below, 1 mm under the box's roof above. There is no third option at this
# bolt circle; the envelope decides the shape.
WALL_FLANGE = 106.0
WALL_FLANGE_T = 4.0
WALL_BOLT_R = 63.0       # BORE_R + 13, the collar's pattern
WALL_BOLT_D = 6.5        # M6 clearance

LIP = 128.0              # square mouth over a 120 fan
LIP_T = 4.0
LIP_Y = 46.0             # its centre, so it spans 44 .. 48

FIX_PITCH = 105.0        # the iPPC's screw spacing -- share the fan's fixings
FIX_D = 4.5              # M4 clearance

# The ring axis: through the centre of the bend's arc, along X. The arc runs
# from (0, 44, 0) pointing +Y round to (0, 97, 53) pointing +Z.
RING_AXIS = Axis((0, IN_Y, RING_R), (1, 0, 0))


# build123d 0.13's `+` does not fuse overlapping solids the way it fuses
# touching ones -- it hands back the complement. Everything below uses the
# explicit boolean methods instead, which are unambiguous anyway.

def _tube(radius):
    """The swept volume at one radius: the bend, plus the straight tail."""
    section = Pos(0, IN_Y, 0) * (Plane.XZ * Circle(radius))
    bend = revolve(section, RING_AXIS, 90)
    tail = Pos(0, DUCT_Y, (TAIL_Z0 + TAIL_Z1) / 2) * Cylinder(radius, TAIL_Z1 - TAIL_Z0)
    return bend.fuse(tail)


def elbow():
    """The one-piece elbow: outer sweep and lip, less the bore."""
    outer = _tube(OUT_R).fuse(Pos(0, LIP_Y, 0) * Box(LIP, LIP_T, LIP))

    if WITH_COLLAR:
        # a flange on the inside of the steel, matching the collar's outside
        outer = outer.fuse(
            Pos(0, DUCT_Y, TAIL_Z1 - WALL_FLANGE_T / 2)
            * Box(WALL_FLANGE, WALL_FLANGE, WALL_FLANGE_T)
        )

    # The bore, run past both open ends so the cuts are clean: down through
    # the lip, and out beyond the air box wall.
    bore = _tube(BORE_R).fuse(
        Pos(0, (IN_Y + LIP_Y + LIP_T) / 2 - 4, 0) * (Rot(90, 0, 0) * Cylinder(BORE_R, 20)),
        Pos(0, DUCT_Y, TAIL_Z1) * Cylinder(BORE_R, 16),
    )

    fixings = Pos(0, LIP_Y, 0) * (
        Rot(90, 0, 0)
        * (PolarLocations(FIX_PITCH / 2 * 2 ** 0.5, 4, start_angle=45)
           * Cylinder(FIX_D / 2, LIP_T * 4))
    )
    if WITH_COLLAR:
        wall_bolts = Pos(0, DUCT_Y, TAIL_Z1 - WALL_FLANGE_T / 2) * (
            PolarLocations(WALL_BOLT_R, 4, start_angle=45)
            * Cylinder(WALL_BOLT_D / 2, WALL_FLANGE_T * 4)
        )
        fixings = list(fixings) + list(wall_bolts)

    return outer.cut(bore, *fixings).clean()


def half(part):
    """Cut on the symmetry plane: x >= 0. Print it twice, mirrored."""
    keep = Pos(200, 0, 0) * Box(400, 600, 600)
    cut = part.intersect(keep)
    if isinstance(cut, (list, tuple)):          # intersect can hand back a ShapeList
        if len(cut) != 1:
            raise RuntimeError(f"the half came back in {len(cut)} pieces, expected 1")
        cut = cut[0]
    return cut.clean()


def for_printing(solid, lay_split_face_down):
    """Model coordinates are Y up; slicers are Z up, so every STL leaves here
    standing the way it should be printed, centred over the origin and sitting
    on z = 0. Left in model coordinates a half stands on edge, 145 tall, which
    is the one orientation the split was made to avoid."""
    # a half goes split face down (its x = 0 plane); a whole part stands on
    # the lip, which is its y = 44 plane.
    turned = (Rot(0, -90, 0) if lay_split_face_down else Rot(90, 0, 0)) * solid
    bb = turned.bounding_box()
    return Pos(-bb.center().X, -bb.center().Y, -bb.min.Z) * turned


def _report(name, solid):
    bb = solid.bounding_box()
    print(f"  {name:<10} {bb.size.X:6.1f} x {bb.size.Y:6.1f} x {bb.size.Z:6.1f} mm"
          f"   {solid.volume / 1000:6.1f} cm3")
    return bb


if __name__ == "__main__":
    whole_only = "--whole" in sys.argv
    here = Path(__file__).parent

    part = elbow()
    print("turning elbow, R/D "
          f"{RING_R / (BORE_R * 2):.2f}, bore {BORE_R * 2:.0f}, wall {WALL}")
    bb = _report("in place", part)
    print(f"  crown at y = {bb.max.Y:.1f}  (the air box's inside is 151)")

    tag = "-collar" if WITH_COLLAR else ""
    whole = for_printing(part, lay_split_face_down=False)
    _report("one piece", whole)
    export_stl(whole, str(here / f"turning-elbow-v2{tag}.stl"),
               tolerance=0.02, angular_tolerance=0.1)

    if not whole_only:
        h = for_printing(half(part), lay_split_face_down=True)
        _report("half", h)
        export_stl(h, str(here / f"turning-elbow-v2{tag}-half.stl"),
                   tolerance=0.02, angular_tolerance=0.1)
        print("  print the half twice and turn one over")
    if WITH_COLLAR:
        print("  pairs with: python -I duct_collar.py --no-stub")
