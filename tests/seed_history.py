"""A plausible six hours: quiet, an aerosol, a shower, the fan clearing it.

Modeled on the numbers actually measured in this bathroom -- baseline
62 %RH, a shower peaking near 91 %, the 40 mm fan pulling it back over
about forty minutes. Used for screenshots and for the chart regression, so
the charts are exercised against the shape they exist to show.

Rows are the device's full stored schema, all eighteen columns. They were
eleven, which predated both the ambient light column and the odor triple,
so every chart added since drew nothing in the fixture and the suites that
count charts had to ask the hardware whether a sensor was plugged in -- a
question whose answer changed between two runs of the same test this
afternoon. A fixture that carries every column makes the count a property
of the app instead.

The NOx triple was appended on 2026-11-02 and immediately proved the point
again: the comparison chart is gated on NOx data being present, so with the
fixture three columns short the chart appeared or vanished according to
whether a real sensor happened to be answering, and the chart count went
non-deterministic for exactly the reason the paragraph above describes.

The odor event is the one measured on the bench on 2026-11-01 with
isopropyl: a baseline near 30200 ticks, raw driven down about 4200, and a
recovery taking a few minutes. It sits in the quiet before the shower, so
the odor charts show an event the humidity charts do not -- which is the
whole reason that sensor is fitted.
"""
import json, math, time

def build(hours=6):
    now = int(time.time() * 1000)
    step = 30_000
    n = int(hours * 3600_000 / step)
    rows = []
    shower_at = n - int(95 * 60_000 / step)      # ~95 min ago
    for i in range(n):
        t = now - (n - i) * step
        since = (i - shower_at) * step / 60_000.0   # minutes since shower
        base = 62.3 + 0.2 * math.sin(i / 220.0)
        if since < 0:
            rh = base + 0.4 * math.sin(i / 40.0)
            fan, lit, rpm = 0, 0, 0
        elif since < 12:
            rh = base + 29.0 * (since / 12.0) ** 0.7
            fan, lit = (1 if since > 2 else 0), 1
            rpm = 4180 if fan else 0
        elif since < 52:
            rh = base + 29.0 * math.exp(-(since - 12) / 17.0) + 1.2
            fan, lit = 1, (1 if since < 18 else 0)
            rpm = 4210
        else:
            rh = base + 1.6 * math.exp(-(since - 52) / 40.0)
            fan, lit, rpm = 0, 0, 0
        temp = 21.2 + (2.6 if 0 < since < 30 else 0) * math.exp(-max(0, since - 8) / 20.0)
        dp = temp - (100 - rh) / 5.0
        light = 31.4 if lit else 1.6
        # Ambient is what the light is judged against, and it tracks the sky,
        # not the fixture: it stays low while the bulb's 31.4 % comes and
        # goes. A fixture where the two were equal would make every
        # light_rise() zero and the Light chart a flat pair of lines.
        amb = 1.6

        # Odor: an aerosol 20 minutes before the shower, clearing over
        # about six. Raw ticks fall as the air gets worse, so the excess is
        # base - raw. Both are stored, because the chart draws the pair.
        voc_base = 30200.0 + 12.0 * math.sin(i / 180.0)
        aerosol = since + 20.0                   # minutes since the aerosol
        if aerosol < 0:
            dip = 0.0
        elif aerosol < 0.5:
            dip = 4200.0 * (aerosol / 0.5)
        else:
            dip = 4200.0 * math.exp(-(aerosol - 0.5) / 1.8)
        voc = voc_base - dip

        # NOx, and the fixture's one job here is to show the discriminator
        # working. Isopropyl is a reducing gas: it moves the VOC pixel and
        # leaves the NOx pixel alone. So this stays flat THROUGH the event
        # the VOC line dips on, which is the shape that separates an aerosol
        # or a bathroom from a gas flame. A fixture where both pixels moved
        # together would draw a chart that cannot tell them apart and would
        # pass a comparison that distinguishes nothing.
        nox_base = 18400.0 + 8.0 * math.sin(i / 150.0)
        nox = nox_base + 6.0 * math.sin(i / 23.0)

        rows.append([t, round(rh, 1), round(base, 1), round(rh - base, 1),
                     round(temp, 1), round(dp, 1), round(light, 1),
                     rpm, fan, lit, 2 if fan else 1, round(amb, 1),
                     int(voc), int(voc_base), int(voc_base - voc),
                     int(nox), int(nox_base), int(nox_base - nox)])
    return rows

if __name__ == "__main__":
    print(json.dumps(build()))
