# Prototype V2

**Status:** design locked, nothing bought, nothing modeled.
**Revised:** 2026-09-28 — the DHT22 is out. V2 wires an SHT40 instead, so no
one-wire humidity sensor is ever soldered into hardware that leaves the bench.
**V1:** the commissioned USB breadboard. Pico 2 W, NF-A4x10 5 V on VBUS, S8050 low-side switch. Leave it working. It is the reference the new build has to beat.
**Written:** 2026-09-28
**Housing follow-on:** [`chassis-and-housing.md`](chassis-and-housing.md)
**Order:** [`shopping-list-v2.html`](shopping-list-v2.html) — prices and store links. Open in a browser.
**Build sheet:** [`build-sheet-v2.html`](build-sheet-v2.html) — schematic, wire order, and meter checks. Open in a browser.
**Hookup:** [`wiring-v2.html`](wiring-v2.html) — the bench drawing, one wire at a time, in the style of the V1 breadboard sheet.
**Board:** [`README.md`](README.md) — the KiCad schematic and two-layer PCB, finished 2026-11-08 and not yet ordered. Where this note and the board disagree, the board wins. It changed four things on 2026-11-08, folded in below: mains lands in a screw terminal J5, the fan plugs into a keyed header J2, the LED resistors moved to the anode side, and the button gained a 1 kΩ series resistor R9.
**Variant:** V2.2, not published yet — the same unit with two fans and one merging shroud. Nothing vertical changes; it is a fork taken after the duct is measured, not instead of this note.

"Prototype V2" is the usual name. V1 is the bench unit that already runs. V2 is the first build meant to leave the bench: a real extractor fan, mains into the electronics enclosure, 12 V and 5 V made on the device, and humidity on I²C instead of a one-wire part. Ordering the parts below is the start of the spend. This file is the start of the design.

Industry stage-gates (EVT, DVT, PVT) are a company's process for a product it intends to manufacture. They add nothing until there is a second unit and a test plan with pass/fail numbers. Do not rename this to EVT.

---

## What changed, in one paragraph

120 V AC lands in the electronics box and stops at a power module. The fan is a 12 V Noctua NF-F12 industrialPPC-2000 IP67 PWM. A buck makes the Pico's 5 V, so the installed unit is not powered by a USB cable. The S8050 no longer switches the fan's ground — it drives a high-side MOSFET on the fan's 12 V lead, because that ground has to stay tied to the Pico or the tach pin floats up toward 12 V. And humidity comes from an SHT40 on I²C rather than the DHT22.

## Three new parts. One of them needs code

The two power parts are the **IRF9540** (high-side switch) and the **R-78E5.0-1.0** (12 V to 5 V). Neither runs code, and neither needs a driver written for it.

The third is the **SHT40**, and it does: `sht4x.py`, plus the one function in `main.py` that reads climate. That is a deliberate change of mind. An earlier draft of this note had V2 running the V1 image untouched, with the DHT22 carried over and swapped later, when the SGP41 arrived.

The reason it changed: the swap was always going to happen — [`sgp41-odor-sensing.md`](../../docs/sgp41-odor-sensing.md) makes the case, and its own rollout puts the humidity sensor first — and V2 is the moment the sensors get soldered into something that goes in a ceiling. Wiring a part in order to remove it later means doing the sensor wiring twice, the second time up a ladder. Soldering a DHT22 into V2 is work with a known expiry date.

### What that costs, and how to pay it

V2 stops being "the V1 image, unchanged", and that premise was worth something: when V2 first powers up, anything odd is hardware, because the code is already proven. Keep it by moving the swap earlier rather than into V2's own commit:

1. Write `sht4x.py` and swap `read_climate` **on the V1 breadboard**, which is on the bench and working. One function, unchanged contract, verifiable against the sensor-fault tests that already exist. The driver and `tests/test_sht4x.py` are written. `main.py` still constructs the DHT22: the unit in the bathroom is that sensor, and the swap waits until an SHT40 is the part on the bus.
2. Let the baseline re-learn — about fifteen minutes at `BASELINE_A = 0.002`. An SHT40 reads 1–3 % away from where the DHT22 sat, and `excess_rh` is meaningless until it settles.
3. Run V1 on it for a few days.
4. Then build V2 against a known-good image. Just a newer one.

Keep `SAMPLE_S = 2` through all of this. It is documented as the DHT22's floor and is no longer forced by the part, but `BASELINE_A`, `STUCK_S`, `SPINUP_S` and the tach window are all reasoned against that cadence. A faster loop is a separate change, afterwards, with `BASELINE_A` scaled to match.

GP17 high still means fan on. `TACH_PPR = 2` still matches a Noctua. `MIN_RPM = 800` still sits under this fan's 2000 rpm. The fan control path does not change at all.

Three config entries become lies the day it is wired, and should be rewritten in the commit that builds V2, not before:

- `PIN_DHT = 22` is deleted. Humidity moves to `PIN_SDA = 0` / `PIN_SCL = 1`, and GP22 is free.

