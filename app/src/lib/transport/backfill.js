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
import { backfill, F } from '$lib/history.js';

// Rows are append-only, so a device can legitimately disagree with this
// bundle about how many columns a row has -- IN EITHER DIRECTION -- and
// neither case is a corrupt row.
//
// Narrower is an older firmware. Those are padded with nulls, which the
// charts already draw as absent.
//
// Wider is a NEWER firmware, and that is this project's ordinary state
// rather than an edge case: www/ only travels by USB while firmware goes
// over the air, so the device routinely runs ahead of the browser bundle.
// Those used to be dropped. Every one of them, silently, which is the
// whole stored week gone the moment a column is added -- the same failure
// the narrow case was fixed for, left standing in the mirror. They are now
// truncated, exactly as a narrow row is padded.
//
// Both halves rest on one invariant: store.py only ever APPENDS to FIELDS,
// so a column's position never changes meaning. If a column is ever
// inserted or reordered instead, positional parsing goes quietly wrong
// rather than loudly, and the fix is to map the header's `fields=` names
// instead of counting them.
//
// Derived from F, never written as a literal. This was `12` while F had
// grown to fifteen, so the device sent perfectly good rows and the app
// dropped every one of them -- a whole week of history discarded by a
// number that only looked like a constant.
const FIELD_COUNT = Object.keys(F).length;
// The narrowest row anything ever wrote. Unlike FIELD_COUNT this really is
// historical and does not move.
const MIN_FIELDS = 11;
const HISTORY_TIMEOUT_MS = 15_000;

// Backfill covers the gap in this browser's buffer, with an overlap so a
// boundary sample cannot fall between the two sources.
export const OVERLAP_S = 600;

/* How many times a failed backfill is worth re-asking for, and how long to
 * wait between tries.
 *
 * Bounded on purpose. History is a nicety and the live stream is the point,
 * so a fan that cannot serve its flash must not turn into a browser asking
 * for 97 kB every two seconds for ever. Five tries a minute apart covers the
 * cases that actually happen -- a phone whose wifi is still associating, a
 * device busy with an OTA, one dropped connection during the burst of
 * requests a page load makes -- and then gives up quietly.
 */
export const BACKFILL_TRIES = 5;
export const BACKFILL_RETRY_MS = 60_000;

/**
 * Should another backfill attempt be made right now?
 *
 * Pure, because the bug this replaces was a scheduling decision and not a
 * fetch: `backfilled = true` was set BEFORE the await, so the one attempt
 * was recorded as done whether or not it worked. Keeping the decision
 * separate from the I/O is what makes it checkable.
 *
 * @param done   has a backfill already succeeded?
 * @param tries  attempts made so far
 * @param now    current time in ms
 * @param nextAt earliest time the next attempt may run
 */
export function shouldTryBackfill(done, tries, now, nextAt) {
  if (done) return false;
  if (tries >= BACKFILL_TRIES) return false;
  return now >= nextAt;
}

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
  // Counted rather than ignored. A row this function refuses is a row the
  // user paid for in flash and will never see, and the only thing worse
  // than losing it is losing it quietly -- which is how the last two
  // versions of this check survived.
  let short = 0;
  let unparseable = 0;
  let ahead = 0;
  let widest = 0;
  for (const line of text.split('\n')) {
    if (!line) continue;
    if (line[0] === '#') {
      const m = /interval=(\d+)/.exec(line);
      if (m) interval = Number(m[1]) || 300;
      continue;
    }
    const parts = line.split(',');
    if (parts.length < MIN_FIELDS) {
      short += 1;
      continue;
    }
    if (parts.length > FIELD_COUNT) {
      ahead += 1;
      widest = Math.max(widest, parts.length);
    }
    // Only the columns this build understands. The rest are newer fields,
    // not different ones; see the FIELD_COUNT comment.
    const width = Math.min(parts.length, FIELD_COUNT);
    const row = new Array(FIELD_COUNT).fill(null);
    let ok = true;
    for (let i = 0; i < width; i++) {
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
    else unparseable += 1;
  }
  return { rows, interval, short, unparseable, ahead, widest };
}

/**
 * Fetch the fan's stored rows and fold them into the buffer.
 *
 * Returns the number of rows added on success -- which may be 0, meaning
 * the fetch worked and had nothing this browser was missing -- and null
 * when the fetch itself failed.
 *
 * The difference is the whole point. This used to return 0 for both, and
 * both callers took "0" as done and never asked again, so a single dropped
 * connection cost the user the entire stored week with no banner, no retry
 * and no way back short of a full reload. Seen on a phone on 5 Nov: charts
 * that began at the moment the app was opened, with seven days sitting on
 * the fan's flash the whole time.
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
    if (!res.ok) return null;
    const parsed = parseRows(await res.text());
    const { rows, interval, short, unparseable, ahead, widest } = parsed;
    if (short || unparseable) {
      console.warn(
        `history backfill: dropped ${short + unparseable} of ` +
          `${rows.length + short + unparseable} rows ` +
          `(${short} too short, ${unparseable} unparseable)`
      );
    }
    if (ahead) {
      // Not a failure, and said at info rather than warn for that reason.
      // It does get said: it is the one visible sign that the bundle in
      // this browser is older than the firmware on the fan.
      console.info(
        `history backfill: ${ahead} rows carry ${widest} columns, this ` +
          `build reads ${FIELD_COUNT} - extra fields ignored, update the app`
      );
    }
    return rows.length ? backfill(rows, interval) : 0;
  } catch (err) {
    console.warn('history backfill failed:', err);
    return null;
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
