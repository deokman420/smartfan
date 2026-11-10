# Smartfan V2 board

> [!WARNING]
> **Not fabricated, not tested, and it carries mains.** Nothing here has been built yet. Check every footprint against parts in hand (see "Before fab" below) before ordering, and do not power it from mains unless you are competent to work on mains. Licensed under CERN-OHL-S v2 ([`../LICENSE`](../LICENSE)), with no warranty.

Schematic and PCB for prototype V2: one Noctua NF-F12, a Pico 2 W, and an SHT40. This is not the two-fan V2.2 board. The circuit is described in `hardware/v2/prototype-v2.md`, `hardware/v2/build-sheet-v2.html` and `hardware/v2/wiring-v2.html`. The board changed that circuit in three ways, listed under "Changed on 2026-11-08", and those files were brought in line on 2026-11-09. Where they disagree with this README, this README wins.

Open `smartfan-v2.kicad_pro` in KiCad 10. `generate.py` is the source. It rewrites the schematic and the board. Run it with KiCad's Python:

```
& "$env:LOCALAPPDATA\Programs\KiCad\10.0\bin\python.exe" hardware/v2/generate.py
```

Do not hand-edit `smartfan-v2.kicad_sch`, `smartfan-v2.kicad_pcb` or `smartfan-v2.kicad_dru`.

The run checks itself and stops on the first problem:

1. The netlist KiCad reads from the drawn schematic must match `PARTS` pin for pin, the off-board parts included.
2. ERC. The only findings allowed are `lib_symbol_mismatch` warnings, because the symbols are embedded, renamed copies.
3. DRC with schematic parity. It has to come back empty.

Then it rewrites `smartfan-v2-schematic.pdf`, `smartfan-v2-front.svg` (front copper and silk), `smartfan-v2-back.svg` (back copper, same orientation as the front, so the ground pour and the +12 V / +5 V runs are visible) and everything in `fab/`. On 2026-11-08 in KiCad 10.0.5: 24 `lib_symbol_mismatch` warnings, DRC clean, parity clean.

`fab/` holds the Gerbers and the drill files. Two layers, 1.6 mm, 1 oz copper. The plot origin is the lower-left corner of the outline, in millimeters, and the coordinates are positive. The plated holes, and the one via, are in `smartfan-v2-PTH.drl`. `smartfan-v2-NPTH.drl` has the MOSFET tab hole (3.5 mm) and the fan header's locating peg (1.1 mm). The four mounting holes are unplated 3.2 mm circles in `smartfan-v2-Edge_Cuts.gm1`, not in the drill file. The back silkscreen is empty.

## Mains

120 V lands in J5, a Phoenix MKDS 1,5/2-5,08 screw terminal (1715721) beside the IRM-10's AC pins. Its wire entries face the mains end wall. Pin 1 takes the C8's unfused lead. Pin 2 takes the lead from the fuse holder. The C8 is not polarized, so "hot" means whichever lead the fuse is in. The silk says C8 and FUSE. The IEC C8 and the fuse holder are panel parts. They are on the schematic and not on the board.

There is no earth. Do not bond 0 V or the MOSFET tab to the box. The tab is the drain: use no screw in it, or a nylon one. If the module marks and the schematic disagree on which pin is +12 V, follow the module.

120 V copper keeps 6.4 mm from everything that is not 120 V. That is more than reinforced insulation needs at 120 V, and enough at 230 V.

- The DRC rule in `smartfan-v2.kicad_dru` enforces it as both clearance and creepage. It covers low-voltage copper, the ground pour, and pads with no net.
- DRC checks one copper layer at a time. `generate.py` also keeps 6.4 mm between layers, which DRC does not check.
- The closest approach is 6.50 mm, between the fused lead and the back ground pour.
- Line to neutral is 2.48 mm at J5's own pads and 3 mm or more elsewhere.
- A screw head or hex standoff at the nearest mounting hole is 8.3 mm from 120 V copper.

## Before fab

None of this has been checked against parts in hand. Do it before ordering.

1. **Wi-Fi in a closed aluminum box.** An unpainted die-cast 1590BB with the lid on is close to a shielded can. The Pico's only antenna is on the module, and the whole unit runs over Wi-Fi: MQTT, Home Assistant and OTA. Put a running Pico W, or a phone, in the box with the lid screwed down, and read the signal from the router or `/api/state`. If it drops out, the fix is a plastic lid or a plastic window over the antenna. That changes the box, not the board, but it decides whether this box is the right one. The board keeps all copper off both layers under the antenna and for 7 mm past the end of the module.
2. **Caliper the box.** The 96 × 70 mm outline uses Hammond's drawing figures: a flat of 103.50 × 78.00 mm between the corner bosses and a 29.75 mm inner height. Stacking 21.5 mm for the IRM-10 and 1.6 mm of board leaves 6.6 mm for standoffs with nothing to spare. Use 5 or 6 mm nylon standoffs.
3. **Insulate under the board.** The 120 V solder joints face the aluminum floor about 4 mm away. Lay a 0.4 mm polycarbonate or fish-paper sheet under the board, and trim the AC leads short.
4. **Panel parts against the board.** The C8 inlet and the fuse holder reach roughly 20–35 mm into the box from the mains end wall. The board covers most of the floor, and the IRM-10 at that end is 21.5 mm tall. Check, with the parts in the box, that neither one lands on the module or on J5's screws.
5. **Print the board 1:1 and lay the parts on it.** Check the IRM-10 pins, the R-78E, J5, the keyed fan header, and the Pico sockets.
6. **USB faces the mains end.** The build sheet puts a USB slot in the far wall, but on this board the Pico's USB end is at the mains end, beside J5. With the board in the box, unplug the cord, take the lid off, and use a right-angle micro-USB lead, or lift the Pico out of its sockets. The `www/` bundle only goes over USB.

