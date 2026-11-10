"""
Grille for Smartfan V2 -- the only part of this build anyone in the room ever
sees, and the one that carries the controls and the sensors.

Dimensions from hardware/v2/model-v2.html, in its own names:

    GRILLE_W = 210            the visible plate
    plate                     box(GRILLE_W, 6, GRILLE_W) at y = -21
                              -> 210 square, 6 thick, spanning -24 .. -18
    slots                     box(GRILLE_W - 40, 2.5, 7) for i = -4 .. 4
                              at z = i * 16   -> nine, 170 long, 16 pitch
    magnets                   cylinder r5 x 4, at +-(GRILLE_W / 2 - 14)
    sensor pod                at (78, -60): box(34, 9, 26), with the SHT40
                              and the LDR on it, and the plug that lets the
                              grille come off at all
    control cluster           at (-66, 72): a 52 x 20 bezel, a button at
                              CX - 16, LEDs at CX + 6 and CX + 18

Part coordinates are the print: Z up, z = 0 at the void face, so the model's
y = -18 .. -24 reads as z = 0 .. 6 here, and the model's x and z are x and y.
It prints void-face-down: the bezel stands proud on top, the slots go
straight through, and the magnet pockets bridge a 10 mm roof. No support.

Where the model had to become a part:

  * The magnets are recessed, not proud. The model stands them 4 mm off the
    void face, which would hold the plate 4 mm off the ceiling and would have
    to print as a boss on the bed face. A pocket open to the void face puts
    the magnet's own face flush with the plate's, so it still meets the steel
    it is holding to, and the room face stays unbroken.

  * The pod is not modelled, only its mounting. It is a module on the far
    side; what the plate owes it is two pilots, a vent under the SHT40 so it
    reads room air rather than plate, and a hole under the LDR so it sees
    room light. The model's own note is that the pod is "in room air and
    seeing room light", and through 6 mm of PETG it would be neither.

  * Three slots are cut short. The model draws the full nine running under
    the cluster and the pod, which would leave the button bezel and the
    sensor openings standing in mid-air. Each of those three stops 4 mm clear
    of what it would otherwise run into. It costs 7 % of the open area.

    python -I grille.py [out.stl]
"""

import sys
from pathlib import Path

from build123d import Box, Cylinder, Pos, export_stl

# ---- dimensions, all in one place (mm) ----
W = 210.0                # GRILLE_W
T = 6.0                  # plate thickness, as modelled

SLOT_L = 170.0           # GRILLE_W - 40
SLOT_W = 7.0             # as modelled
SLOT_PITCH = 16.0
SLOTS = range(-4, 5)     # nine of them

MAG_D, MAG_DEEP = 10.0, 4.0
MAG_AT = W / 2 - 14      # 91, as modelled

POD = (78.0, -60.0)      # the sensor pod's centre
POD_W, POD_D = 34.0, 26.0
PILOT_D = 2.5            # M3 self-tapper into plastic
VENT_W, VENT_D = 16.0, 4.0   # under the SHT40, at the pod's -9 offset
LDR_D = 4.0                  # under the LDR, at its +7

CLUSTER = (-66.0, 72.0)  # the control cluster's centre
BEZEL_W, BEZEL_D, BEZEL_H = 52.0, 20.0, 2.0
BTN_D = 12.4             # the model's r6 button, plus a working fit
LED_D = 5.4              # its r2.6 LEDs, likewise
CLEAR = 4.0              # how far a slot stops short of the above


def _slot_span(y):
    """How far a slot at this y may run before it undercuts something."""
    lo, hi = -SLOT_L / 2, SLOT_L / 2
    near = SLOT_W / 2

    bez_lo, bez_hi = CLUSTER[1] - BEZEL_D / 2, CLUSTER[1] + BEZEL_D / 2
    if y + near > bez_lo and y - near < bez_hi:
        lo = max(lo, CLUSTER[0] + BEZEL_W / 2 + CLEAR)

    pod_lo, pod_hi = POD[1] - POD_D / 2, POD[1] + POD_D / 2
    if y + near > pod_lo and y - near < pod_hi:
        hi = min(hi, POD[0] - POD_W / 2 - CLEAR)

    return lo, hi


def grille():
    """Plate, less the slots, the magnet pockets and the sensor openings,
    plus the bezel the controls sit in."""
    body = Pos(0, 0, T / 2) * Box(W, W, T)

    slots = []
    for i in SLOTS:
        y = i * SLOT_PITCH
        lo, hi = _slot_span(y)
        slots.append(
            Pos((lo + hi) / 2, y, T / 2) * Box(hi - lo, SLOT_W, T + 2)
        )

    pockets = [
        Pos(sx * MAG_AT, sy * MAG_AT, MAG_DEEP / 2 - 1) * Cylinder(MAG_D / 2, MAG_DEEP + 2)
        for sx in (-1, 1) for sy in (-1, 1)
    ]

    # the pod's mounting: two pilots, a vent for the SHT40, a hole for the LDR
    pod_holes = [
        Pos(POD[0], POD[1] + sy * (POD_D / 2 - 4), T / 2) * Cylinder(PILOT_D / 2, T + 2)
        for sy in (-1, 1)
    ]
    pod_holes.append(Pos(POD[0] - 9, POD[1], T / 2) * Box(VENT_W, VENT_D, T + 2))
    pod_holes.append(Pos(POD[0] + 7, POD[1], T / 2) * Cylinder(LDR_D / 2, T + 2))

    bezel = Pos(CLUSTER[0], CLUSTER[1], T + BEZEL_H / 2) * Box(BEZEL_W, BEZEL_D, BEZEL_H)
    body = body.fuse(bezel)

    controls = [
        Pos(CLUSTER[0] - 16, CLUSTER[1], T / 2) * Cylinder(BTN_D / 2, T + BEZEL_H * 3),
        Pos(CLUSTER[0] + 6, CLUSTER[1], T / 2) * Cylinder(LED_D / 2, T + BEZEL_H * 3),
        Pos(CLUSTER[0] + 18, CLUSTER[1], T / 2) * Cylinder(LED_D / 2, T + BEZEL_H * 3),
    ]

    return body.cut(*slots, *pockets, *pod_holes, *controls).clean()


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "grille-v2.stl")
    part = grille()
    bb = part.bounding_box()
    export_stl(part, str(out), tolerance=0.02, angular_tolerance=0.1)
    open_area = sum(
        (lambda s: (s[1] - s[0]))(_slot_span(i * SLOT_PITCH)) * SLOT_W for i in SLOTS
    )
    print(f"wrote {out}")
    print(f"  plate {W:.0f} square x {T:.0f}   nine slots at {SLOT_PITCH:.0f} pitch")
    print(f"  open area {open_area / 100:.0f} cm2 "
          f"({open_area / (SLOT_L * SLOT_W * 9) * 100:.0f}% of nine full slots)")
    print(f"  envelope {bb.size.X:.1f} x {bb.size.Y:.1f} x {bb.size.Z:.1f} mm")
    print(f"  volume {part.volume / 1000:.1f} cm3")
