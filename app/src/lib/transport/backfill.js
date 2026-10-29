/**
 * Asking the fan for its stored week, from whichever transport is live.
 *
 * This was private to the device transport, which quietly meant history only
 * ever backfilled for people who talked to the fan directly. A broker-mode
 * browser recorded from the moment it connected and no earlier, so a new
 * phone still opened on an empty chart -- the exact complaint store.py was
 * written to answer, left half-answered because the answer lived in the
 * wrong file.
 *
 * The fan serves the rows over HTTP either way. Broker mode adds no second
 * route to them: it just needs the fan's address, which now arrives in the
 * telemetry payload as `ip`.
 *
 * CSV, not JSON, and that is the device's constraint rather than a
 * preference: parsing a week into objects and serialising it costs about 890
 * bytes of heap per row on an RP2350, roughly four times the memory the whole
 * board has. It sends the stored lines as they are and the parsing happens
 * here, where there is room for it. See the Rows docstring in webapi.py.
 */
import { backfill } from '$lib/history.js';

// Rows are append-only, so a device can legitimately be a firmware behind
// this app and send a row narrower than FIELD_COUNT. That is not a corrupt
// row, it is an older one, and dropping it would throw away the whole
// stored week every time a column is added -- which is exactly what the
// strict equality here used to do. Anything from MIN_FIELDS up is accepted
// and padded with nulls, which the charts already draw as absent.
const FIELD_COUNT = 12;
const MIN_FIELDS = 11;
const HISTORY_TIMEOUT_MS = 15_000;

// Backfill covers the gap in this browser's buffer, with an overlap so a
// boundary sample cannot fall between the two sources.
export const OVERLAP_S = 600;

/**
 * The device's history format: a comment line, then one CSV row per bucket.
 *
 *   #interval=300 fields=t,rh,base,exc,temp,dp,light,rpm,fan,lit,st
 *   1790000000,71.5,60.0,11.5,21.4,14.2,7.6,4100,1,1,2
 *
 * Empty fields are nulls, and a short last line is a row that was being
 * appended when the power went out — skipped rather than guessed at, which
 * is what the device's own parser does with it too.
 */
export function parseRows(text) {
  let interval = 300;
  const rows = [];
  for (const line of text.split('\n')) {
    if (!line) continue;
    if (line[0] === '#') {
      const m = /interval=(\d+)/.exec(line);
      if (m) interval = Number(m[1]) || 300;
      continue;
    }
    const parts = line.split(',');
    if (parts.length < MIN_FIELDS || parts.length > FIELD_COUNT) continue;
    const row = new Array(FIELD_COUNT).fill(null);
    let ok = true;
    for (let i = 0; i < parts.length; i++) {
      if (parts[i] === '') {
        row[i] = null;
        continue;
      }
      const n = Number(parts[i]);
      if (!Number.isFinite(n)) {
        ok = false;
        break;
      }
      row[i] = n;
    }
    if (ok) rows.push(row);
  }
  return { rows, interval };
}

/**
 * Fetch the fan's stored rows and fold them into the buffer. Returns the
 * number of rows added, or 0 for every kind of failure.
 *
 * `base` is an origin with no trailing slash, or '' for same-origin.
 * `knewUpTo` is the newest sample this browser held BEFORE connecting, in ms.
 *
 * History is a nicety and the live stream is the point, so nothing here ever
 * becomes a banner. It does get said out loud, because a silent catch is how
 * a backfill that quietly stopped working stays unnoticed.
 */
export async function loadHistory(base, knewUpTo = 0) {
  const since = knewUpTo ? Math.max(0, Math.floor(knewUpTo / 1000) - OVERLAP_S) : 0;
  const ac = new AbortController();
  const id = setTimeout(() => ac.abort(), HISTORY_TIMEOUT_MS);
  try {
    const res = await fetch(`${base}/api/history?since=${since}`, {
      signal: ac.signal,
      cache: 'no-store'
    });
    if (!res.ok) return 0;
    const { rows, interval } = parseRows(await res.text());
    return rows.length ? backfill(rows, interval) : 0;
  } catch (err) {
    console.warn('history backfill failed:', err);
    return 0;
  } finally {
    clearTimeout(id);
  }
}

/**
 * Whether a cross-origin read of the fan's API can possibly succeed from
 * this page, checked before trying so the ordinary case does not log a
 * failure it could have predicted.
 *
 * Two independent walls, and neither is the device's to climb:
 *
 *   - An https page cannot fetch http://192.168.x.x at all. Mixed content is
 *     blocked by the browser and there is no header the fan can send to opt
 *     out of that.
 *   - httpd.py reflects Access-Control-Allow-Origin only for a plainly local
 *     origin -- loopback, an RFC1918 literal, or a .local name -- because a
 *     LAN device with no authentication must not hand out a blanket allow.
 *
 * So backfill over the broker works for a page served over plain http on the
 * LAN, which is the common case, and is skipped rather than attempted when
 * the page came over https. Someone reaching the app through the proxy keeps
 * the live stream and charts that fill from now on, as before.
 */
export function canReachDevice() {
  if (typeof location === 'undefined') return false;
  return location.protocol === 'http:';
}
