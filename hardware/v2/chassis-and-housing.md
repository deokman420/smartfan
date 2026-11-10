# Chassis, housing and a 4 in duct

**Status:** housing plan for Prototype V2. No parts bought, nothing modeled yet.
**Fan and power:** decided in [`prototype-v2.md`](prototype-v2.md). NF-F12
industrialPPC-2000 IP67, 120 V into the electronics box, V1 firmware unchanged.
**Supersedes:** the one-line Phase 3 note in `README.md:189` — "mains relay
for a real ceiling extractor, in a proper enclosure".
**Written:** 2026-09-27. Fan and power pointed at V2 on 2026-09-28.
**Dual-fan variant:** V2.2, not published yet, reworks §8c for two
fans into one shroud. The turn radius is unchanged; everything else in this
file still applies.

---

## The thing that decides everything else

`README.md:21` lists the fan as a **Noctua NF-A4x10, 5 V, 50 mA**. That is a
40 mm computer fan. It is a perfectly good bench stand-in and it is not a
bathroom extractor — it moves on the order of 5 CFM into free air and
essentially nothing against a duct.

A bathroom on a 4 in duct wants **50 CFM intermittent** (ASHRAE 62.2; UK Part F
asks 15 l/s, about 32 CFM). The chassis is the housing for the V2 fan, the
NF-F12 industrialPPC-2000 IP67, not for the 40 mm fan on the bench. That fan
is chosen and not yet in hand. Model from the part, not from the datasheet
drawing.

### Static pressure is the trap

Every fan's headline CFM is **free air** — no duct, no bends, no cap. A 4 in
run with a wall or roof terminal typically presents 0.1–0.25 in w.g. Ordinary
axial fans collapse under that; some deliver near zero. Choosing by headline
CFM produces a chassis that whispers and extracts nothing, and the controller
will faithfully run it for 45 minutes wondering why the humidity is not moving.

### The tension, stated honestly

You asked for a motor that can be replaced without tearing out the guts. That
is a **commodity** requirement: the part must still be buyable, in the same
dimensions, in ten years. It pulls against the static-pressure requirement,
which points at radial blowers and proprietary duct modules that are neither
standard nor re-buyable.

| Route | Static pressure | Replaceable in 10 years? |
|---|---|---|
| 120 mm high-static PC fan | Adequate, if chosen deliberately | **Yes.** 120 × 120 × 25 mm, 105 mm screw pitch, 4-pin — the most standardised object in consumer hardware |
| Radial / centrifugal blower | Excellent | No. Model-specific mounting, gone in two years |
| Commercial inline duct module | Excellent | Sealed unit; you replace the whole thing, which is the opposite of the ask |
| Mains extractor + relay | Excellent | Yes, but gives up PWM, tach and the whole control surface |

### Decision

**NF-F12 industrialPPC-2000 IP67, in a plenum that transitions 120 mm square
to 4 in round.** Parts, wire colors, and the 120 V boundary are in
[`prototype-v2.md`](prototype-v2.md). This file keeps the housing.

The earlier draft pointed at the industrialPPC class for its ~7.6 mm H₂O
figure. That figure is the 3000 rpm part. The fan actually chosen is the
2000 rpm IP67: 3.94 mm H₂O, about 20 CFM at 0.1 in w.g., IP67 so it can live
in the damp air path. The 3000 rpm fan fits the same 105 mm screws if a
measured duct comes up short. It is the spare decision, not the plan.

What this buys:

- The replaceable part is a standard 120 × 25 mm fan. Any later 120 mm fan
  fits the same cartridge.
- The tach logic already written works unchanged. `TACH_PPR = 2`.
- The blue PWM lead is tied to 5 V. V2 stays on/off. A trickle/boost law is a
  later firmware project, written up as an open question below, and it is not
  part of building V2.

What it costs: the area step. A 120 mm fan face is ~113 cm²; a 4 in round is
~81 cm². The transition should still be a cone rather than a flat plate with a
hole in it — but this is the *cheap* part of the design, for a reason worth
being precise about (see **Contractions are forgiving** below). The expensive
losses are downstream, in the duct and the terminal.