- `PIN_TACH` says the tach is yellow. On this fan yellow is +12 V and the tach is green.
- `STUCK_S` was timed on an NF-A4x10, which coasts 2–3 s. A 120 mm rotor coasts longer. Remeasure before trusting the 12 s stuck-on check. The failure it is looking for also moves: a yellow lead landed on raw 12 V bypasses the MOSFET the way a ground lead on the collector used to bypass the S8050.

A firmware project starts later, on purpose, and is not part of V2:

- Driving the blue PWM pin for a trickle / boost law. V2 ties that pin to 5 V so the fan runs at full speed whenever GP17 is on. The fan stops if PWM is low, so the tie is required, not optional.
- The SGP41 odor path, already costed in [`sgp41-odor-sensing.md`](../../docs/sgp41-odor-sensing.md). V2 wires the humidity half of that note and stops there. If the gas sensor comes later it chains at the grille, one QT cable between the two breakouts, onto the bus J3 already carries. There is no second socket on the board, and the run does not get rebuilt.

## Fan

Noctua NF-F12 industrialPPC-2000 IP67 PWM. 120 × 120 × 25 mm, 105 mm screw pitch, 2000 rpm, 1.2 W max, 0.1 A max, IP67, frame UL 94 V-0.

| | Free air | At 0.1 in w.g. | At 0.25 in w.g. | Max pressure |
|---|---:|---:|---:|---:|
| This fan | 72 CFM | ~20 CFM | 0 | 3.94 mm H₂O (0.15 in) |
| NF-F12 industrialPPC-3000 (the step up, if a measurement comes up short) | 110 CFM | ~81 CFM | ~26 CFM | 7.63 mm H₂O |
| Panasonic FV-08VQ5, for scale | — | 80 | 62 | centrifugal bath fan |

The 0.1 / 0.25 in columns are read off Noctua's published curve. HVI rates bath fans at those two points. Expect about a continuous-code 20 CFM on a short easy duct, and less if the flex is long or crushed. The 3000 rpm part fits the same cartridge and the same screws. It is louder (43.5 dBA against 29.7).

### The filter reverses this, and takes the fan with it

An earlier version of this note said no lint filter, and the reasoning was sound: this fan has 0.155 in w.g. of pressure in total and the easy-duct case already spends 0.1 of it. A filter spends the margin the duct needs.

**The housing brief now requires a filter**, on the room side of the grille where it can be washed. That does not make the arithmetic go away, it just decides which side of it to be on. A clean pleated pad costs roughly 0.02–0.05 in w.g.; a loaded one costs more. On the iPPC-2000 that is most or all of what is left at 0.1 in, and the fan is at zero by 0.155.

So the filter and the fan are one decision, not two: **if the filter stays, the fan should be the NF-F12 iPPC-3000.** Same 120 × 25 mm pocket, same 105 mm screw pitch, same 12 V, same 0.1 A class of load, and nothing in the electronics changes. 110 CFM free air and 7.63 mm H₂O (0.30 in) instead of 72 and 3.94. The cost is noise: 43.5 dBA against 29.7, which in a bathroom at night is the thing that will be noticed.

That swap is not made in this note. It is the first thing a measured duct should settle.

### Which way it points

The fan assembly is oriented by the framing, not by the room. A 100 mm duct
cannot cross a joist — a bored hole may be roughly a third of the member's
depth, about 46 mm on a 2x6 — and there is no room above the joists in a
152 mm void. So the spigot leaves along the bay, parallel to the joists, with
2 in of collar proud of the box for the flex duct and its clamp. See
[`model-v2.html`](model-v2.html) and the housing note.

### Wire colors

These are not the V1 colors. The build sheet's "yellow is tach, red is 5 V" puts 12 V on GP18 if it is copied onto this fan.

| Pin | Color | Goes to |
|---|---|---|
| 1 | Black | Pico GND, permanently. Also the IRM-10 0 V pin |
| 2 | Yellow | IRF9540 drain. +12 V switched. Never a GPIO |
| 3 | Green | Existing tach circuit: GP18, 10 kΩ to 3.3 V |
| 4 | Blue | Pico 5 V rail, so PWM reads 100%. Never 12 V. The PWM input is a 5 V pin |

## Power, and where 120 V stops

Mains enters the electronics enclosure through an IEC C8 inlet (figure-8, no earth pin). The supply is Class II, so there is no protective earth to land. A fuse, at the value in Mean Well's IRM-10 installation manual, sits in the hot lead. Follow that manual for clearances around the module. The module is board-mount and has to live inside an enclosure; the enclosure is what makes the mains side a finished box.

