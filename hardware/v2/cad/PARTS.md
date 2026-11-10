# Smartfan V2 — printed parts

Every part is authored parametrically from the dimensions in
`hardware/v2/model-v2.html`, which models the assembly at real size. One script
per part; `python -I <script>.py` re-exports its STL. The `-I` matters: the
repo root has a `secrets.py` that shadows the stdlib module.

Each STL leaves its script **in print orientation** — Z up, centred, sitting on
z = 0 — so a slicer needs no re-orienting. Settings below are what the
estimates were made with: Bambu A1, 0.4 nozzle, 0.20 mm Strength, PETG Basic,
6 walls, 25 % infill.

## The list

The build order, the hardware and the dimensions are in
`../assembly-v2.html`. Read its first card before printing: **the duct collar
and the turning elbow do not both fit**, and which pair you print depends on
the answer.

Print **0** first. It is 3 h against the set's 33, and it answers the two fits
that cannot be checked on a screen.

| # | Part | File | Printed | Qty | Time | PETG | Support |
|---|------|------|---------|-----|------|------|---------|
| 0 | Fit coupons | `fit-coupons.stl` | ☐ | 1 | 3 h 07 m | 52 g | none |
| 1 | Duct collar | `duct-collar-v2.stl` | ☐ | 1 | 5 h 12 m | 96 g | flange face only |
| 2 | Turning elbow, half | `turning-elbow-v2-half.stl` | ☐ ☐ | **2** | 4 h 16 m ea | 67 g ea | none |
| 3 | Cartridge frame | `cartridge-frame-v2.stl` | ☐ | 1 | 7 h 35 m | 126 g | none |
| 4 | Filter frame | `filter-frame-v2.stl` | ☐ | 1 | 3 h 47 m | 63 g | none |
| 5 | Grille | `grille-v2.stl` | ☐ | 1 | 8 h 00 m | 146 g | none |
| | **the set, 1–5** | | | 6 prints | **33 h 07 m** | **564 g** | |

The coupons also come separately — `fit-coupon-collar.stl` (1 h 32 m, 28 g)
and `fit-coupon-cartridge.stl` (1 h 33 m, 24 g) — if you only want one of
them on the plate.

Grams are the slicer's filament volume at PETG's 1.27 g/cm³ — the CLI does not
resolve the filament preset's inheritance chain, so it reports weight as 0.00
and the conversion is done here.

Only #1 needs support, and only on the one face. #2, #3 and #4 slice with no
warning and 0 % overhang walls.

`turning-elbow-v2.stl` is the same elbow in one piece, for a bigger printer and
a lot of support. It is an alternative to #2, not an addition.

### The two air paths

The elbow is a toggle in the model (`shroud.visible = false`), so the viewer
never draws it next to the plain spigot — but as parts they want the same
22 mm of bore between z 53 and the box wall at 75, both at Ø104.8. Pick one:

| Build | Collar | Elbow | Extra |
|-------|--------|-------|-------|
| **Plain** | `duct-collar-v2.stl` | none | — |
| **Shroud** | `duct-collar-v2-nostub.stl` (4 h 02 m, 73 g) | `turning-elbow-v2-collar-half.stl` ×2 (4 h 52 m, 70 g ea) | +9 h 45 m, +140 g |

Regenerate either with `python -I duct_collar.py --no-stub` and
`python -I turning_elbow.py --with-collar`. In the shroud pair the elbow's tail
stops on the wall and gains a **square** 106 flange — a disc like the collar's
would reach y 169 against a box whose inside is 151 — and the same four M6
clamp elbow, steel and collar together.

## Part notes

**1. Duct collar** — carries the 100 mm flex duct out of the steel air box.
Stands on its proud end, bead near the bed. Run a brim: the first layer is a
2.4 mm ring. The flange's bed-facing side is a 19 mm annular overhang and wants
support on that face alone — inherent to a flange in the middle of a tube.
Four M6 bolts on R63 through the box wall.

