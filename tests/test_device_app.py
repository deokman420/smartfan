"""The app against the device's own server, with no broker anywhere.

    python tests/test_device_app.py [--headed]

This is the suite for the setup the on-device server exists to make possible:
no Home Assistant, no Mosquitto, nothing running but the fan and a browser.

The trick that makes it runnable on a laptop is that the server under test is
the real one. `httpd.Server` and `webapi.Api` are the modules that ship, bound
to a stand-in controller and stepped in short bursts exactly as the control
loop steps them. The only fiction is the fan.

    :8779   the real device server -- /api/state, /api/cmd, /api/history
    :8780   app/build, as a device would serve it from flash

Two ports rather than one, because it is also the arrangement a developer
works in: a page on one origin talking to a device on another, which is the
case CORS has to allow and the one most likely to break silently.

What it proves that tests/test_httpd.py cannot: that the app, built, actually
reads those endpoints -- that a tile shows the number the API returned, that
Boost reaches the fan, and that a browser which has never seen this fan draws
a week of history from the fan's own flash rather than an empty chart.

The bundle is served by our own httpd.py too, not by a convenient Python web
server. That was a real hole: a browser opens several connections at once and
this server caps them at three, answering the rest with 503, and nothing had
ever checked that a page loads anyway.
"""

import functools
import http.server
import json
import os
import socketserver
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import httpd                                            # noqa: E402
import store                                            # noqa: E402
import webapi                                           # noqa: E402
from telemetry import Snapshot                           # noqa: E402
from test_httpd import Cfg, FakeController, Pump         # noqa: E402

BUILD = os.path.join(ROOT, "app", "build")
WWW = os.path.join(ROOT, "www")
SHOTS = os.path.join(ROOT, "tests", "screenshots")
API_PORT = 8779
APP_PORT = 8780
HEADED = "--headed" in sys.argv

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("  %-52s %s %s" % (name, "PASS" if ok else "FAIL", detail))
    return ok


