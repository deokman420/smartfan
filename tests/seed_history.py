"""A plausible six hours: quiet, a shower, the fan clearing it, quiet again.

Modelled on the numbers actually measured in this bathroom -- baseline
62 %RH, a shower peaking near 91 %, the 40 mm fan pulling it back over
about forty minutes. Used for screenshots and for the chart regression, so
the charts are exercised against the shape they exist to show.
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
        rows.append([t, round(rh, 1), round(base, 1), round(rh - base, 1),
                     round(temp, 1), round(dp, 1), round(light, 1),
                     rpm, fan, lit])
    return rows

if __name__ == "__main__":
    print(json.dumps(build()))
