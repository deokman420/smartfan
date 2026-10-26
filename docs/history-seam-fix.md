# The history seam fix, as deployed

**Done 2026-10-26.** Commit `89c1dcc` closed the hole the charts drew at the
moment the app connected. It shipped in two halves, by two different routes,
and that split is the part worth remembering.

| | change | route | on the fan |
|---|---|---|---|
| device | `store.py` `partial()`, `webapi.py` `Rows(tail=...)` | OTA | `fw 485b54cd` |
| app | `app/src/lib/history.js` | USB | `app bcb319ed` |

## Why it took two routes

`ota_publish.py` ships Python modules only, and `www/` is excluded for a reason
beyond versioning: `ota.py` downloads each file with
`requests.get(...).content`, holding it whole in RAM before writing. The app's
gzipped chunks run to about 100 kB against a ~430 kB heap, so carrying the
bundle would need chunked download first.

So the app half reaches the fan only through
`python tools/build_www.py --push COM5`, which handles safe mode itself.

**Neither half works alone**, which is the trap. Measured with the device fix
live and the two bundles:

```
old bundle:  open row kept false   widest seam step 7.0 min   (threshold 5.0)
new bundle:  open row kept true    widest seam step 5.0 min   (threshold 5.5)
```

The old bundle discarded the row the device had just started sending, because
the first live reading of a session claimed its whole bucket — and the open
bucket falls in that same bucket. For the hours between the OTA and the USB
push, nothing was user-visible and nothing was broken.

## Verified

On the device, `/api/history` now ends with the bucket still being filled: the
newest row is **144 s** old where it used to be **452 s**, and `?since=` past
that row returns the header alone rather than repeating the partial row on
every poll.

In a real browser against the real fan, on a profile whose session ended seven
minutes earlier:

```
seeded      47 rows, session ended 7.0 min ago
after load  50 rows total, 3 at or after the seam
seam steps  5.0, 2.1 min
widest      4.98 min   threshold 5.50 min   -> ONE LINE
```

That widest step is a full interval. Against the old `GAP_MS` of exactly
300 s it would have passed by a second, by luck of where in the bucket the
connect landed; a connect a moment later would have broken the line again.
The threshold has to sit strictly between one interval and two, and it was
pinned at the lower bound.

`node tests/test_history.mjs` covers the same scenario off-device, and
`tests/test_httpd.py` covers the store and HTTP halves.
