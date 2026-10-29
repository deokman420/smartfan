# Daylight is brighter than the bulb

What was wrong with the light sensing, what replaced it, and the two other
bugs that only showed up once the first one was understood.

Measured 2026-10-29, from the device's own stored history for the four days
to that date.

## The report

The fan was running. The bathroom light was not on. It was the most sunlight
the room had had since the controller was commissioned.

## What the device said

```
light = 23397  (35.7%)   LIGHT_ON = 22000   ->  light_on: true
exc   = 0.5%             state: RUN, fan_on, rpm 3336
```

The room was dry — 0.5 %RH above its learned baseline. The fan was running
purely because the LDR had crossed `LIGHT_ON`, and what crossed it was the
sun. Today's trace is a smooth sunlight ramp with no step anywhere in it:
4.4 % at 08:40, 29.2 % at 15:00, over the threshold at 15:05.

It was not the first time. Two earlier false runs were already on flash:

| start | duration | light range | exc |
|---|---|---|---|
| 10-27 15:35 | 75 min | 19.6 → 43.5 % | −0.5 |
| 10-28 14:05 | **175 min** | 21.1 → 55.4 % | 0.3 → −0.6 |

## Why no threshold could have fixed it

The calibration in `config.py` recorded the fixture at 30000–32500 (46–50 %).
It actually reads **~81 %**. Worse, daylight in this room reaches **85 %**.

**The sun is brighter than the ceiling light.** There is no level that
separates them, so no value of `LIGHT_ON` would have worked — not a higher
one, not any one. The old 22000 merely sat less than halfway up the daylight
ramp, which is why it failed early and often.

What *does* separate them is the rate. Compare a real light-on with the false
trigger:

```
real fixture:   06:35  2.5%  ->  06:40  81.1%    one sample, +51511 counts
sunlight:       14:05 31.9%  ->  17:00 15.0%     smooth, max +51 counts/sample
```

A factor of a thousand. Measured over four days, the fastest the sky ever
moved was **+51 counts per 2 s sample**; the fixture moves **+51511 in one**.

## The fix

### 1. Track the ambient; call "fixture" what the ambient cannot explain

`light.py`. The same move the humidity logic already makes with its learned
baseline, for the same reason — the README's "absolute humidity thresholds
don't work" argument applies to light more sharply than it does to humidity.

Two conditions, both required, because they catch different lies:

- **the jump** — `raw - prev >= LIGHT_STEP_ON` in one sample. Rejects the sky,
  which cannot move this fast.
- **the rise** — `raw - ambient >= LIGHT_RISE_ON`. Rejects a jump that merely
  climbs out of a shadow back to where the room already was.

The ambient itself moves asymmetrically, because the two directions mean
different things. A reading that *falls* cannot be a fixture switching on, so
it is believed quickly. A reading that *rises* might be, so it is absorbed
only as fast as weather can account for — and as a hard ceiling in counts
(`LIGHT_AMB_UP`), not an EMA weight. That is deliberate: anything slower than
the ceiling is absorbed *completely* and can never accumulate into a false
trigger. An EMA cannot promise that, because a cloud edge crossing the sun
shifts indoor light by tens of percent in twenty seconds — which an EMA lags
and a slew limit eats.

The ambient freezes while the fixture is believed on, or it would climb to
meet the bulb and report the room dark. A frozen ambient can go stale, so the
off-edge re-anchors it, and `LIGHT_MAX_ON_S` re-anchors anyway if neither
edge ever arrives. That last one is what bounds every misreading.

**Replayed against all four days of stored history**, interpolated back to the
real 2 s sample rate: 30 minutes of asserted occupancy against the old rule's
275, every minute of it inside a window the history independently recorded as
lit, and no false triggers. Worst the sky ever came to either condition was a
51-count jump against 10000, and a 5958-count rise against 8000.

### 2. The MAX_RUN_S latch was being bypassed

`decide()`, in IDLE:

```python
if self.light_on:          # a LEVEL
    self.maxed = False     # "a new occupancy event clears the latch"
    self.set_state(RUN, "light on")
```

