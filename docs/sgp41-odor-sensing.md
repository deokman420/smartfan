# Odor sensing with the SGP41

What this room's own data says about the gas sensor, what each threshold in
`config.py` was measured from, and which ideas were tried and refuted. Written
4 Nov 2026; `config.py` has pointed here since the sensor was fitted.

Every number below was measured on this unit, in this flat, on a baseline the
settle gate allowed. None of it comes off a datasheet.

## The signal, and its sign

Two pixels on one part. The **VOC pixel** reads reducing gases; its raw tick
count **falls** as the air gets worse, so the deviation meaning "bad air" is
`base - raw`. The **NOx pixel** reads oxidising gases, mainly NO2, and moves
the opposite way: bad air drives the raw count **up**, so a NOx rise is a
*negative* `excess_nox`. `Detector.excess()` is `base - raw` for both pixels,
and `_combustion()` in `main.py` flips the sign locally rather than changing
what the published value means.

That sign was an open question for two days — `voc.py` carried a section headed
`THE NOx SIGN IS NOT CONFIRMED` — and it was settled by two event types
agreeing, not by reasoning. It had predicted its own source, too: "a gas hob
next door is a better NO2 source than anything on the bench."

## Four labeled events

All on one baseline, within hours of each other, device in the bathroom.
`ratio` is NOx rise divided by VOC fall, and it is the only column that
separates the event *classes*.

| event | VOC | NOx rise | ratio | dew point | what it is |
|---|---|---|---|---|---|
| bathroom odor | **+899** | +64 | **0.07** | -0.1 C | the real thing |
| gas hob, kitchen | +504 | **+1498** | **2.97** | +0.7 C | another room |
| tidying used litter | -150 | +21 | **0.14** | +0.2 C | litter disturbed |
| agitating **fresh** litter | -166 | +24 | **0.14** | +0.1 C | litter disturbed |

Three findings fall straight out of that table.

**The classes are an order of magnitude apart in ratio, not in size.** On VOC
alone the cooking event is *smaller* than the bathroom event, so no VOC
threshold can reject the one and keep the other. The NOx pixel separates them
by a factor of about 20. This is what `VOC_NOX_VETO` exists for.

**Dew point distinguishes "moisture arrived" from "the room cooled".** Only
cooking brought real water (+0.7 C). Everything else moved the humidity
reading slightly while the dew point stayed flat, which is the signature of
temperature change rather than moisture — and the reason the controller logs
dew point at all.

**The NOx pixel is the stronger sensor on the event it responds to**, by nine
to one in signal-to-noise, and it led VOC by about 7 minutes on the cooking
event. It is nonetheless *not* a general odor channel: it barely moves on
bathroom odor or on litter, because those are reducing gases. It is a
combustion channel, and it is used as a veto rather than as a trigger.

## The litter experiment, and what it ruled out

Prompted by a real question: the cat's box is in the bathroom, and a dust is
raised whenever anything disturbs it. Can the sensor see the dust?

**No — and it does not need to.** Both litter events fell to about 150-166
ticks and then **recovered fully within 5-8 minutes**. An MOX sensor cannot
transduce a particle; dust affects it only by settling on the heated element,
which is a *permanent* offset. A clean rise-and-recover curve is therefore
proof that what was measured was a **vapour**, not dust. Silica gel is a
desiccant: it holds water and volatiles in its pores and gives them up when
agitated, and the dust simply travels with them.

**Fresh litter is indistinguishable from used** — -166 against -150, the same
0.14 ratio, the same curve. So the vapour is the litter material's own,
nothing depends on the box being dirty, and a freshly changed box is equally
detectable. It also means the signal is not ammonia: the box does not smell
unless it badly needs changing.

The signature is **repeatable**, which matters more than either event alone:
two independent disturbances landed within 10% of each other on magnitude and
identical on ratio.

**Still unmeasured: the cat's own event.** Both measurements are of a human
disturbing the litter. His may well be weaker, and nothing here shows
otherwise.

## Refuted: a rate-of-arrival trigger

Tried twice, for two different reasons, and it fails both times.

Largest sustained VOC fall in ticks per minute, every series decimated to 10 s
so that denser sampling cannot win by luck. The last column is the one that
matters: the margin over a quiet night, which is the false-positive risk.

| | 60 s | 120 s | 180 s | 120 s vs quiet |
|---|---|---|---|---|
| quiet night (settled) | -77 | -38 | -25 | — |
| bathroom event | -894 | -444 | -295 | **11.8x** |
| gas hob | -84 | -73 | -65 | 2.0x |
| used litter | -113 | -75 | -47 | 2.0x |
| fresh litter | -140 | -79 | -39 | 2.1x |

Rate is excellent for a real bathroom event — nearly 12x clear at every
window. It is useless for litter, because **litter sits exactly where cooking
sits** (about 2x). A rate trigger tuned to catch the litter box would also
start the fan for cooking in another room, which is the one thing this unit
must not do.

