/**
 * Talking straight to the fan, with nothing else running anywhere.
 *
 * The other transport needs a broker, and a broker in practice means Home
 * Assistant's Mosquitto add-on -- so "local first" quietly meant "as long as
 * you run Home Assistant". This one needs the fan and a browser. The device
 * serves this very bundle off its own flash and answers a small JSON API on
 * the same port, so opening http://fan.local/ is the whole setup.
 *
 * Polling, not pushing. A browser cannot hold a raw socket, the device has
 * one core and a watchdog, and a WebSocket server on the control loop would
 * be a connection to keep alive on the side of something whose first duty is
 * a fan. Two seconds against a device that samples every two is as fresh as
 * the data gets, and the request is a few hundred bytes.
 *
 * One hop instead of two, which shows up in the failure modes: there is no
 * state where "the broker is up but the fan is not reporting". Either the
 * fan answers or nothing does.
 */
import { record } from '$lib/history.js';
import { loadHistory as fetchHistory, shouldTryBackfill,
         BACKFILL_RETRY_MS, RESUME_GAP_MS } from './backfill.js';

const POLL_MS = 2000;
const TIMEOUT_MS = 4000;

function base(s) {
  // Same host we were served from: use relative URLs. That is the ordinary
  // case -- the page came off the device's own flash -- and it sidesteps CORS
  // entirely rather than relying on the device to allow us. A different host
  // means the dev server on a laptop pointed at a real board, which does
  // need the absolute form.
  const port = s.httpPort && Number(s.httpPort) !== 80 ? `:${s.httpPort}` : '';
  if (typeof location !== 'undefined' && s.host === location.hostname) {
    const ours = location.port && location.port !== '80' ? `:${location.port}` : '';
    if (ours === port) return '';
  }
  return `http://${s.host}${port}`;
}

async function ask(url, options, ms = TIMEOUT_MS) {
  // A fetch with no timeout against a device that has gone away hangs until
  // the browser gives up, which on a phone can be a minute -- long enough
  // that the page looks frozen rather than offline.
  const ac = new AbortController();
  const id = setTimeout(() => ac.abort(), ms);
  try {
    return await fetch(url, { ...options, signal: ac.signal, cache: 'no-store' });
  } finally {
    clearTimeout(id);
  }
}