The comment says *event*; the code tests a *level*. So when `MAX_RUN_S`
latched the run off, the very next pass saw the light still on, cleared the
latch, and started again — the exact pathology the `MAX_RUN_S` comment says it
was added to fix for humidity. That is how 10-28 ran 175 minutes against a
60-minute cap.

Now the latch is released by a rising edge, and the "room has dried" release
additionally requires the light to be off — otherwise a latch set by a light
that will not go out releases instantly, since a dry room already satisfies
the humidity half.

### 3. Light alone no longer ventilates indefinitely

`LIGHT_ONLY_MAX_S`, default 600 s. Past that the fixture stops holding the fan
on by itself and humidity has to justify the rest.

This is the belt to the braces. However good the detector gets, it reads one
LDR through a window, and this bounds the cost of a bad reading to ten minutes
rather than an afternoon — whatever the cause, including causes nobody has
thought of. It happens to match how a bathroom is actually used.

## The limit that is left, and it is hardware

Once the ambient is near the rail the divider is compressed enough that the
bulb adds a few hundred counts, under `LIGHT_STEP_ON`, so a blazing afternoon
simply does not see the light at all. That fails *safe* — a missed occupancy,
not a fan running for three hours — and no threshold can do better from this
sensor in this position.

The fix for that one is a shade or a snoot aimed at the fixture rather than
the window, so the signal means what the whole design assumes it means. Until
then, humidity still runs the fan, which is the half of the design that was
never in doubt.

## An OTA ceiling, found the hard way

Worth recording because it will bite again, and because the first diagnosis
was wrong.

`ota.py` installs a batch or nothing. A batch much past ~60 kB of staged bytes
stages every file and then dies before the install step, with the error going
only to a USB serial console nobody is holding. From the network the symptom is
silent and confusing: the device re-downloads the same files on every push,
never resets, and keeps running the old code.

Measured on this device, in this order:

| staged | largest file | result |
|---|---|---|
| ~98 kB | 51.0 kB | staged, never installed |
| ~48 kB | 20.6 kB | installed |
| ~106 kB | 47.1 kB | staged, never installed |
| ~62 kB | 20.6 kB | installed |
| ~47 kB | **47.1 kB** | installed |

The obvious first reading — that a single file over ~45 kB cannot be pulled
into RAM, since `ota.py` does `data = r.content` — is **wrong**. The last row
disproves it: a 47 kB file installed perfectly well on its own. What binds is
the **batch total**, which makes sense, because every staged file is written as
`name.new` while the original and any `name.bak` are still on flash. It is
space, not RAM.

So the rule is: keep a push under about 60 kB of changed files. That makes
staged updates the normal case for any change touching several modules, and
staging has its own trap, which this change walked straight into:

- `config.py` keeps vestigial `LIGHT_ON` / `LIGHT_OFF` definitions so it can
  land a push *ahead* of the `main.py` that stops reading them. Without them an
  old `main.py` loses `cfg.LIGHT_ON` mid-flight, raises on its next sample, and
  rides the watchdog down to a rollback. That one was anticipated.
- `telemetry.py` was *not* made symmetrical, and that one was not. Its new
  payload fields read `c.light_amb` and `c.light_rise()`, which only the new
  `main.py` provides, so for the eight minutes between two pushes the snapshot
  raised — taking out `/api/state` and the history writer while the fan itself
  carried on correctly. It now reads both through `getattr`. **A module that
  can ship one push early must tolerate a Controller one push old.**

The root cause of the size limit is in `ota.py`, which is excluded from OTA on
purpose — it is the recovery path, and a recovery path you can break remotely
is not one. So fixing it needs the cable; parked in
[`TODO.md`](TODO.md).

The failure mode is at least safe: every failed attempt left the device healthy
on its old code, which is the design working as intended. It is just entirely
silent about it, and an invisible "everything is fine" is the worst shape a
deploy failure can have.

## Verified on the hardware

Installed 2026-10-29 as build `c5186ee8`, in the same afternoon sun that
started this:

```
light   24229   amb 24355   rise -126   light_on False
state   IDLE    fan False   fault False
```

The reading that had the fan running all afternoon now sits 126 counts *below*
the learned ambient, and the fan is off.