**Consequence for the electronics:** handled in the V2 note. 12 V from an
IRM-10-12 in a metal box outside the duct, a buck to 5 V for the Pico, and the
S8050 demoted to driving an IRF9540 on the yellow lead. The printed housing
does not contain the Pico or the mains module.

---

## What else it should have

Answering the open question, ordered by what actually bites.

### 1. A backdraft damper — not optional

Without one, a 4 in duct is an open hole to outside. You get cold air falling
into the room all winter, insects, and on a shared stack, next door's air. A
gravity flap in the duct collar is a printed part plus a hinge pin, and it is
the difference between a fan and a vent that occasionally blows.

Design note: the flap must open at the fan's pressure and close under its own
weight. That means light — thin TPU or a printed flap with a film hinge —
and it must not slam and chatter, which means a soft stop.

### 2. The RGB ring will blind the LDR

This is the sharpest interaction in the whole design, and it is already
documented in the code you have. `main.py:216` blanks **both status LEDs for
5 ms before every light reading**, with the comment that the red LED "will
otherwise read as 'the bathroom light is on', which latches the fan
permanently". That is two indicator LEDs. An RGB ring is orders of magnitude
brighter and sits in the same housing.

Three mitigations, and I would do all three:

- **Blank the ring during the read.** The existing pattern, extended. 5 ms of
  darkness every 2 s is invisible to a human eye and free.
- **Optically isolate the LDR** with a printed light pipe or shroud that sees
  the room and not the ring. Geometry solves this better than firmware.
- **Re-calibrate.** `LIGHT_ON`/`LIGHT_OFF` are documented as measured in the
  actual bathroom in the actual mounting position (`config.py:17-26`). New
  housing means those numbers are void and `calibrate.py` must be re-run.

If the ring still defeats the LDR after all three, the fallback is a different
occupancy sensor — a PIR or an mmWave presence module — but that is a
significant change to the control law and should be a last resort, not a
starting assumption.

### 3. Condensation, and where the water goes

The housing straddles a warm wet room and a cold void. Warm moist air will
condense on cold plastic. That water has to go somewhere, and "onto the
electronics" is the default unless the geometry says otherwise.

- Electronics compartment **beside or above** the airflow path, never beneath it.
- A drip lip and a sloped floor that returns condensate into the duct, where
  it was going anyway.
- Gasket the room-side plate — closed-cell foam tape in a printed channel.
- The duct itself should be insulated in a cold void. Not a printed part, but
  it belongs on the shopping list.

### 4. Material, heat and fire

**Decided: the air box is folded galvanized sheet steel. The collar, the
grille and the fan cartridge are printed.**

The split follows what each part is for. The air box is the thing sitting in
the ceiling plane with a motor inside it, so it is the fire barrier and it is
metal — 26 gauge is what every commercial housing uses, and there is no reason
to be cleverer than that. The collar, the grille and the cartridge are
aerodynamic and serviceable parts; they are not the barrier, and printing them
is what makes the fan a replaceable item rather than a soldered-in one.

For the printed parts, PLA softens around 60 °C and has no business in a
ceiling void. **PETG is the minimum**; ASA if any part sees a hot roof space.

A printed part penetrating a ceiling is a building-code question, not a
printing question. If the ceiling is fire-rated, the penetration needs proper
fire-stopping and the printed collar must not be the thing providing it. That
is still the unknown to settle before modelling anything that crosses the
plane, and it is now a question about the collar specifically.

### 4b. What "UL listed" can and cannot mean here

Worth stating precisely, because it is the kind of claim that is easy to make
loosely and expensive to be wrong about.

**A listing applies to a complete assembly tested as a whole, by a Nationally
Recognised Testing Laboratory.** Three consequences follow, and none of them
are avoidable by choosing a material:

1. **A self-built unit is not listed**, whatever it is made of. Bathroom fans
   are listed under UL 507; that mark belongs to the manufacturer's tested
   assembly, not to the concept.
2. **Modifying a listed housing voids its listing.** Buying a builder-grade
   fan and swapping the motor for an NF-F12 is a reasonable way to get a steel
   box with joist ears and a collar already on it — but the result is no
   longer a listed appliance, and saying otherwise would be false.
