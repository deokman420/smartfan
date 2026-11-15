"""Replay recorded history through the VOC baseline, with and without the
humidity correction (ODOR_RH_COMP), and report what it would have done.

    python tools/odor_comp_replay.py day*.json [--from ISO --to ISO]

Input is Home Assistant's /api/history/period JSON (minimal_response) for
the bathroom smartfan's odour_raw, nox_raw, humidity, light_detected and
fan_running entities. Samples are resampled to 10 s; the EWMA weight is
scaled to match the device's 2 s loop. The real voc.Detector does the
learning; the correction mirrors main.Controller._odor_comp(). The learning
gate is approximated (IDLE, light off, room dry, excess <= VOC_FALL_OFF or
held 2 h), which is close enough to compare the two arithmetics -- not to
predict the device sample for sample.
"""
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import config as cfg   # noqa: E402
import voc             # noqa: E402

KEYS = {"odour_raw": "voc", "nox_raw": "nox", "humidity": "rh",
        "light_detected": "lit", "fan_running": "fan"}
STEP = 10


def load(paths):
    series = {}
    for p in paths:
        for ser in json.load(open(p)):
            eid = ser[0]["entity_id"]
            key = next((v for k, v in KEYS.items() if eid.endswith(k)), None)
            if key is None:
                continue
            for s in ser:
                st = s["state"]
                if key in ("lit", "fan"):
                    v = {"on": 1, "off": 0}.get(st)
                else:
                    try:
                        v = float(st)
                    except ValueError:
                        v = None
                t = datetime.fromisoformat(s["last_changed"]).timestamp()
                series.setdefault(key, []).append((t, v))
    for k in series:
        series[k].sort()
    t0 = max(s[0][0] for s in series.values())
    t1 = max(s[-1][0] for s in series.values())
    idx = {k: 0 for k in series}
    cur = {k: None for k in series}
    t = t0
    while t <= t1:
        for k, s in series.items():
            while idx[k] < len(s) and s[idx[k]][0] <= t:
                cur[k] = s[idx[k]][1]
                idx[k] += 1
        yield t, dict(cur)
        t += STEP


def replay(rows, comp_on):
    cfg.ODOR_RH_COMP = comp_on
    a = cfg.VOC_BASELINE_A * STEP / 2.0
    vd = voc.Detector(None, log=lambda *x: None, alpha=a)
    nd = voc.Detector(None, log=lambda *x: None, alpha=a, name="nox")
    rhb = ref = None
    hold = 0
    out = []
    for t, r in rows:
        rh, raw, nox = r.get("rh"), r.get("voc"), r.get("nox")
        if rh is None or raw is None or nox is None:
            out.append((t, None, r))
            continue
        busy = bool(r.get("fan")) or bool(r.get("lit"))
        rhb = rh if rhb is None else rhb
        xrh = rh - rhb
        if not r.get("fan") and xrh < 8:
            rhb += (rh - rhb) * 0.002 * STEP / 2.0
        comp = 0.0
        if comp_on:
            ref = rh if ref is None else ref + (rh - ref) * min(1.0, STEP / float(cfg.ODOR_COMP_TAU_S))
            ne = nd.excess()
            if not (xrh > 3.0 or (ne is not None and -ne > cfg.ODOR_COMP_NOX_GATE)):
                comp = max(-cfg.ODOR_COMP_MAX, min(cfg.ODOR_COMP_MAX, cfg.ODOR_COMP_K * (rh - ref)))
        exc = vd.excess(raw) if vd.base is not None else None
        quiet = exc is None or exc <= 45
        hold = 0 if quiet else hold + STEP
        learn = (not busy) and xrh <= 3.0 and (quiet or hold >= 7200)
        vd.update(int(raw), learn, comp=comp)
        ne = nd.excess(nox)
        nd.update(int(nox), learn and (ne is None or -ne < cfg.VOC_NOX_VETO))
        out.append((t, vd.excess(), r))
    return out


def main(argv):
    paths = [a for a in argv if a.endswith(".json")]
    lo = hi = None
    if "--from" in argv:
        lo = datetime.fromisoformat(argv[argv.index("--from") + 1]).timestamp()
    if "--to" in argv:
        hi = datetime.fromisoformat(argv[argv.index("--to") + 1]).timestamp()
    rows = list(load(paths))
    res = {}
    for on in (False, True):
        res[on] = replay(rows, on)
    for on in (False, True):
        xs = [(t, e) for t, e, r in res[on] if e is not None
              and (lo is None or t >= lo) and (hi is None or t < hi)
              and not r.get("fan") and not r.get("lit")]
        v = [e for _, e in xs]
        if not v:
            continue
        m = sum(v) / len(v)
        sd = (sum((x - m) ** 2 for x in v) / len(v)) ** 0.5
        above = 100.0 * sum(1 for x in v if x >= 80) / len(v)
        print("%-14s n=%6d  mean %6.1f  sd %6.1f  max %5d  >=80 %5.1f%%"
              % ("correction ON" if on else "correction off", len(v), m, sd,
                 max(v), above))


if __name__ == "__main__":
    main(sys.argv[1:])
