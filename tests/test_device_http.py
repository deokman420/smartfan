"""The on-device server, on the device, over real WiFi.

    python tests/test_device_http.py [host] [--headed]

Needs the board powered, on the network, and running this firmware. Defaults
to `fan.local`, which is the thing it is partly there to prove.

This is the layer tests/test_httpd.py and tests/test_device_app.py cannot
reach. They run the same modules on a laptop, where sockets are CPython's and
memory is effectively free. Here the sockets are lwIP's over an SPI bus to a
radio, the heap is about 430 kB with a fan controller already living in it,
and the control loop is being asked to serve a browser between samples.

So what this asserts is deliberately not "does the API work" -- the host
suites settle that. It is:

* the name resolves, because "no IP to remember" was the point of it;
* a real browser loads the whole bundle off flash;
* the fan keeps its own time while being hammered. The watchdog reboots the
  board if the control loop ever stops feeding it, so `up_min` going
  backwards is the loudest possible failure and is checked after every phase;
* the stored history is being written, in the format the app reads.

It drives the real fan for a few seconds and puts it back, like test_ha.py.
"""

import json
import sys
import time
import urllib.error
import urllib.request

HOST = "fan.local"
HEADED = "--headed" in sys.argv
for arg in sys.argv[1:]:
    if not arg.startswith("-"):
        HOST = arg

BASE = "http://%s" % HOST
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("  %-52s %s %s" % (name, "PASS" if ok else "FAIL", detail))
    return ok


def get(path, timeout=10):
    """(status, body_text). A refused connection is a status of 0."""
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)


def post(path, body, timeout=10):
    req = urllib.request.Request(BASE + path, data=str(body).encode(),
                                 method="POST",
                                 headers={"Content-Type": "text/plain"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)


def state():
    st, body = get("/api/state")
    if st != 200:
        return None
    try:
        return json.loads(body)
    except ValueError:
        return None


def wait_for(pred, seconds=30):
    end = time.time() + seconds
    while time.time() < end:
        try:
            if pred():
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def main():
    print("the on-device server, on the device (%s)\n" % HOST)

    # --- it is there, under the name it promised ---------------------------
    st, body = get("/api/info")
    if st != 200:
        print("  cannot reach %s -- is the board powered and on the network?" % BASE)
        print("  %s" % body[:200])
        sys.exit(1)
    info = json.loads(body)
    check("the device answers at %s" % HOST, True,
          "fw %s, app %s" % (info.get("fw"), info.get("app")))
    check("it reports an app bundle on flash", bool(info.get("app")),
          info.get("app") or "none -- run tools/build_www.py --push")

    first = state()
    check("GET /api/state returns a live reading",
          first is not None and first.get("rh") is not None,
          "rh %s%%, %s" % (first.get("rh"), first.get("state")) if first else "")
    started_in = first.get("state")
    uptime = first.get("up_min")

    # --- the app itself, off flash, through a real browser -----------------
    browser_ok = True
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        browser_ok = False
        print("  (playwright not installed; skipping the browser checks)")

    if browser_ok:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not HEADED)
            page = browser.new_page(viewport={"width": 420, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text)
                    if m.type == "error" else None)
            failed = []
            page.on("requestfailed",
                    lambda r: failed.append(r.url.rsplit("/", 1)[-1]))

            t0 = time.time()
            page.goto(BASE + "/", wait_until="load", timeout=60000)
            load_s = time.time() - t0
            try:
                page.locator("h1").wait_for(state="visible", timeout=30000)
                loaded = True
            except Exception:
                loaded = False
            check("a browser loads the app from flash", loaded,
                  "%.1f s" % load_s)
            check("every file arrived", not failed, ", ".join(failed[:4]))

            # The app talks to the device it was served by, with nothing
            # configured -- the whole point of serving its own app.
            try:
                page.get_by_text("Live", exact=True).first.wait_for(
                    state="visible", timeout=30000)
                live = True
            except Exception:
                live = False
            check("it connects with nothing configured", live)

            body_text = page.locator(".wrap").inner_text()
            check("the reading on screen is the one the API returned",
                  str(first.get("rh")).split(".")[0] in body_text,
                  "expected rh %s" % first.get("rh"))
            check("no uncaught JS errors in the browser", not errors,
                  "; ".join(errors[:2]))
            page.screenshot(path="tests/screenshots/device-live.png",
                            full_page=True)
            print("     -> screenshots/device-live.png")
            browser.close()

    now = state()
    check("the controller did not reboot under a page load",
          now is not None and now.get("up_min", 0) >= uptime,
          "up_min %s -> %s" % (uptime, now.get("up_min") if now else "?"))
    uptime = now.get("up_min")

    # --- a command, verified on the fan rather than on the screen ----------
    st, body = post("/api/cmd", "boost")
    check("POST /api/cmd is accepted", st == 200, "status %d" % st)
    check("the fan actually runs",
          wait_for(lambda: (state() or {}).get("fan_on") is True, 20),
          "state %s" % (state() or {}).get("state"))
    time.sleep(3)
    st, _ = post("/api/cmd", "cancel")
    check("the fan can be stopped again", st == 200 and
          wait_for(lambda: (state() or {}).get("state") != "BOOST", 30),
          "state %s" % (state() or {}).get("state"))

    # --- history, in the format the app parses -----------------------------
    st, body = get("/api/history", timeout=30)
    lines = [l for l in body.split("\n") if l]
    check("GET /api/history streams CSV", st == 200 and
          bool(lines) and lines[0].startswith("#interval="),
          lines[0][:48] if lines else "empty")
    rows = [l for l in lines[1:] if l]
    check("history is being written",
          len(rows) > 0 or info["history"]["rows"] == 0,
          "%d rows (a fresh board writes its first after 5 minutes)" % len(rows))
    if rows:
        check("every stored row has 11 fields",
              all(len(r.split(",")) == 11 for r in rows))

    # --- the thing a browser does that a test client does not --------------
    # Several requests at once, which is how the cap and the backlog get
    # exercised. Anything refused here is a blank asset in a real page.
    import threading
    got = []

    def hit():
        got.append(get("/api/state", timeout=20)[0])

    threads = [threading.Thread(target=hit) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("eight simultaneous requests are all served",
          got.count(200) == len(got), "%d of %d ok: %s"
          % (got.count(200), len(got), sorted(set(got))))

    final = state()
    check("the control loop never stopped feeding the watchdog",
          final is not None and final.get("up_min", 0) >= uptime,
          "up_min %s -> %s" % (uptime, final.get("up_min") if final else "?"))
    check("the fan was left as it was found",
          final is not None and final.get("state") != "BOOST",
          "started in %s, left in %s"
          % (started_in, final.get("state") if final else "?"))

    failed_checks = [n for n, ok, _ in results if not ok]
    print("\n%d/%d passed" % (len(results) - len(failed_checks), len(results)))
    if failed_checks:
        for n in failed_checks:
            print("  FAILED: %s" % n)
        sys.exit(1)


if __name__ == "__main__":
    main()