| Part | Role |
|---|---|
| Mean Well IRM-10-12 | 85–305 V AC to 12 V DC, 0.85 A, UL 62368-1, Class II. Fan uses 0.1 A. The Pico's 5 V is about another tenth of an amp at the 12 V rail |
| IEC C8 inlet | Mains into the electronics box only |
| Fuse and holder | Per the IRM-10 installation manual |
| J5, Phoenix MKDS 1,5/2-5,08 (1715721) | Two-way screw terminal on the board, beside the IRM-10's AC pins. Pin 1 takes the C8's unfused lead, pin 2 the lead from the fuse holder. Nothing is soldered to the module's pins by hand |
| Hammond 1590, or another metal box | Electronics. The fan does not live here |
| Air box, folded galvanized steel | The fan, the cartridge rails and the duct collar. Metal because it sits in the ceiling plane with a motor in it. Not printed, and not the same box as the electronics |
| IRF9540 | P-channel MOSFET, source on +12 V, drain to fan yellow. Gate rated ±20 V, so −12 V of drive is inside its rating |
| 100 kΩ | Gate to source. Holds the fan off while GP17 is high-impedance at boot |
| 100 Ω | S8050 collector to the gate |
| S8050, 1 kΩ base, 100 kΩ base pulldown | Already on the V1 board. Moved from "fan ground switch" to "MOSFET gate driver". GP17 high still turns the fan on |
| 1N4148 | Moved to sit across the fan: band on the drain (yellow), anode on fan ground |
| Recom R-78E5.0-1.0 | 12 V to 5.0 V, 1 A, for the Pico. Sized for the Pico, not for a WS2812 ring |
| 1N5819 | Buck into VSYS, so a USB programming cable and the buck cannot feed each other |
| Sensirion SHT40, Adafruit 4885 | Humidity and temperature on I²C, 0x44. Replaces the DHT22. Carries its own regulator, level shifting and bus pull-ups |
| J2, keyed 4-pin fan header (Molex 47053-1000) | The NF-F12's own plug. Keyed, so it cannot go on backwards and put switched 12 V on the tach |
| J3, 6-pin 2.54 mm socket | Grille sensor cable: 1 GND, 2 3V3, 3 SDA (GP0), 4 SCL (GP1), 5 LDR, 6 3V3 for the LDR. The 10 kΩ stays on the board |
| J4, 6-pin 2.54 mm socket | Button and LED cable: 1 GND, 2 button, 3 green anode, 4 green cathode, 5 red anode, 6 red cathode. Pins 4 and 6 are 0 V on the board. The resistors stay on the board, between the Pico and J4: R9 1 kΩ to GP16, R7 330 Ω to GP19, R8 330 Ω to GP20. A pinched cable or a static spark at the grille meets a resistor before a GPIO |
| Two 20-pin 2.54 mm sockets | The Pico plugs into the board rather than being soldered to it, so it can come out for USB |

```
120 V AC → C8 → fuse → J5 → IRM-10-12
                         ├─ +12 V → IRF9540 → yellow
                         ├─ +12 V → R-78E5.0 → 1N5819 → Pico VSYS
                         └─ 0 V  → Pico GND, fan black
GP17 → 1 kΩ → S8050 → 100 Ω → MOSFET gate
5 V → blue
green → 10 kΩ to 3.3 V → GP18
J3 → SHT40 and LDR, on the grille
GP16 → 1 kΩ, GP19 → 330 Ω, GP20 → 330 Ω → J4 → button and both LEDs, on the grille
```

The air box receives four conductors: yellow, black, green, blue. The button, both LEDs, the LDR and the SHT40 mount on the room-side grille, still on 3.3 V. The button cable leaves the grille through a cable fitting and enters the 1590BB through a second one, on the end wall toward the button. The sensor cable has its own fitting on the opposite end. They do not move into the duct — a humidity sensor in the extract stream reads the air that has already left.

USB remains the programming port. It is not the installed power source. On the board the Pico's USB end faces the mains end of the box, beside J5, not a slot in the far wall: unplug the cord, take the lid off, and use a right-angle micro-USB lead, or lift the Pico out of its sockets.

## What V2 does not include

- A printed electronics tray. The Pico, the IRM-10, the buck and the MOSFET stay in the metal box, outside the airstream.
- Mains in the air box, or 120 V on the fan.
- PWM speed control, and any firmware to do it.
- The SGP41. J3 carries SDA and SCL; the sensor is a later purchase. There is no second socket on the board. The breakout's spare QT connector, if it is used, stays at the grille.
- The RGB ring, slide-out rails, a blind-mate connector. Those stay in the housing plan.
- Any change to `SAMPLE_S`, or to the loop cadence built around it.
- Nothing: a washable filter is now in the housing brief, on the room side of the grille. See above for what it costs.

## Model, when this list is accepted

The first model is the air path only, built around this fan's real body once one is in hand:

- 120 × 25 mm pocket, 105 mm screw pattern, pads so the frame is not hard against the plastic
- transition to a 4 in spigot, about 99 mm OD to slip inside flex duct
- a way out for the four wires
- a gravity flap in the collar if the damper is printed rather than bought with the duct fitting

Print the plate in PETG to measure airflow. A plate that stays in a ceiling is sheet aluminum. The duct is metal and terminates outdoors. The 3D model is not the mains enclosure.

Still blocking the ceiling geometry, and unchanged by this decision: joist depth, the existing hole, duct length and bends, and whether the ceiling is fire-rated. Measured on one visit. Listed in the housing note.