An earlier attempt at the same idea was refuted differently: quiet-night drops
of 116-258 ticks exceeded the cooking event's fan-off drops of 38-51.

## The real constraint is baseline stability, not signal strength

This is the finding worth acting on, and it reframes every threshold here.

    stable room, short window     VOC excess sd   7-8 ticks
    overnight, room cooling       VOC excess max   125 ticks

The *signal* is strong: a 150-tick litter event is about **20 standard
deviations** above a stable room. What is weak is the reference it is measured
against. Across a night the excess wandered 204 ticks peak-to-peak — not
because the sensor is noisy, but because the room cooled, and

> **0.3 C of cooling moves this channel by an entire trigger threshold.**

Measured: **VOC -115 and NOx +138 ticks per %RH**, *after* the SGP41's own
on-chip humidity compensation.

So a `VOC_RISE_ON` sized to survive **baseline error** rather than sensor
noise is what put a genuine 150-tick litter event out of reach, and the two
requirements pull against each other directly: 200 is clean overnight and
blind to litter, while something near 120 sees litter and reintroduces the
3 a.m. false start.

**The unexplored fix is to compensate the baseline for temperature**, using
the coefficients above and the nine hours of labeled quiet data already
recorded. If that cuts the noise floor several-fold, a low threshold becomes
safe without the trade below, litter events trigger, and the NOx veto keeps
cooking out. It has not been attempted.

## The litter run, and what it costs

The trade was taken deliberately rather than waited out, because a short run
during a litter event is wanted for its own sake — it keeps the disturbed
material from drifting out of the bathroom — and an occasional unexplained
short run is acceptable here. Settings only, no firmware:

    VOC_RISE_ON   200 -> 140      clears both litter events (-150, -166)
    MIN_RUN_S     180 -> 300      the 5 minutes the material needs
    VOC_FALL_OFF   70             unchanged
    VOC_NOX_VETO  300             unchanged; litter's NOx ratio is 0.14

`MIN_RUN_S` is doing the real work on run length. Fresh litter stayed over
140 for only **1.5 minutes**, so without the floor it would have been a
3-minute run; used litter decayed through 70 in about 5-6 minutes and so
self-limits at roughly the same place. Both give a clean 5 minutes. A real
bathroom event at +899 is untouched and still runs until the air is clear,
not for 5 minutes.

Three costs, all of them consequences of the baseline problem above:

- **The hysteresis gap falls from 130 ticks to 70** — 3.7 sd of quiet noise
  down to 2.0. That is the margin that stopped the decaying tail of the real
  event restarting the fan twice. `MIN_RUN_S 300` lengthens each cycle and so
  partly masks it, but the off-time is still unbounded (`docs/TODO.md`).
- **`MIN_RUN_S` is global**, so every run now has a 5-minute floor, including
  a brief light-on with dry air. A per-reason minimum would be control-logic
  work in `main.py`. That file does go over the air now; what is missing is
  the design of the per-reason floor, not a way to ship it.
- **False starts become possible.** A genuinely quiet 3.5 h window (1204
  samples, fan off, excess -87..+117) crossed nothing at any threshold down
  to 120 — but 10 s sampling missed a peak the device itself triggered on at
  `up=265.7`, so the true quiet maximum is above 120 and may reach 140.

### Confirmed on a live event, of unestablished source

Measured 4 Nov with the settings above already live, during a tidy of the
box. This replaces the prediction in this section with an observation --
about the *path*, not about litter. See the source note below: the decay
curve does not match either bench event, and the occupant's rival
explanation for it cannot be ruled out.

    t=0      FAN ON    voc_exc 136   nox_exc -47   (already decaying)
    t=60     voc_exc  71
    t=120    voc_exc  67
    t=270    voc_exc  -4
    t=298    FAN OFF   ran 5.0 min
    after    voc_exc returns to 0 and stays

**The run was 298 s, and `MIN_RUN_S` set all of it.** The air was back under
`VOC_FALL_OFF` within 60 seconds — far faster than either bench event, whose
decay curves are what the 5-minute figure was derived from. So the floor, not
the decay, is what makes this work: under the old 180 this would have been a
3-minute run.