**2. Turning elbow** — the bend from the fan's vertical discharge to the duct,
R/D 0.53. Mirror-symmetric, so it is cut on that plane: **print the half twice
and turn one over**. Split face down, no support needed. Glue the flat faces
(CA or solvent); the seam is not structural and not the fire barrier, the steel
box is. The bore's crown on each half droops slightly in the last ~10 mm —
inside the duct, out of sight. Lip bolts to the fan's own 105 mm screw square.

**3. Cartridge frame** — the square carrier the 120 × 25 fan sits in, which
slides out downward on its rails. Prints standing up, as modelled. The fan
lands on four corner seats and takes M4 self-tappers into 3.4 pilots. The
rails carry a 45° gusset underneath, which is what keeps this one support-free.

**4. Filter frame** — the tray that holds the 170 square pad on the room side
of the ceiling line. Prints plate-down, no overhangs anywhere. The pad drops
into a 170.6 pocket; a 158 window with two bars each way is the air path.

**5. Grille** — the only part of this build anyone in the room sees, and the
one carrying the controls and the sensors. Prints void-face-down: the bezel
stands proud on top, the slots go through, the magnet pockets bridge a 10 mm
roof. Nine slots at 16 pitch, 100 cm² open. Three of them stop short of the
cluster and the pod, which costs 7 % of the open area and is the difference
between a button bezel and a hole where one should be.

**0. Fit coupons** — offcuts of #1 and #3, cut out of the real parts by
`fit_coupons.py` rather than drawn again, so they cannot drift from what they
stand in for. The collar coupon is its proud end with the clamp bead: put the
actual flex duct and clamp on it. If the duct will not go on, change `WALL` in
`duct_collar.py` before the 5-hour print, not after. The cartridge coupon is
one corner — seat, pilot, wall and rail: check an M4 self-tapper holds in the
3.4 pilot and the fan's corner clears the 5.7 wall.

### Hardware to have in

- 4 × M6 × 20 + nuts — collar to the air box wall
- 4 × M4 × 16 — elbow lip to the fan's 105 square
- 4 × M4 self-tapping × 16 — fan down onto the cartridge seats
- CA or PETG solvent — the elbow's two halves
- a 170 × 170 × 10 lint pad, 10 PPI

## Where the model had to become a part

The viewer draws surfaces with no thickness, so a few things are decisions
rather than transcriptions. Each is argued in its script's header; in short:

- **Wall thickness goes outward, everywhere.** The bore stays 100 on the collar
  and the elbow. This fan has 0.155 in w.g. in total and the easy-duct case
  already spends 0.10, so bore area is not available to give back.
- **The elbow's crown lands at y = 149.4** against an air box whose inside is
  151. That 1.6 mm is the model's own margin. Do not thicken that wall.
- **The cartridge's walls are 5.7, not the modelled 10.** At 10 the 132 outer
  leaves a 112 opening and a 120 fan does not fit. The outer stays 132.
- **105 mm screw square** on the cartridge seats, so the fan is held by its own
  fixings rather than new ones. The elbow's lip has the same four holes, but
  **do not use them**: bolting the lip to the fan would take the elbow out with
  the cartridge, and the cartridge dropping out on its own is what the whole
  layout is for. They are left over from a wrong idea and carry nothing.

## Build notes

Scripts need `build123d`; this machine has it in a scratchpad venv rather than
the system Python. Note that build123d 0.13's `+` does **not** fuse overlapping
solids — it returns the complement — so every script uses explicit
`fuse`/`cut`/`intersect`.

Two things about slicing from Bambu Studio's command line, both the same bug:
**it does not follow a preset's `inherits` chain.** So the filament preset
arrives with `filament_density = 0` and reports weight as 0.00, and the A1
machine preset arrives with no `printable_area` at all — which silently shrinks
the bed and makes anything over ~200 mm refuse to place ("no object fully
inside it"). The grille is 210 and hits exactly that. The fix is a machine json
with `printable_area` written in directly; the GUI resolves inheritance
properly and needs none of this.

`viewer.html` shows any of the parts in a browser with a print bed under it:
serve this folder (`python -m http.server 8777`) and open it. Bambu Studio also
opens the STLs directly, and will slice from the command line.
