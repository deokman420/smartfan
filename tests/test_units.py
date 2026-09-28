"""Celsius and Fahrenheit, everywhere a temperature is printed.

    python tests/test_units.py [--headed]

Needs a built app and playwright. No fan, no broker: the units are a viewer
preference and the device is never told about them.

Why a browser and not a unit test of the arithmetic
---------------------------------------------------
The arithmetic is two lines and nobody gets it wrong twice. What goes wrong
is a temperature that is printed from a path the conversion does not reach,
and that only shows up rendered. Both bugs found while building this were of
exactly that shape:

  * the chart card's headline read `23.6 °F` beside an axis reading 75,
    because `stats()` reads the stored Celsius rows while the chart converts
    its own copy;
  * `Chart.svelte` decided whether to narrate the temperature/dew-point gap
    by testing `unit === '°C'`, which is a type tag wearing a unit's clothes
    and silently stopped being true in Fahrenheit.

So this walks the tabs and reads what is actually on screen.

The margin check is the one that matters most. A temperature and a
temperature DIFFERENCE do not convert the same way: the room-to-dew-point
margin is 1.8 °F per °C with no offset, and running it through the absolute
conversion turns a 3.9 degree margin into 39, which is not a rounding error
but a different claim about whether the mirror is about to fog.
"""

import http.server
import json
import os
import socketserver
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BUILD = os.path.join(ROOT, "app", "build")
SHOTS = os.path.join(ROOT, "tests", "screenshots")
PORT = 8783
HEADED = "--headed" in sys.argv

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("  %-54s %s %s" % (name, "PASS" if ok else "FAIL", detail))
    return ok


def bail(msg):
    print("\n%s" % msg)
    sys.exit(2)


if not os.path.isdir(BUILD):
    bail("No build at app/build. Run:  cd app && npm run build")

try:
    from playwright.sync_api import sync_playwright
except ImportError as e:
    bail("missing dependency: %s" % e)

from seed_history import build as build_history  # noqa: E402

os.makedirs(SHOTS, exist_ok=True)


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=BUILD, **kw)

    def log_message(self, *a):
        pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            self.close_connection = True