3. **Listed components inside a metal enclosure is the honest claim**, and it
   is a real one. The IRM-10-12 carries UL 62368-1 and is Class II; the fan
   frame is UL 94 V-0; the enclosure is metal; the mains is fused, confined to
   one box, and never leaves it. That is a defensible description of a
   one-off, and it is the sentence to use.

If this ever stops being a one-off, listing is a project with a real cost and
a real calendar — an NRTL, a submitted sample, construction review, and a
factory inspection. **ETL (Intertek) is usually the cheaper and faster door
than UL** and is accepted wherever UL is; that is worth knowing before anyone
promises "UL listed" to someone who will check.

What none of this changes: the mains side is already designed the way a
listed product would be. That was the point of stopping 120 V at a module
inside a metal box.

### 5. Acoustic decoupling

The fan is the noise source; a large flat printed housing is a soundboard
bolted to it. Two rules:

- **No hard plastic-to-plastic contact** between fan and plenum. TPU grommets
  or the fan's own anti-vibration mounts, in captive pockets.
- **Rib every flat panel.** A 100 × 100 mm unribbed wall will drum at the
  blade-pass frequency and turn a quiet fan into an audible one.

### 6. Everything must come out downward

You cannot easily get into most lofts, and the whole premise of the build is
that a fan dying in a ceiling should not need a ladder and an afternoon. So:
fan cartridge, electronics tray and sensor pod all release and drop through
the ceiling aperture, from the room side, with the duct and the mains-side
wiring undisturbed.

This is the requirement that shapes the geometry more than any other. Design
the aperture first and everything else to pass through it.

### 7. Blind-mate connectors

A cartridge swap must be "slide out, slide in" — not "reach above your head
and unplug three connectors by feel". A keyed connector on a floating mount,
so the act of seating the cartridge makes the connection, with enough compliance
to absorb print tolerance.

This is also what makes the replaceable-motor promise real rather than
nominal. A cartridge you have to unwire is not a cartridge.

### 8. Sensors in the grille

The SHT40 and SGP41 must sample **the room**. Mounted in the extract stream
they measure air that is already leaving, which is the one sample that cannot
tell you whether the room is done. The existing build sheet says this; the
chassis has to physically enforce it — a vented pod on the room side, out of
the direct path, and away from LED heat.

**Decided: the pod is built into the grille.** Room air and room light, by
construction, and the LDR ends up on the one surface that actually faces the
room. Two consequences, both of which are new work:

- **The grille now carries wiring, so it needs plugs, not solder.** A
  magnetic grille that cannot be taken off without a soldering iron is not a
  magnetic grille. Two cables, matching the board. J3 is the sensor cable:
  GND, 3V3, SDA, SCL, the LDR, and a second 3V3 so the LDR does not splice.
  J4 is the button and LED cable: GND, the button, green anode, green cathode,
  red anode, red cathode. The 10 kΩ, both 330 Ω and the button's 1 kΩ stay
  on the board, the last three between the Pico and J4, so a pinched cable
  meets a resistor before a GPIO. The button and both LEDs sit on the room face, which is what makes
  `PIN_BUTTON` reachable in item 9. The button cable leaves through a fitting
  on the back of the grille and enters the 1590BB through a fitting on the end
  wall toward the button. The sensor cable has its own fitting on the opposite
  end. The face toward the fan is crossed by a hanger bar, so neither gland
  goes there.
- **It is a keying problem.** The grille can go back on rotated. Either the
  connector is captive and keyed, or the pod sits on the diagonal so any
  rotation still leaves it out of the airstream.

### 8b. The filter, and what it costs

A filter is in the brief, and it is worth stating plainly what it spends,
because the number is small and the margin is smaller.

The iPPC-2000 has **0.155 in w.g.** in total, and about 20 CFM left at 0.10 in.
A clean pleated pad is roughly 0.02–0.05 in w.g. and a loaded one is worse, so
on this fan a filter is most or all of the remaining margin — and a forgotten
filter is a fan that moves nothing at all while still reporting a healthy rpm,
which is the worst failure this system can have, because the tach cannot see it.

Two answers, and the second is the real one:

1. **Keep the iPPC-2000 and treat the filter as a service item.** The runtime
   counter already exists; a filter reminder is a threshold on it, not new
   hardware. Fragile, because it depends on someone acting on a notification.