export function create(getSettings, sink) {
  let timer = null;
  let stopped = true;
  let inflight = false;
  let backfilled = false;
  let backfillTries = 0;
  let backfillAt = 0;
  let misses = 0;
  // The newest sample this browser held BEFORE we connected. Captured up
  // front because the first poll records a live sample immediately, and
  // asking "what is newer than my newest row" a moment later asks for the
  // last ten minutes rather than for the week that is actually missing.
  let knewUpTo = 0;
  // When the fan last answered. A long silence that ends in an answer is a
  // page that was frozen, not a fan that was down: see resumed().
  let lastOk = 0;

  function url(path) {
    return base(getSettings()) + path;
  }

  async function poll() {
    if (inflight || stopped) return;
    inflight = true;
    try {
      const res = await ask(url('/api/state'));
      if (!res.ok) throw new Error(`the fan answered ${res.status}`);
      const payload = await res.json();
      resumed(Date.now());
      misses = 0;
      sink.link.set('online');
      sink.linkError.set('');
      sink.deviceOnline.set(true);
      sink.telemetry.set(payload);
      sink.lastSeen.set(Date.now());
      record(payload);
      // Marked done only once it has actually worked. The old version set
      // the flag before awaiting, so one dropped connection discarded the
      // fan's whole stored week for the life of the session.
      if (shouldTryBackfill(backfilled, backfillTries, Date.now(), backfillAt)) {
        backfillTries += 1;
        backfillAt = Date.now() + BACKFILL_RETRY_MS;
        loadHistory().then((n) => {
          if (n !== null) backfilled = true;
        });
      }
    } catch (err) {
      // One miss is a dropped packet on a busy loop, not an outage. Say
      // nothing until a few in a row, or every reload of a page on a phone
      // with patchy wifi reports a fault it recovers from immediately.
      misses += 1;
      if (misses >= 3) {
        sink.deviceOnline.set(false);
        sink.link.set('error');
        sink.linkError.set(
          stopped ? '' : `No answer from the fan at ${base(getSettings()) || 'this address'}.`
        );
      }
    } finally {
      inflight = false;
    }
  }

  /**
   * Treat an answer after a long silence as a fresh connect, for history.
   *
   * The backfill used to run once per connect(), and connect() runs once
   * per page load. A home-screen app on iOS is not reloaded when it comes
   * back from the background -- the frozen page simply resumes -- so the
   * hours it spent frozen were never asked for: the charts showed "not
   * enough data", then filled from the moment of return. A fresh tab was
   * fine, which is what gave it away.
   *
   * Must run BEFORE record(): knewUpTo has to be the newest row from before
   * the gap, or the request asks for the last ten minutes instead of the
   * hours that are missing. backfill() merges by timestamp, so re-asking
   * for an overlap cannot duplicate rows.
   */
  function resumed(now) {
    if (lastOk && now - lastOk > RESUME_GAP_MS) {
      knewUpTo = sink.newestSample();
      backfilled = false;
      backfillTries = 0;
      backfillAt = 0;
    }
    lastOk = now;
  }

  // Poll at once when the page comes back rather than waiting out the
  // interval, so the refetch starts the moment somebody is looking. poll()
  // already refuses to overlap itself and to run once stopped.
  function onVisible() {
    if (document.visibilityState === 'visible') poll();
  }
  function onPageShow(e) {
    if (e.persisted) poll();
  }

  function loadHistory() {
    // The device keeps five-minute means for a week. Without this the charts
    // only ever know what this browser has personally watched, which is the
    // one honest complaint the old app could not answer.
    //
    // Same-origin in the ordinary case -- the page came off the fan's own
    // flash -- so there is no CORS question to answer here. backfill.js owns
    // the fetch because the broker transport needs it too.
    return fetchHistory(base(getSettings()), knewUpTo);
  }

  async function post(path, body) {
    try {
      const res = await ask(url(path), {
        method: 'POST',
        headers: { 'Content-Type': 'text/plain' },
        body: String(body)
      });
      // Poll straight away rather than waiting out the interval: the device
      // has already acted, and the confirmation is the next reading. This is
      // still not optimistic -- nothing is shown until the fan says so.
      poll();
      return res.ok;
    } catch {
      return false;
    }
  }

  return {
    kind: 'device',

    connect() {
      stopped = false;
      backfilled = false;
      backfillTries = 0;
      backfillAt = 0;
      misses = 0;
      lastOk = 0;
      knewUpTo = sink.newestSample();
      sink.link.set('connecting');
      sink.linkError.set('');
      sink.authFailed.set(false);
      poll();
      timer = setInterval(poll, POLL_MS);
      if (typeof document !== 'undefined') {
        document.addEventListener('visibilitychange', onVisible);
        addEventListener('pageshow', onPageShow);
      }
    },

    disconnect() {
      stopped = true;
      if (timer) clearInterval(timer);
      timer = null;
      lastOk = 0;
      if (typeof document !== 'undefined') {
        document.removeEventListener('visibilitychange', onVisible);
        removeEventListener('pageshow', onPageShow);
      }
      sink.link.set('idle');
      sink.deviceOnline.set(false);
    },

    command(name) {
      return post('/api/cmd', name);
    },

    setParam(name, value) {
      return post(`/api/set/${String(name).toLowerCase()}`, value);
    },

    /* WiFi provisioning. Only this transport has it: the whole point is a
       door that opens on the fan itself, and a broker on the far side of
       the network it is about to leave is not the right channel for it.
       link.js returns null in broker mode and the app says so. */
    wifi: {
      async status() {
        const res = await ask(url('/api/wifi'));
        return res.ok ? res.json() : null;
      },

      async scan() {
        // 15 s: a scan takes the radio through every channel, and on a busy
        // 2.4 GHz band that is several seconds before a single byte comes
        // back. The default timeout was aborting a scan that was working.
        const res = await ask(url('/api/wifi/scan'), {}, 15000);
        if (!res.ok) return { ok: false, status: res.status };
        return res.json();
      },

      async brokerStatus() {
        const res = await ask(url('/api/broker'));
        return res.ok ? res.json() : null;
      },

      async setBroker({ host, port, user, password, keepPassword }) {
        // One field per line, as /api/wifi and /api/cmd already do: a
        // password may contain any printable character, and a newline is the
        // one thing it cannot.
        //
        // Three lines, not four, when the password box was left alone. A
        // fourth empty line means "this broker wants no password", and
        // sending one regardless would wipe the credential every time
        // somebody corrected a typo in the hostname.
        const fields = [host ?? '', String(port ?? 1883), user ?? ''];
        if (!keepPassword) fields.push(password ?? '');
        const body = fields.join('\n');
        const res = await ask(url('/api/broker'), {
          method: 'POST',
          headers: { 'Content-Type': 'text/plain' },
          body
        });
        let payload = null;
        try {
          payload = await res.json();
        } catch {
          /* a body we cannot read is still a status code we can report */
        }
        // 200 saved outright ("no broker"), 202 still connecting.
        return {
          ok: res.status === 200 || res.status === 202,
          settled: res.status === 200,
          status: res.status,
          ...(payload ?? {})
        };
      },

      async connect(ssid, psk, country) {
        // One field per line, matching /api/cmd's plain-text bodies. A
        // passphrase may contain any printable character, and a newline is
        // the one thing it cannot, so splitting on it cannot corrupt it.
        const body = [ssid, psk ?? '', country ?? ''].join('\n');
        const res = await ask(url('/api/wifi'), {
          method: 'POST',
          headers: { 'Content-Type': 'text/plain' },
          body
        });
        let payload = null;
        try {
          payload = await res.json();
        } catch {
          /* a body we cannot read is still a status code we can report */
        }
        return { ok: res.status === 202, status: res.status, ...(payload ?? {}) };
      }
    }
  };
}