socketserver.TCPServer.allow_reuse_address = True
httpd = socketserver.TCPServer(("127.0.0.1", PORT), Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()

# Values chosen so no two of the three answers collide in either scale, and
# so a delta mistakenly run through the absolute conversion (39.0) could not
# be confused with the right answer (7.0).
TEMP, DEW = 23.6, 19.7
CFG = {"RH_RISE_ON": 8.0, "RH_FALL_OFF": 3.0, "MIN_RUN_S": 180, "MAX_RUN_S": 2700,
       "POST_LIGHT_S": 120, "BOOST_S": 900, "LIGHT_ON_ADC": 12.0, "LIGHT_OFF_ADC": 8.0}
STATE = {"state": "IDLE", "rh": 78.4, "base": 62.4, "exc": 16.0, "temp": TEMP,
         "dp": DEW, "light": 31.4, "light_on": False, "fan_on": False, "rpm": 0,
         "rssi": -58, "up_min": 941, "run_min": 30, "boost_left": 0,
         "fault": False, "mqtt": "online", "cfg": CFG, "app": "t", "fw": "t",
         "boot": "power", "light_pc": 31.4, "outage_s": 0, "reason": "",
         "reconn": 0}

_rows = build_history(6)
HIST = "# interval=30\n" + "\n".join(
    ",".join(str(int(v) if i == 0 else v)
             for i, v in enumerate([r[0] // 1000] + list(r[1:])))
    for r in _rows) + "\n"


def expected(unit):
    """Celsius in, what the screen must say out."""
    absolute = (lambda c: c * 9 / 5 + 32) if unit == "F" else (lambda c: c)
    difference = (lambda c: c * 9 / 5) if unit == "F" else (lambda c: c)
    suffix = "°F" if unit == "F" else "°C"
    return {
        "temp": "%.1f%s" % (absolute(TEMP), suffix),
        "dew": "%.1f%s" % (absolute(DEW), suffix),
        "margin": "%.1f%s" % (difference(TEMP - DEW), suffix),
        "wrong_margin": "%.1f%s" % (absolute(TEMP - DEW), suffix),
        "suffix": suffix,
    }


print("temperature units regression\n")

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=not HEADED)
    for unit in ("C", "F"):
        want = expected(unit)
        ctx = browser.new_context(viewport={"width": 430, "height": 900})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(
            "localStorage.setItem('smartfan.units', %r);"
            "localStorage.setItem('smartfan.settings', JSON.stringify("
            "{mode:'device', host:'127.0.0.1', httpPort:%d, room:'bathroom'}))"
            % (unit, PORT))
        page.route("**/api/state*", lambda r: r.fulfill(
            status=200, content_type="application/json", body=json.dumps(STATE)))
        page.route("**/api/history*", lambda r: r.fulfill(
            status=200, content_type="text/plain", body=HIST))
        page.goto("http://127.0.0.1:%d/" % PORT, wait_until="load")
        page.wait_for_timeout(2200)

        print("  -- %s" % ("Fahrenheit" if unit == "F" else "Celsius"))
        glance = page.eval_on_selector_all(
            ".glance-item .v", "els => els.map(e => e.textContent.trim())")
        check("the status glance shows the temperature", want["temp"] in glance,
              str(glance))

        page.click("nav button:has-text('Readings')")
        page.wait_for_timeout(700)
        reads = page.eval_on_selector_all(
            ".read-row .v", "els => els.map(e => e.textContent.trim())")
        check("readings: temperature", want["temp"] in reads, str(reads[:3]))
        check("readings: dew point", want["dew"] in reads)
        check("readings: the margin above dew point", want["margin"] in reads)
        # The whole reason delta() exists, asserted rather than assumed.
        check("the margin is NOT converted as an absolute",
              want["wrong_margin"] not in reads or unit == "C",
              "must not say %s" % want["wrong_margin"])

        page.click("nav button:has-text('Charts')")
        page.wait_for_timeout(1400)
        climate = page.evaluate("""() => {
          const card = [...document.querySelectorAll('.card')]
            .find(e => /Temperature and dew point/.test(e.textContent));
          if (!card) return null;
          return {
            head: card.querySelector('.chart-now')?.textContent.trim() ?? '',
            legend: card.textContent
          };
        }""")
        check("the climate chart card exists", climate is not None)
        if climate:
            # The bug this catches: stats() reads the stored Celsius rows
            # while the chart converts its own copy, so the headline and the
            # axis disagreed.
            check("the card headline is in the chosen unit",
                  want["temp"].replace(want["suffix"], "") in climate["head"]
                  and want["suffix"].strip("°") in climate["head"],
                  repr(climate["head"]))
            check("the legend shows both series converted",
                  want["temp"] in climate["legend"]
                  and want["dew"] in climate["legend"])

        check("no page errors", not errors, "; ".join(errors[:1]))
        page.screenshot(path=os.path.join(SHOTS, "units-%s.png" % unit))
        ctx.close()

    # --- the control itself -------------------------------------------
    ctx = browser.new_context(viewport={"width": 430, "height": 900})
    page = ctx.new_page()
    page.goto("http://127.0.0.1:%d/" % PORT, wait_until="load")
    page.wait_for_timeout(1200)
    print("  -- the setting")
    # Scoped by aria-label, not by class: the clock sits in an identical
    # .modes.two group beside this one, and a bare class selector matched
    # both the moment that arrived.
    temp = page.locator("[aria-label='Temperature units']")
    clock = page.locator("[aria-label='Clock format']")
    check("the units control is in the settings sheet", temp.count() == 1)
    check("the clock control is beside it", clock.count() == 1)

    temp.locator("button", has_text="°F").click()
    page.wait_for_timeout(400)
    check("choosing °F persists immediately",
          page.evaluate("localStorage.getItem('smartfan.units')") == "F")
    check("the chosen unit is marked for a screen reader",
          temp.locator("button[aria-checked=true]").inner_text().strip() == "°F")

    clock.locator("button", has_text="12 h").click()
    page.wait_for_timeout(400)
    check("choosing 12 h persists",
          page.evaluate("localStorage.getItem('smartfan.clock')") == "12")

    # Neither is part of the connection draft, so Cancel must not revert them.
    page.locator(".sheet-actions button", has_text="Cancel").first.click()
    page.wait_for_timeout(400)
    check("Cancel does not revert the unit",
          page.evaluate("localStorage.getItem('smartfan.units')") == "F")
    check("Cancel does not revert the clock",
          page.evaluate("localStorage.getItem('smartfan.clock')") == "12")

    # A viewer preference, never a device setting: the fan must not be told.
    posts = []
    page.on("request", lambda r: posts.append(r.url) if r.method == "POST" else None)
    page.wait_for_timeout(800)
    check("changing units sends nothing to the fan", not posts, str(posts[:2]))
    ctx.close()

    # -- the clock, rendered ------------------------------------------
    # Chart.svelte and Activity.svelte had byte-identical clock() functions,
    # which is how one of them gets left behind on 24-hour. A fresh context
    # so this is testing a loaded preference, not a just-clicked one.
    for pref, pattern, name in (
        ("12", r"^\d{1,2}:\d{2}(am|pm)$", "12-hour"),
        ("24", r"^([01]\d|2[0-3]):[0-5]\d$", "24-hour"),
    ):
        ctx = browser.new_context(viewport={"width": 430, "height": 900})
        page = ctx.new_page()
        page.add_init_script(
            "localStorage.setItem('smartfan.clock', %r);"
            "localStorage.setItem('smartfan.settings', JSON.stringify("
            "{mode:'device', host:'127.0.0.1', httpPort:%d, room:'bathroom'}))"
            % (pref, PORT))
        page.route("**/api/state*", lambda r: r.fulfill(
            status=200, content_type="application/json", body=json.dumps(STATE)))
        page.route("**/api/history*", lambda r: r.fulfill(
            status=200, content_type="text/plain", body=HIST))
        page.goto("http://127.0.0.1:%d/" % PORT, wait_until="load")
        page.wait_for_timeout(2600)
        found = page.evaluate(
            "(re) => [...document.querySelectorAll('span, div')]"
            ".map(e => e.textContent.trim())"
            ".filter(t => new RegExp(re).test(t))", pattern)
        check("the timeline reads as %s" % name, len(found) > 0, str(found[:3]))
        ctx.close()

    browser.close()

httpd.shutdown()

failed = [n for n, ok, _ in results if not ok]
print("\n%d/%d passed" % (len(results) - len(failed), len(results)))
sys.exit(1 if failed else 0)