2. **Step up to the iPPC-3000.** 0.30 in w.g. and 110 CFM free air, same
   120 × 25 pocket, same 105 mm screws, same 12 V. The filter then costs
   margin that exists. It is louder — 43.5 dBA against 29.7.

The filter also decides where it sits: **room side of the ceiling line.** In
the void, the stack is grille + filter + cartridge + duct = about 150 mm of a
152 mm void, which is a coincidence rather than a tolerance.

### 8c. The turn out of the box, and the elbow that softens it

The fan blows **up** and the duct leaves **sideways**, so every cubic foot the
fan moves changes direction by ninety degrees inside the air box. That turn was
never drawn until the V2 model drew the air, and what it shows is a jet hitting
the lid, spreading along it, and finding the duct sideways — with two slow
eddies in the far corners that the fan is paying for and getting nothing from.
(`model-v2.html`, **Airflow** and **Turning shroud** in the Show panel.)

**The size of the fix is decided, not chosen.** That is the useful part. Take a
bend joining the fan centreline to the duct centreline. In the model's
coordinates the fan blows along `z = 0` and the duct leaves along `y = 97`, so
for a centreline radius *R* the centre of curvature is at `(z = R, y = 97 − R)`.
Two consequences:

- The outer wall tops out at **y = 147** and the inner wall bottoms at **y = 47**
  *whatever R is* — exactly the 100 mm the bore already occupies. The height
  always works.
- The inlet does not scale. It sits at `y = 97 − R`, and it cannot drop below
  the cartridge top at **y = 44**.

That pins **R ≈ 53 mm**, so **R/D ≈ 0.53**. A tight elbow, not a good one — a
good one (R/D ≥ 1) would have to start inside the fan.

**What it is worth.** A plenum turn of this kind runs about 1.2–1.5 velocity
heads; a radiused elbow at R/D 0.5 runs about 0.7. So the part buys back a bit
over half a velocity head:

| | 50 CFM | 100 CFM |
|---|---|---|
| Velocity in the 100 mm bore | 590 fpm | 1180 fpm |
| One velocity head | 0.022 in w.g. | 0.087 in w.g. |
| Saved by the elbow (~0.6 VH) | **0.013 in w.g.** | **0.05 in w.g.** |
| Against the fan's total | 8 % of the iPPC-2000's 0.155 | 17 % of the iPPC-3000's 0.30 |

For one printed part that is a good trade, and it is the same order as the
filter it has to pay for — see *The filter, and what it costs* above.

**It is printed, and that is consistent.** It is not the fire barrier; the steel
box is. Same argument that keeps the collar, the grille and the cartridge
printed while the box stays folded galvanized steel.

**Constraints it has to respect:**

- **Everything still comes out downward.** The elbow stands on the cartridge top
  plane and goes up from there, so the cartridge drops out without touching it.
  It is captive to the box — screwed or trapped, never friction-fit, because a
  part that can be sucked into a duct eventually is.
- **No bellmouth at the bore.** The 100 mm hole leaves about 3.5 mm of wall above
  and below it. There is room to radius the **sides** of the entry and nowhere
  else. Do not draw a ring that cannot exist.
- **A square mouth over the fan.** The fan is 120 square, the bend is 100 round;
  the air that misses the mouth recirculates. The mouth is the transition.
- **Print it to be smooth on the inside** — 1.6–2 mm wall in PETG, seams placed
  along the flow, no supports inside the bore — and gasket it to the box wall
  around the spigot with foam tape. A leak path at that joint gives back the
  pressure the shape just earned.

**The honest caveat.** These streamlines are sketched, not solved: no CFD was
run, and the loss coefficients are handbook figures for duct fittings, not for
this part. The gain is also second-order next to the duct run and the filter, so
it must not be spent twice in the same budget. It is a drop-in that can be
printed and fitted after the box exists, which makes it the right thing to
defer if the schedule gets short — and the wrong thing to skip.

### 9. Access to the button

`PIN_BUTTON` is boost, relearn on a long press, and the safe-mode entry that
`config.py:88` calls "not optional once the controller is mounted". The button
is on the grille's room face, on the J4 cable, so it is reachable with the
grille on. Pulling the magnetic grille off, without tools, is the other way in.

### 10. Smaller things that still matter

