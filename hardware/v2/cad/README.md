# hardware/v2/cad — the Smartfan V2 printed parts

Six build123d scripts, the STLs they export, and a browser viewer to look at
those STLs before anything goes on a print bed.

Two files to start from, depending on what you came for:

- **[`PARTS.md`](PARTS.md)** — the print list. Build order, times, grams,
  support, hardware, and the reasoning behind every dimension that had to
  change on its way from the model to a part. **Read it before printing.**
- **this file** — how to run things: the viewer, the scripts, the slicer.

Everything here is authored from `../model-v2.html`, which models the assembly
at real size. The scripts are the single source of the STLs; the STLs are
committed artefacts of a `python -I` run, not hand-edited.

None of these parts has been test-fitted in the ceiling yet. Print the fit
coupons first (`fit_coupons.py`), and measure your own duct and ceiling void
before committing filament to the large parts.

## Launching the viewer

`viewer.html` draws any of the parts over a print bed, with its envelope and
triangle count, in whatever orientation it will actually print in.

**It must be served over HTTP.** It fetches the STLs, and `fetch` is blocked
on `file://` — double-clicking the file gets you a red error overlay saying
exactly that, not a model. So:

```bash
cd hardware/v2/cad
python -m http.server 8777
```

then open **<http://localhost:8777/viewer.html>**.

In this IDE, push it into the live preview pane instead of a browser tab:

```bash
curl -s -X POST "$OBLIVION_CONTROL/preview" \
  -H "x-oblivion-token: $OBLIVION_TOKEN" -H "content-type: application/json" \
  -d '{"url":"http://localhost:8777/viewer.html"}'
```

Any port works; 8777 is just the habit. Serve **this** folder, not the repo
root — the page asks for `duct-collar-v2.stl` and `vendor/` as siblings.

### What you get

It opens on the duct collar. The buttons on the right switch part, snap the
camera to iso / side / down-the-bore, and toggle the turntable, wireframe and
bed. Drag orbits, scroll zooms. There are no query parameters — the part is a
button, not a URL.

The panel bottom-left is the useful part: it reads the envelope straight off
the geometry, so it is the fastest way to check a part still fits its bed.
The same line goes to the console, which is how to verify a load without
looking at the picture:

```
[parts] duct-collar-v2.stl: 27988 triangles, 144.0 x 144.0 x 76.8 mm
```

### When it will not load

- **Red overlay, "Could not load …"** — either the STL is missing (re-run its
  script) or the page is on `file://`. The overlay says which.
- **"not a binary STL, or truncated"** — the parser handles binary STL only,
  which is what `export_stl` writes. An ASCII STL from somewhere else will
  not open here.
- **Blank page, no panels** — `vendor/` did not serve. `three.min.js` and
  `OrbitControls.js` are vendored locally precisely so this works offline;
  the only network request is Google Fonts, and losing it costs you nothing
  but the typeface.

The viewer never regenerates anything. If you changed a script, re-export
first — it will happily show you yesterday's STL.

## Regenerating the STLs

One script per part. Each writes its STL next to itself, already in print
orientation — Z up, centred, sitting on z = 0 — so no slicer needs to
re-orient it.

```bash
python -I duct_collar.py        # duct-collar-v2.stl
python -I turning_elbow.py      # turning-elbow-v2.stl + -half.stl
python -I cartridge_frame.py    # cartridge-frame-v2.stl
python -I filter_frame.py       # filter-frame-v2.stl
python -I grille.py             # grille-v2.stl
python -I fit_coupons.py        # both coupons, and both on one plate
```

**The `-I` is not optional.** The repo root has a `secrets.py` holding WiFi and
broker credentials, which shadows the stdlib `secrets` module; numpy does
`from secrets import randbits` deep inside its RNG, and without `-I` every
script dies on `ImportError: cannot import name randbits` before it draws
anything. `-I` also keeps the scripts from picking up anything else on the
path, which is worth having anyway.

Each script prints its own dimensions and envelope as it exports — that output
is the check, not the file size.

### The variants

| Want | Command | Writes |
|------|---------|--------|
| Plain build | `python -I duct_collar.py` | `duct-collar-v2.stl` |
| Shroud build, collar | `python -I duct_collar.py --no-stub` | `duct-collar-v2-nostub.stl` |
| Shroud build, elbow | `python -I turning_elbow.py --with-collar` | `turning-elbow-v2-collar{,-half}.stl` |
| One-piece elbow only | `python -I turning_elbow.py --whole` | skips the half |

The collar scripts also take an output path as a positional argument
(`python -I duct_collar.py scratch.stl`) if you want to export somewhere
without clobbering the committed STL.

`fit_coupons.py` takes no arguments. It *imports* `duct_collar` and
`cartridge_frame` and cuts the coupons out of the real solids, so it cannot
drift from them — change a part, re-run this, and the coupon follows. That
import is also why `__pycache__/` appears here.

### The environment

`build123d` is not in the system Python. It lives in a scratchpad venv:

```
C:\Users\0x458\AppData\Local\Temp\claude\T--claude-projects-pico-pico2w-fan-controller\
  40ebcf1e-7e76-46a7-b86b-e500fbaf8d2f\scratchpad\cad\.venv
```

Python 3.13.7, build123d 0.13.0. That path is a session scratchpad and may be
cleaned up at any time; if it is gone, any fresh venv does:

```bash
python -m venv .venv && .venv/Scripts/pip install build123d
```

One build123d trap worth knowing before editing a script: in 0.13 the `+`
operator does **not** fuse overlapping solids — it returns the complement. Every
script here uses explicit `.fuse()`, `.cut()` and `.intersect()` for that
reason. Keep it that way.

## Slicing

Bambu Studio's GUI opens the STLs directly and needs nothing special.

From the command line it needs `slicer-A1-256.json`, because the CLI **does not
follow a preset's `inherits` chain**. The stock A1 machine preset arrives with
no `printable_area`, which silently shrinks the bed and makes anything over
~200 mm refuse to place with "no object fully inside it" — the grille is 210
and hits exactly that. This file is the A1 preset with `printable_area`
written in directly.

The same bug means the filament preset arrives with `filament_density = 0` and
the CLI reports weight as `0.00`. The grams in `PARTS.md` are the slicer's
filament volume converted by hand at PETG's 1.27 g/cm³.

Estimates there were made with: Bambu A1, 0.4 nozzle, 0.20 mm Strength, PETG
Basic, 6 walls, 25 % infill.

## What is in here

| | |
|---|---|
| `*.py` | the six part scripts — authoring source |
| `*.stl` | their exports, in print orientation |
| `viewer.html` | the browser viewer, served over HTTP |
| `vendor/` | three.js + OrbitControls, local copies |
| `slicer-A1-256.json` | A1 machine preset with `printable_area` fixed |
| `PARTS.md` | the print list and the design reasoning |
| `assembly-*.png`, `viewer-*.png`, `*.gif` | renders, for the write-ups |

Related, one level up: `../model-v2.html` (the dimensions everything is taken
from) and `../assembly-v2.html` (build order and hardware — its first card
decides whether you print the collar or the elbow pair; they do not both fit).
