"""
Fit coupons -- small offcuts of the real parts, to print before committing to
the real ones.

The set is 25 hours and 400-odd grams of PETG. Two of the fits in it cannot be
checked by looking at a screen, and both are cheap to be wrong about:

  A. the collar's spigot and clamp bead against the actual flex duct and its
     clamp. The bore is 100 and the outside is 104.8 because this fan cannot
     spare bore area -- that is the right call on paper, and paper is not what
     the duct slides onto. Coupon A is the proud end of the collar, bead and
     all, cut 25 mm up. If the duct will not go on, change WALL in
     duct_collar.py before printing the 5-hour part, not after.

  B. the cartridge's corner: one seat, one pilot, one wall corner and one
     rail. It answers whether an M4 self-tapper holds in a 3.4 pilot, and
     whether the fan's corner clears the 5.7 wall -- the wall thickness that
     had to be argued down from the modelled 10 to make the fan fit at all.

Both are cut out of the real parts rather than drawn again, so a coupon cannot
drift from what it is standing in for. Change a part, re-run this, and the
coupon changes with it.

    python -I fit_coupons.py        -> both, on one plate
"""

import sys
from pathlib import Path

# The parts are imported, so this needs its own directory on the path:
# python -I deliberately leaves it off, and we want -I for the repo's
# secrets.py, which shadows the stdlib module of that name.
sys.path.insert(0, str(Path(__file__).parent))

from build123d import Box, Pos, export_stl       # noqa: E402

import cartridge_frame                            # noqa: E402
import duct_collar                                # noqa: E402

COUPON_A_H = 25.0        # enough of the spigot to take the bead and a clamp
GAP = 20.0               # between the two on the plate


def coupon_a():
    """The collar's proud end: the duct fit and the clamp bead."""
    part = duct_collar.for_printing(duct_collar.collar())
    keep = Pos(0, 0, COUPON_A_H / 2) * Box(300, 300, COUPON_A_H)
    cut = part.intersect(keep)
    return cut[0] if isinstance(cut, (list, tuple)) else cut


def coupon_b():
    """One corner of the cartridge: seat, pilot, wall and rail."""
    part = cartridge_frame.frame()
    inner = cartridge_frame.CAVITY / 2
    # the +x, -y corner: far from the latch, and it carries a whole rail
    keep = Pos(inner, -inner, cartridge_frame.HEIGHT / 2) * Box(
        90, 90, cartridge_frame.HEIGHT + 2
    )
    cut = part.intersect(keep)
    return cut[0] if isinstance(cut, (list, tuple)) else cut


def plate():
    """Both coupons, side by side, far enough apart to pick up separately."""
    a, b = coupon_a(), coupon_b()
    ba, bb = a.bounding_box(), b.bounding_box()
    a = Pos(-ba.center().X - ba.size.X / 2 - GAP / 2, -ba.center().Y, 0) * a
    b = Pos(-bb.center().X + bb.size.X / 2 + GAP / 2, -bb.center().Y, 0) * b
    return a, b


if __name__ == "__main__":
    here = Path(__file__).parent
    a, b = plate()

    for name, solid in (("fit-coupon-collar.stl", a), ("fit-coupon-cartridge.stl", b)):
        bbx = solid.bounding_box()
        export_stl(solid, str(here / name), tolerance=0.02, angular_tolerance=0.1)
        print(f"wrote {name}")
        print(f"  {bbx.size.X:.1f} x {bbx.size.Y:.1f} x {bbx.size.Z:.1f} mm"
              f"   {solid.volume / 1000:.1f} cm3")

    both = a.fuse(b)
    export_stl(both, str(here / "fit-coupons.stl"),
               tolerance=0.02, angular_tolerance=0.1)
    bbb = both.bounding_box()
    print("wrote fit-coupons.stl  (both, one plate)")
    print(f"  {bbb.size.X:.1f} x {bbb.size.Y:.1f} x {bbb.size.Z:.1f} mm"
          f"   {both.volume / 1000:.1f} cm3")