## Changed on 2026-11-08

- **J5, the mains terminal**, is new. Before, the C8 and fuse leads were to be soldered to the IRM-10's pins on the underside, with no strain relief and facing the floor. The mounting hole at (12, 34) moved to (22, 33.5) to make room.
- **J2 is a keyed 4-pin fan header** (`FanPinHeader_1x04`; Molex 47053-1000 or any 4-pin PWM fan header). A bare pin header let the fan plug go on backwards. That puts switched 12 V on the fan's tach output and the fan's ground on +5 V.
- **The LED resistors moved to the anode side**, and **R9, 1 kΩ, is new** in series with the button. R7, R8 and R9 now sit between J4 and the Pico. A pinched grille cable, or a static spark at the button, now meets a resistor before it meets a GPIO. J4's pinout is unchanged; pins 4 and 6 are ground on the board.
- **The fan switch was regrouped** around Q1:
  - R4 runs from gate to source on the left of Q1.
  - R3 drops straight from the gate to Q2's collector.
  - R1 and R2 meet the base from below and above.
  - D2 hangs off the yellow lead.
  - R5 sits over the tach pin.
  - J4 moved beside J2.

  The holes at (80, 32) and (76, 50) moved to (80, 36.5) and (91, 63). Every net in that corner is a short hand route. The tach line crosses the fan line on the back and comes up through one via. The old board's router walked BASE 120 mm, FAN_ON 118 mm and LIGHT 317 mm. Now they are 24, 7 and 44 mm.
- **R6** sits beside J3, so the LDR line no longer crosses the board.
- **Net names and footprint library names** on the board now match the schematic. "Update PCB from Schematic" no longer renames every net or swaps every footprint.
- **The schematic** joins the Pico by labels instead of long wires, places every reference and value clear of wires, and marks junctions.

## Wiring the connectors

J2 is the NF-F12, pin 1 at the square pad. The keying wall faces the board edge. The silk names each lead's color.

1. Black, permanent 0 V. Do not low-side switch it.
2. Yellow, IRF9540 drain, switched +12 V. Never a GPIO.
3. Green, open-collector tach to GP18, pulled up to Pico 3V3 only.
4. Blue, +5 V from the buck, before the 1N5819. Never 12 V and never VSYS. A low PWM pin stops the fan even with 12 V on yellow.

These are not the V1 lead colors. On the V1 NF-A4x10, yellow was tach.

J3 is the grille sensor cable, six pins: 1 GND, 2 3V3, 3 SDA (GP0), 4 SCL (GP1), 5 LDR, 6 3V3 for the LDR. Pull-ups are on the SHT40. R6 stays on the board. SGP41 is not on this board. GP22 is free. The DHT22 does not come across.

J4 is the button and LED cable, six pins: 1 GND, 2 button, 3 green anode, 4 green cathode, 5 red anode, 6 red cathode. The button reaches GP16 through R9, the green LED GP19 through R7, the red LED GP20 through R8. The button, both LEDs, the LDR and the SHT40 are on the grille, not on this board.

J5 is mains: pin 1 the C8's unfused lead, pin 2 from the fuse.

## The rest

- The Pico sits in two 20-pin sockets. The footprint assumes it, and traces run under the module.
- VBUS, pin 40, gets no wire. Installed power is the buck into VSYS, pin 39, through the 1N5819. The band is the cathode at VSYS. With the cord in and USB unplugged, pin 39 should read about 4.5–4.8 V.
- GP17 high turns the fan on. R1, 1 kΩ, is the base resistor. R2, 100 kΩ, holds the base down while GP17 floats at boot. Do not swap them. The S8050 flat face, left to right, is E-B-C. Diode-test it before soldering.
- The Recom R-78E5.0-1.0 uses the 0.5 A footprint. The pin pitch matches. The body is 11.6 × 8.5 × 10.4 mm. Its datasheet needs no external capacitors; 10 µF on the input is only part of its EMC filter.
- The grille parts are drawn off the board and connect through J3 and J4. On the schematic, labels with the same name are one net.