**The *path* attribution is unambiguous**, which is the part worth having on
record. The device's own history row for the run reads `fan=1, lit=0,
rh_exc=1.0` — light off, humidity at baseline — so the odor path is the only
cause available, and no other reason could have started this run. Across the 11 minutes temperature moved 0.1 C, RH 1.7 % and dew
point 0.4, confirming gas rather than moisture or the room cooling.

**The veto stayed out of it correctly:** `nox_exc -47`, a 47-tick NOx rise
against a 300 veto.

**`voc_exc` returned to 0 and stayed there**, with one 26-tick bump that
restarted nothing. Dust settling on the heated element would leave a
*permanent* offset, not a recovery, so this is a second line of evidence for
a vapour.

#### What produced it is not established

This was first written up as a litter event. That was an assumption carried
over from what was happening in the room, not a measurement, and the data
mildly argues against it. The occupant's own suggestion -- that the sensor
had caught a fart -- fits the curve better:

| event | peak | time to clear `VOC_FALL_OFF` |
|---|---|---|
| used litter, bench | -150 | 5-6 min |
| fresh litter, bench | -166 | 4-5 min |
| bathroom event, 4 Nov | +899 | above 120 for 8.3 min |
| **this event** | **>140, clipped** | **60 s** |

**It cleared five times faster than either litter event.** Agitated litter
outgasses from the whole tray and decays slowly; a single puff of gas
disperses and is gone. A 60-second clear is the shape of a transient point
source, not of a disturbed mass of material. Against that reading, the event
was *small*, where the one documented bathroom event was +899 -- so if that is
what it was, it was a modest one, and one event gives no distribution to judge
against.

The sensor cannot settle this. The VOC pixel measures reducing gases and has
no access to their origin; litter vapour and a human bathroom event differ in
shape alone. **So record it as an odor event in the bathroom, source
unknown** -- which costs nothing, because the fan's response is the same
either way and that response was correct.

It does leave a marker worth having: if a ~60-second clear turns out to be
repeatable for puffs and a multi-minute one for the tray, that is a
discriminator, and it would come for free from the data already being stored.
Two more labeled events of each kind would test it.

One further caveat: a launch error in the recorder cost the first minute, so
**the true peak is unknown** — only that it exceeded 140, since the device had
already triggered and was passing 136 downward at the first sample. The
5-minute history interval is too coarse to recover it. That is also why the
NOx ratio here (~0.35 against the clipped peak) cannot be compared with the
bench events' 0.14: the denominator is wrong by an unknown amount.

## Warm-up, and why it is two questions

The pixels warm at very different rates, measured on a clean boot on 4 Nov:

| pixel | settled at | how |
|---|---|---|
| NOx | **75.6 min** | flatness, a 31-tick window |
| VOC | **120.2 min** | **the ceiling** — the gate timed out |

The shared 45-tick flatness threshold was *narrower than the VOC pixel's own
flattest 15-minute window* (51 ticks), so for that pixel the gate could never
fire and `VOC_SETTLE_MAX_S` was always the exit. It did no harm — the ceiling
seeded a baseline that the following six quiet hours agreed with.

**Set 2026-11-06, per pixel.** VOC's limit is 70, so that 51-tick window can
pass. NOx stays at 45. 70 also clears a 4 tick/min ramp (60 ticks across the
same window), which is the slope VOC still had at 30 minutes, so the gate
may now seed during the last of the warm-up rather than at the ceiling. See
`docs/TODO.md`.

While a pixel is unsettled its baseline stays unseeded, which makes the odor
path **inert rather than wrong**: humidity and the light still run the fan. A
power cut therefore costs about two hours of odor sensing, because the
baselines live in RAM.

## What each threshold was measured from

| setting | live | measured from |
|---|---|---|
| `VOC_RISE_ON` | 140 | clears both litter events; 200 was the quiet-air answer |
| `VOC_FALL_OFF` | 70 | leaves a 70-tick gap, 2.0 sd of quiet noise |
| `MIN_RUN_S` | 300 | fresh litter is only 1.5 min over the trigger |
| `VOC_NOX_VETO` | 300 | 4.7x above the bathroom event's NOx, 5.0x below cooking's |
| `VOC_SETTLE_MAX_S` | 7200 | the measured NOx warm-up, so the ceiling rarely fires |

Both VOC thresholds **ship at 0**, which `_voc_armed()` reads as "never
trigger", so a reflash is never louder than the unit it replaced. The live
values come from `settings.json`.

## Open, in priority order

1. **Temperature-compensate the VOC baseline.** The blocker for everything
   else here, and what would let the threshold come down without the costs
   recorded above.
2. **Per-pixel flatness threshold**, so the VOC settle gate fires on flatness
   instead of timing out.
3. **Latch the combustion veto.** NOx clears faster than VOC, so late in a
   cooking event the veto releases while the odor excess is still high.
   `VOC_RECOVER_S` is the precedent.
4. **Measure the cat's own event**, which needs a long watcher rather than a
   window somebody picked.
5. **Test decay time as a source discriminator.** The one live event cleared
   in 60 s where both bench litter events took 4-6 minutes, so a puff and the
   tray may be separable by decay alone. Needs two more labeled events of
   each kind; the data is already stored, so this costs only the labeling.

Related: `docs/TODO.md` for the open items with their evidence,
`custom_components/smartfan/` for what Home Assistant sees, and `config.py`
for the full argument written beside each number.