- **Removable grille**, magnets not screws, because it will be cleaned.
- **A washable filter**, on the room side of the grille, reachable the moment the grille drops. It is not free: see *The filter, and what it costs* below. The grille
  being easy to wipe is worth more.
- **Strain relief and a service loop** on every cable crossing the aperture.
- **Mounting to joists** where possible; spring clips into plasterboard as the
  fallback, sized for the real weight including the fan.
- **Print-tolerance coupons** — a 4 in spigot ring and a fan-boss test piece,
  printed and fitted, before committing eight hours to the main body.

---

## RGB specifics

WS2812B ring. The Pico's PIO is genuinely the right peripheral for this, and
one GPIO drives the whole ring.

| Concern | Resolution |
|---|---|
| Logic level | 3.3 V data into a 5 V part is marginal. Use a 74AHCT125 as a level shifter |
| Power | 16 LEDs at full white is roughly 1 A. **Not from the Pico's regulator**, and not from the V2 R-78E5.0, which is sized for the Pico. A ring gets its own 5 V budget |
| Brightness | Cap it in firmware. Full white is never wanted and is most of the power budget |
| The LDR | Must be blankable. See above — this is a hard requirement on the driver, not a nice-to-have |
| Diffusion | Printed translucent ring, 1.2–1.6 mm wall in natural PETG. Direct-view LEDs in a bathroom ceiling are unpleasant |

Worth having it earn its place beyond decoration: fault red, boost blue,
running green maps onto states the controller already publishes, and a dim
warm nightlight on low occupancy is the feature people actually use.

---

## Parts to model

Eleven printed parts. Listed in the order I would model them, which is the
order in which one part's decisions constrain the next.

| # | Part | Notes |
|---|---|---|
| 1 | **Duct collar** | 4 in spigot, ~99.5 mm OD to sit inside 100 mm flex duct, bead for the clip |
| 2 | **Backdraft damper** | Flap, hinge pin, soft stop. Prints with the collar |
| 3 | **Turning elbow / transition** | 120 mm square mouth over the fan to 4 in round at the wall, through a 90° bend. R/D is pinned at ~0.53 by the void — see *The turn out of the box* above. The aerodynamic part |
| 4 | **Fan cartridge** | Holds a standard 120 × 25 mm fan on TPU isolators. The replaceable unit |
| 5 | **Cartridge rails + latch** | Slide and lock from below. Keyed so it cannot go in rotated |
| 6 | **Electronics tray** | Deferred. V2 puts the Pico, IRM-10, buck and MOSFET in a metal box outside the duct. A printed tray is not the mains enclosure |
| 7 | **Blind-mate connector mount** | Floating, compliant, keyed |
| 8 | **Sensor pod** | Vented, room-side, out of the extract path |
| 9 | **LED carrier** | WS2812 ring, standoffs, wire route |
| 10 | **Diffuser ring** | Translucent, 1.2–1.6 mm |
| 11 | **Room-side grille** | Magnetic, button access, gasket channel |

Plus two test coupons — spigot ring and fan-boss — printed first.

## How to build them

**build123d.** Python, code-first, parametric, OCCT kernel — so real fillets
and STEP export, not just meshes.

The reasoning is the same one that put every threshold in `config.py` instead
of in someone's head: a chassis defined by a script diffs, reviews and
re-renders. A duct diameter that appears once as `DUCT_OD = 99.5` and is
referenced everywhere cannot drift. The alternative, OpenSCAD, is simpler but
mesh-only with awkward fillets and no STEP, which matters the first time a
part needs a chamfer to print well.

Proposed layout, in-repo alongside the firmware:

```
cad/
  params.py        # every dimension, one place, commented like config.py
  parts/           # one module per part above
  build.py         # renders all parts to STEP + STL
  coupons/         # the two test pieces
```

`params.py` is the point of the whole structure. Fan size, duct OD, wall
thickness, aperture, print tolerances — named, commented with *why*, and
shared by every part, so a 120 mm fan becoming a 140 mm one is one edit.

## Sequence

1. **Settle the fan.** Chosen: NF-F12 industrialPPC-2000 IP67. Not bought.
   Every dimension downstream depends on its real body, not its datasheet, so
   get one in hand before committing the cartridge.
