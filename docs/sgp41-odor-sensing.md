# Odour sensing with the SGP41

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

## Four labelled events

All on one baseline, within hours of each other, device in the bathroom.
`ratio` is NOx rise divided by VOC fall, and it is the only column that
separates the event *classes*.

| event | VOC | NOx rise | ratio | dew point | what it is |
|---|---|---|---|---|---|
| bathroom odour | **+899** | +64 | **0.07** | -0.1 C | the real thing |
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
event. It is nonetheless *not* a general odour channel: it barely moves on
bathroom odour or on litter, because those are reducing gases. It is a
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

So `VOC_RISE_ON = 200` is sized to survive **baseline error**, not sensor
noise, and that is exactly what puts a genuine 150-tick litter event out of
reach. Lowering the threshold to about 120 would catch litter and would also
reintroduce the 3 a.m. false start that raising it to 200 fixed.

**The unexplored fix is to compensate the baseline for temperature**, using
the coefficients above and the nine hours of labelled quiet data already
recorded. If that cuts the noise floor several-fold, a threshold near 120
becomes safe, litter events trigger, and the NOx veto keeps cooking out. That
combination is what would make a litter event run the fan. It has not been
attempted.

Run length needs no new work. `MIN_RUN_S` guarantees 3 minutes and the run
ends when the excess falls under `VOC_FALL_OFF`. Both litter events decayed
through 70 ticks within 4-6 minutes, so such a run would last roughly 4-7
minutes and end itself.

## Warm-up, and why it is two questions

The pixels warm at very different rates, measured on a clean boot on 4 Nov:

| pixel | settled at | how |
|---|---|---|
| NOx | **75.6 min** | flatness, a 31-tick window |
| VOC | **120.2 min** | **the ceiling** — the gate timed out |

The shared 45-tick flatness threshold is *narrower than the VOC pixel's own
flattest 15-minute window* (51 ticks), so for that pixel the gate can never
fire and `VOC_SETTLE_MAX_S` is always the exit. It did no harm — the ceiling
seeded a baseline that the following six quiet hours agreed with — but it
wants a per-pixel threshold. See `docs/TODO.md`.

While a pixel is unsettled its baseline stays unseeded, which makes the odour
path **inert rather than wrong**: humidity and the light still run the fan. A
power cut therefore costs about two hours of odour sensing, because the
baselines live in RAM.

## What each threshold was measured from

| setting | live | measured from |
|---|---|---|
| `VOC_RISE_ON` | 200 | quiet air reached +125 unaided; the real event was +899 |
| `VOC_FALL_OFF` | 70 | leaves a 130-tick gap, 3.7 sd of quiet noise |
| `VOC_NOX_VETO` | 300 | 4.7x above the bathroom event's NOx, 5.0x below cooking's |
| `VOC_SETTLE_MAX_S` | 7200 | the measured NOx warm-up, so the ceiling rarely fires |

Both VOC thresholds **ship at 0**, which `_voc_armed()` reads as "never
trigger", so a reflash is never louder than the unit it replaced. The live
values come from `settings.json`.

## Open, in priority order

1. **Temperature-compensate the VOC baseline.** The blocker for everything
   else here, and the only route to detecting a litter event.
2. **Per-pixel flatness threshold**, so the VOC settle gate fires on flatness
   instead of timing out.
3. **Latch the combustion veto.** NOx clears faster than VOC, so late in a
   cooking event the veto releases while the odour excess is still high.
   `VOC_RECOVER_S` is the precedent.
4. **Measure the cat's own event**, which needs a long watcher rather than a
   window somebody picked.

Related: `docs/TODO.md` for the open items with their evidence,
`custom_components/smartfan/` for what Home Assistant sees, and `config.py`
for the full argument written beside each number.