def wait_for(pred, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if pred():
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


# --- a week of history, written the way the device writes it ----------------

def seed_history(hist, now):
    """Two days of buckets with one shower in them.

    Written through History.note() rather than into the file, so what the app
    reads back has been through the same rounding, bucketing and rotation the
    device would put it through. A test that writes the file directly is
    testing its own idea of the format.
    """
    clock = [now - 2 * 86400]
    store._epoch = lambda: clock[0]
    rows = 0
    while clock[0] < now:
        # A shower on each of the two mornings, an hour after midnight local.
        into_day = clock[0] % 86400
        showering = 3600 <= into_day <= 5400
        rh = 88.0 if showering else 61.0
        hist.note({"rh": rh, "base": 60.0, "exc": rh - 60.0, "temp": 21.5,
                   "dp": 14.0, "light_pc": 40.0 if showering else 6.0,
                   "rpm": 4100 if showering else 0, "fan_on": showering,
                   "light_on": showering,
                   "state": "RUN" if showering else "IDLE"})
        clock[0] += Cfg.HISTORY_INTERVAL_S
        rows += 1
    store._epoch = lambda: now
    return rows


def main():
    if not os.path.isdir(BUILD):
        sys.exit("no app/build -- run `cd app && npm run build` first")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("playwright not installed: pip install playwright && "
                 "playwright install chromium")

    os.makedirs(SHOTS, exist_ok=True)
    print("the app against the device's own server (no broker)\n")

    # The device server, with history already on its "flash".
    work = os.path.join(os.environ.get("TEMP", "/tmp"), "smartfan-device-test")
    os.makedirs(work, exist_ok=True)
    controller = FakeController()
    snap = Snapshot(Cfg, log=lambda *a: None)
    # `where` rather than a chdir: the server runs on another thread, and a
    # store reading relative paths while the main thread moves the working
    # directory reports an empty week rather than an error. Which is exactly
    # what it did, and it looked like a bug in the app.
    hist = store.History(Cfg, log=lambda *a: None, where=work)
    hist.clear()
    # Anchored to the real clock, not to a round number: the app discards
    # rows stamped in the future, so a fixed epoch makes the backfill silently
    # do nothing and the test pass for the wrong reason.
    now = int(time.time()) // Cfg.HISTORY_INTERVAL_S * Cfg.HISTORY_INTERVAL_S
    seeded = seed_history(hist, now)
    api = webapi.Api(controller, Cfg, snap, hist)
    server = httpd.Server(handler=api.handle, root=None, port=API_PORT,
                          log=lambda *a: None)
    if not server.start():
        sys.exit("could not bind :%d" % API_PORT)
    pump = Pump(server)
    pump.start()

    # The bundle, served by the real server from the real gzipped tree --
    # the same files tools/build_www.py puts on flash. Anything less would
    # test a page load that no user ever performs.
    if not os.path.isdir(WWW):
        sys.exit("no www/ -- run `python tools/build_www.py --no-build` first")
    files_api = webapi.Api(controller, Cfg, snap, hist)
    files = httpd.Server(handler=files_api.handle, root=WWW, port=APP_PORT,
                         log=lambda *a: None)
    if not files.start():
        sys.exit("could not bind :%d" % APP_PORT)
    files_pump = Pump(files)
    files_pump.start()

    cfg = {"mode": "device", "host": "127.0.0.1", "httpPort": API_PORT,
           "room": "bathroom"}

    errors = []
    offsite = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not HEADED)
            page = browser.new_page(viewport={"width": 420, "height": 900})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text)
                    if m.type == "error" else None)
            # Anything not addressed to this machine has left the network.
            page.on("request", lambda r: offsite.append(r.url)
                    if not r.url.startswith("http://127.0.0.1") else None)
            # Settings only. History is deliberately NOT seeded: the point is
            # that the charts fill from the fan rather than from this browser.
            page.add_init_script(
                "localStorage.setItem('smartfan.settings', %s);"
                % json.dumps(json.dumps(cfg)))
            page.goto("http://127.0.0.1:%d/" % APP_PORT, wait_until="load")

            def appears(locator, timeout=30000):
                try:
                    locator.wait_for(state="visible", timeout=timeout)
                    return True
                except Exception:
                    return False

            check("app loads", appears(page.locator("h1"), 15000))
            check("it reaches the fan with no broker running",
                  appears(page.get_by_text("Live", exact=True).first))

            body = page.locator(".wrap").inner_text()
            check("the humidity the API returned is on screen",
                  "63" in body, body[:80].replace("\n", " "))
            check("nothing claims a broker is involved",
                  "broker" not in body.lower())

            # A command, and the fan the app cannot see into.
            before = len(controller.commands)
            page.get_by_role("button", name="Boost").first.click()
            check("Boost reaches the fan over HTTP",
                  wait_for(lambda: len(controller.commands) > before, 15),
                  "%d commands" % len(controller.commands))
            check("the command arrived on the one command path",
                  controller.commands[-1][1] == "boost"
                  if controller.commands else False)
            # Verified on the controller, not on the app's own screen: the
            # screen is the thing under test and would be marking its own
            # homework.
            check("the fan is actually running", controller.fan.value() == 1)
            check("the app then shows the boost it was told about",
                  wait_for(lambda: "BOOST" in page.locator(".wrap").inner_text(),
                           15))

            # A parameter write, which the device validates and may refuse.
            page.get_by_role("button", name="Charts").first.click()
            page.wait_for_timeout(1500)
            charts = page.locator("svg").count()
            check("the charts drew from the fan's own history",
                  charts > 0, "%d svg elements" % charts)
            check("history came off the device, not this browser",
                  page.evaluate(
                      "JSON.parse(localStorage.getItem('smartfan.history')"
                      " || '[]').length") > 100,
                  "%d rows backfilled of %d seeded" % (page.evaluate(
                      "JSON.parse(localStorage.getItem('smartfan.history')"
                      " || '[]').length"), seeded))

            page.screenshot(path=os.path.join(SHOTS, "device-mode-charts.png"),
                            full_page=True)
            print("     -> screenshots/device-mode-charts.png")

            check("no uncaught JS errors", not errors, "; ".join(errors[:2]))

            # The whole point, stated as a test: nothing else was running.
            check("the fan served every request itself",
                  api.hits > 0 and server.errors == 0,
                  "%d api calls, %d errors" % (api.hits, server.errors))
            # The page load itself, through the same three-connection server.
            # A browser opens more sockets than that at once, so this is the
            # check that the cap does not cost anyone their stylesheet.
            check("the whole bundle loaded from the device's own server",
                  files.requests > 10 and files.errors == 0,
                  "%d requests, %d errors" % (files.requests, files.errors))

            # The app claims that nothing leaves the network. It used to be
            # untrue: the typefaces came from fonts.googleapis.com, so three
            # requests went to Google every time anyone opened the fan --
            # including from a phone's home screen. This is the check that
            # keeps the claim honest, because the next CDN link will look
            # just as harmless as that one did.
            check("nothing left the network", not offsite,
                  "; ".join(sorted(set(u.split("?")[0] for u in offsite))[:3]))
            browser.close()
    finally:
        pump.stop()
        server.stop()
        files_pump.stop()
        files.stop()

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d passed" % (len(results) - len(failed), len(results)))
    if failed:
        for n in failed:
            print("  FAILED: %s" % n)
        sys.exit(1)


if __name__ == "__main__":
    main()