2. **Measure the site.** Ceiling void depth, joist spacing, aperture, duct run
   length and number of bends. Void depth caps stack height and the straight
   run after the fan, not the contraction. See **Ceiling void depth** below.
3. **Board changes.** Specified in [`prototype-v2.md`](prototype-v2.md):
   IRM-10-12, R-78E5.0, IRF9540, PWM tied to 5 V. Bench this before it is
   inside anything. V1 firmware, no new image.
4. **Coupons.** Spigot and fan boss. Fit them to the real duct and real fan.
5. **Model, in the order in the table.**
6. **Print the airflow path first** — collar, damper, plenum, cartridge — and
   measure actual flow before printing anything cosmetic. This is the step
   that tells you whether the fan choice was right, and it is much cheaper to
   learn it before the grille exists.

---

# Before anything can be modeled

Three unknowns, each of which changes the geometry rather than decorating it.
Written out properly because two of them are easy to get wrong by intuition,
and one of them is a safety question.

## 1. Ceiling void depth

The empty space between the bathroom ceiling and whatever is above. With
joists, it is as deep as the joists are tall.

| Joist | Actual depth |
|---|---|
| 2×6 | 140 mm (5½ in) |
| 2×8 | 184 mm (7¼ in) |
| 2×10 | 235 mm (9¼ in) |
| Open loft above | effectively unlimited |

### Contractions are forgiving — the correction

An earlier draft of this plan said void depth caps the transition cone length,
which caps achievable airflow. That reasoning was wrong and is worth recording
so it does not come back.

Air going from a 120 mm fan into a 100 mm duct is **speeding up**. That is a
contraction, and accelerating air stays attached to the walls even when the
shape is fairly abrupt. Contractions are cheap.

The rule about needing a long gentle cone belongs to **diffusers**, where air
*slows down*: decelerating flow separates from the wall, tumbles into eddies,
and turns pressure into noise and heat. That is a real constraint — it just is
not the one in this part of this system.

### What void depth actually governs

**Stack height.** Fan (25 mm) plus transition plus damper plus electronics
tray plus diffuser plus grille all have to fit. In a 140 mm void that is tight
and drives the entire layout. In a loft it is a non-issue.

**Straight length after the fan.** This is the real constraint. Air leaves a
fan corkscrewing, not in a neat column. Hit it with a 90° bend immediately and
that swirl piles into the outside of the turn at real cost. Give it **one to
two duct diameters — 100 to 200 mm — of straight run before any bend** and it
settles into orderly flow first. Whether that straight section fits is a
void-depth question.

**How to find out:** measure a joist directly if there is loft access. From
below, pull the existing fan and poke a bent wire or tape up through the hole
until it stops. Note which way the joists run while you are there.

## 1b. The duct runs down the bay, and that is not a choice

**Decided, and forced by the framing.** The spigot leaves the air box parallel
to the joists, along the bay. Two alternatives were considered and neither
exists:

- **Across the joists.** A 100 mm duct cannot pass through a joist. The rule
  of thumb every inspector applies is that a bored hole may be at most a third
  of the joist depth and must keep clear of the edges: on a 2x6 that is roughly
  46 mm, and a 100 mm hole removes most of the member. This is a structural
  question, not a preference, and no amount of packaging gets around it.
- **Over the joists.** A 2x6 is 140 mm of a 152 mm void. There is nothing above
  them to run in.

So the housing is oriented to suit the bay, not the room: **the long axis of
the air box follows the joists**, the spigot points down the bay, and the
grille below it can be square regardless.

Two things fall out of that, one good and one to check:

- **Good:** the straight-run rule is satisfied for free. Air leaves a fan
  corkscrewing and wants one to two duct diameters — 100 to 200 mm — before
  the first bend. A bay gives metres of it.
- **To check:** which way the joists actually run, and where the bay goes.
  A bay running the wrong way turns a short duct into a long one with a bend
  at the end, and that is the single biggest line item in the pressure budget
  below. Note the direction while measuring the void depth; it is the same
  visit.

## 2. Duct run and terminal

**The idea in one image.** Drinking through a straw. The fan is your lungs.
Airflow (CFM) is how fast you drink. Static pressure is how hard you have to
suck. Short and fat is easy; long, thin, kinked and covered is the same lungs
and almost no drink.

Pressure here is **inches of water gauge** (in w.g.) — how far you could push
water up a tube. These are tiny numbers: 0.1 in w.g. is about 25 pascals,
roughly a 250th of a PSI.

### Two curves crossing

- **The fan curve** slopes down. Nothing attached: maximum air, zero pressure.
  Fully blocked: maximum pressure, zero air. Reality sits between.
- **The system curve** slopes up, steeply — resistance rises with roughly the
  **square** of flow. Double the airflow, quadruple the resistance.

Where they cross is the **operating point**, and it is the only airflow you
will actually get. This is the entire reason a fan advertising 70 CFM can
deliver 20: the 70 was measured with nothing attached. The industry knows it,
which is why residential bath fans are rated at **0.1 in w.g.** rather than
free air.

### What eats the budget

| Item | Rough cost |
|---|---|
| Flexible duct vs rigid | Flex is **2–3× worse** — corrugated bore, rarely pulled taut |
| Each 90° elbow | Equivalent to roughly 10–15 ft of straight duct |
| Wall or roof terminal | 0.05–0.15 in w.g. alone; worse with a clogged insect screen |
| Straight smooth duct | Cheap. This is the part to have most of |

Keep the total under **~0.25 in w.g.** for a residential bathroom.

**How to find out, no instruments needed:** trace the run and record length,
number of 90° bends, flex or rigid, true diameter (plenty of "4 inch" installs
are really 3 inch), and what the outside end looks like. A $30 vane anemometer
at the grille gives a real before-and-after if you want a number.

**Check while you are up there:** confirm the duct actually terminates
**outside**. A surprising number vent into the loft, which is a serious
moisture problem on its own and would make this project "fix a building
defect" before "fit a nicer fan".

## 3. Is the ceiling fire-rated?

Some ceilings are not just ceilings — they are a tested fire barrier, built to
hold fire back for a rated period (commonly 1 hour) so people can get out. The
rating belongs to the whole assembly: board, framing, fixings, spacing.

Cutting a hole breaches it. Code then wants that penetration protected so the
assembly keeps its rating — a listed fire-rated fan housing, a fire hood over
the fixture, or an intumescent collar that swells shut when hot.

**Why printed plastic is the wrong material for that job:** PETG, PLA and ASA
all soften and burn. A printed housing crossing a rated ceiling does not
merely fail to hold the rating — it adds fuel to the void and opens a hole at
the exact moment the barrier is needed.

**How to tell**, strongest indicators first:

- **Almost certainly not rated** — single-family house, top-floor bathroom,
  non-habitable attic above. The common case.
- **Likely rated** — a flat or condo with another dwelling above or below; a
  garage on the other side of the ceiling; part of an escape corridor.
- **Check the board.** Rated assemblies use Type X: ⅝ in thick, stamped on the
  back. Ordinary board is ½ in. Two layers is a strong hint.
- **Look at the neighbours.** Existing downlights wearing fire hoods in the
  loft mean someone already established the answer.

**If it is rated,** the project does not stop — the boundary moves. A listed
metal housing takes the penetration; the printed parts live on the room side
(grille, diffuser, sensor pod) and inside the metal box (electronics tray,
cartridge rails). The design survives intact.

## The measuring trip

One visit, a tape measure and a phone. Everything above collapses into this:

- [ ] Joist depth, and which way they run
- [ ] Ceiling board thickness — ½ in or ⅝ in
- [ ] Existing ceiling aperture size
- [ ] Duct diameter, measured not assumed
- [ ] Duct length and number of bends
- [ ] Flex or rigid
- [ ] Photo of the outside terminal
- [ ] Confirm it vents outside, not into the loft

---

## Open questions

- **PWM control law.** V2 ties the blue wire to 5 V and stays on/off, on the
  firmware V1 already runs. Variable speed makes a trickle mode possible,
  which interacts with both the humidity path and the planned odor path.
  That is a firmware project of its own, after a V2 unit has a measured CFM.
- **Baseline flow.** Whatever is installed now has a real, measurable
  performance. Getting a number before changing anything is the only way to
  know afterwards whether this was an improvement or an expensive sideways
  move.
