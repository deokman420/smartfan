"""The app, against the live broker and the live fan.

    python tests/test_app.py [--headed]

Serves app/build itself, so it tests the artefact that would actually be
deployed rather than a dev server with different module resolution and
different error handling.

Needs: a built app (`cd app && npm run build`), the broker reachable, and
the device online. Read `secrets.py` for the broker account.

What is worth asserting here, and what is not
---------------------------------------------
Not "does it render" -- a screenshot shows that. What a suite can prove and
an eye cannot is that the values on screen are the values the device
actually published, and that pressing a control reaches the fan and comes
back. Everything else is decoration.

Like test_ha.py this drives the real fan for a few seconds and puts it back.
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

BUILD = os.path.join(ROOT, "app", "build")
SHOTS = os.path.join(ROOT, "tests", "screenshots")
PORT = 8778
HEADED = "--headed" in sys.argv

results = []

CONTRAST_AUDIT = """() => {
  const lum = (r,g,b) => { const c=[r,g,b].map(v=>{v/=255;
    return v<=0.03928? v/12.92 : Math.pow((v+0.055)/1.055,2.4);});
    return 0.2126*c[0]+0.7152*c[1]+0.0722*c[2]; };
  // Chromium serialises color-mix() as `color(srgb 1 1 1 / 0.92)`, whose
  // channels are 0-1 floats, not 0-255. Scraping digits out of that and
  // treating it as rgb() gives rgb(1,1,1) -- near-black -- so every element
  // sitting on a color-mix() surface was scored against the wrong
  // background. That was 25 false failures here, including the whole nav
  // bar: #455265 on #ffffff is 7.51:1, which the token file documents as
  // AAA and which this audit was calling 2.63:1.
  const parse = s => {
    const n = (s.match(/[0-9.]+/g)||[]).map(Number);
    if (/^color\(\s*srgb/i.test(s) && n.length >= 3)
      return [n[0]*255, n[1]*255, n[2]*255, n.length > 3 ? n[3] : 1];
    return n;
  };
  // Walk up for the first opaque ancestor: the element's own background is
  // usually transparent, and scoring text against transparent silently
  // passes everything.
  const bgOf = el => { let n = el;
    while (n) { const p = parse(getComputedStyle(n).backgroundColor);
      if (p.length >= 3 && (p.length < 4 || p[3] > 0.5)) return p;
      n = n.parentElement; }
    return [12,15,22]; };
  const out = [];
  document.querySelectorAll('.wrap *').forEach(el => {
    if (!el.offsetParent) return;
    const direct = [...el.childNodes].some(
      n => n.nodeType === 3 && n.textContent.trim().length > 1);
    if (!direct) return;
    const cs = getComputedStyle(el);
    const fg = parse(cs.color), bg = bgOf(el);
    if (fg.length < 3) return;
    const l1 = lum(fg[0],fg[1],fg[2]), l2 = lum(bg[0],bg[1],bg[2]);
    const ratio = (Math.max(l1,l2)+0.05)/(Math.min(l1,l2)+0.05);
    const px = parseFloat(cs.fontSize);
    const large = px >= 24 || (px >= 18.66 && parseInt(cs.fontWeight) >= 700);
    out.push({ t: el.textContent.trim().slice(0,34), ratio: +ratio.toFixed(2),
               px: +px.toFixed(1),
               // The two colours, so a failure says WHICH pair is wrong
               // rather than only how wrong it is. A ratio on its own sends
               // the reader hunting through the token file.
               fg: 'rgb(' + fg.slice(0,3).join(',') + ')',
               bg: 'rgb(' + bg.slice(0,3).join(',') + ')',
               cls: el.className && el.className.toString
                      ? el.className.toString().slice(0,24) : '',
               aa: ratio >= (large?3:4.5), aaa: ratio >= (large?4.5:7) });
  });
  return out;
}"""



def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("  %-44s %s %s" % (name, "PASS" if ok else "FAIL", detail))
    return ok


def bail(msg):
    print("\n%s" % msg)
    sys.exit(2)


if not os.path.isdir(BUILD):
    bail("No build at app/build. Run:  cd app && npm run build")

try:
    import secrets
except ImportError:
    bail("secrets.py is required for the broker account.")

try:
    import paho.mqtt.client as mqtt
    from playwright.sync_api import sync_playwright
except ImportError as e:
    bail("missing dependency: %s" % e)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from seed_history import build as build_history            # noqa: E402

os.makedirs(SHOTS, exist_ok=True)
print("app regression against %s\n" % secrets.MQTT_HOST)

# --- a witness on the broker, independent of the app ------------------------
# The app's own screen cannot prove the fan got a command: the app would be
# marking its own homework. This subscriber is the second opinion.
latest = {"state": None, "payload": None, "any_voc": False}


def on_connect(c, u, f, rc):
    c.subscribe("fan/bathroom/state")


def on_message(c, u, m):
    try:
        d = json.loads(m.payload.decode())
        latest["payload"] = d
        latest["state"] = d.get("state")
        # voc_ok, not voc: voc is None through the sensor's 10 s conditioning
        # window and whenever a read is dropped, while voc_ok is non-null
        # for as long as an SGP41 is fitted at all. The app's charts follow
        # whether odor data exists anywhere it can see -- the live payload
        # OR the week of rows backfilled off the device's flash -- so the
        # question the test has to ask is "is a sensor fitted", and only
        # voc_ok answers that without racing the conditioning window.
        if d.get("voc") is not None or d.get("voc_ok") is not None:
            latest["any_voc"] = True
    except Exception:
        pass


witness = mqtt.Client(client_id="smartfan-app-test-witness")
witness.username_pw_set(secrets.MQTT_USER, secrets.MQTT_PASS)
witness.on_connect, witness.on_message = on_connect, on_message
witness.connect(secrets.MQTT_HOST, getattr(secrets, "MQTT_PORT", 1883), 30)
witness.loop_start()


def wait_for(pred, timeout=60, poll=0.5):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(poll)
    return False


check("device is publishing", wait_for(lambda: latest["state"] is not None, 45),
      latest["state"] or "nothing in 45 s")
if latest["state"] is None:
    witness.loop_stop()
    bail("No telemetry. Is the fan online?")

started_in = latest["state"]

# --- serve the build --------------------------------------------------------
handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                            directory=BUILD)


class Quiet(socketserver.TCPServer):
    allow_reuse_address = True


httpd = Quiet(("127.0.0.1", PORT), handler)
httpd.RequestHandlerClass.log_message = lambda *a, **k: None
threading.Thread(target=httpd.serve_forever, daemon=True).start()

# Seeded in broker mode. The app now defaults to talking straight to the fan
# over HTTP, which is the right default and the wrong thing for this suite:
# these tests exist to prove the broker path still works end to end, against
# the live broker and the live fan. The device path has its own suite in
# tests/test_httpd.py.
# One always-present state card, plus the charts the app builds from CHARTS
# in +page.svelte. Counted here rather than written as a total, because a
# total is the thing that goes stale when a chart is added. The odor pair
# is unconditional: tests/seed_history.py seeds odor rows, so the app has
# odor data whether or not a sensor is currently fitted.
BASE_CHARTS = 5          # humidity, excess, temperature, light, fan speed
ODOR_CHARTS = 2          # odor against baseline, excess odor


cfg = {"mode": "broker",
       "host": secrets.MQTT_HOST, "port": 1884, "room": "bathroom",
       "username": secrets.MQTT_USER, "password": secrets.MQTT_PASS}

errors = []

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=not HEADED)
    page = browser.new_page(viewport={"width": 420, "height": 900})
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console",
            lambda m: errors.append(m.text) if m.type == "error" else None)
    # Charts are fed from a buffer this browser accumulates, so a fresh
    # profile has nothing to draw. Seed a shower shaped like the one this
    # bathroom actually produces, rather than testing the empty state and
    # calling the charts covered.
    page.add_init_script(
        "localStorage.setItem('smartfan.settings', %s);"
        "localStorage.setItem('smartfan.history', %s);"
        % (json.dumps(json.dumps(cfg)),
           json.dumps(json.dumps(build_history()))))

    page.goto("http://127.0.0.1:%d/" % PORT, wait_until="load")

    # wait_for(), not is_visible(). is_visible() answers instantly about
    # right now, and goto(wait_until="load") returns before Svelte has
    # hydrated -- so the naive check reports FAIL on an app that is about to
    # render perfectly well. Every assertion after these two auto-waited and
    # passed, which is exactly what that mistake looks like.
    def appears(locator, timeout=45000):
        try:
            locator.wait_for(state="visible", timeout=timeout)
            return True
        except Exception:
            return False

    check("app loads", appears(page.locator("h1"), 15000))
    check("connects to the broker",
          appears(page.get_by_text("Live", exact=True).first))

    # --- navigation -------------------------------------------------------
    def go(label):
        page.get_by_role("button", name=label, exact=True).first.click()
        page.wait_for_timeout(250)

    for label in ("Status", "Readings", "Thresholds", "Charts", "Health"):
        check("section reachable: %s" % label,
              page.get_by_role("button", name=label, exact=True).count() > 0)

    # --- the screen agrees with the wire ---------------------------------
    p = latest["payload"]
    go("Status")
    shown_state = page.locator(".state").inner_text().strip()
    check("controller state matches telemetry",
          shown_state == p["state"], "%s vs %s" % (shown_state, p["state"]))

    # The at-a-glance strip carries every input that can start the fan. An
    # odor sensor that only appears further down the page is a sensor
    # nobody looks at, so it is checked here beside fan and light rather
    # than only in its own panel.
    #
    # Lowercased before matching: inner_text() returns text AS RENDERED and
    # these keys are uppercased in CSS, the same trap as the boost label
    # below.
    strip = page.locator(".facts").first.inner_text().lower()
    for label in ("Fan", "Light", "Odor", "Fault"):
        check("status strip carries %s" % label, label.lower() in strip)
    if latest["any_voc"]:
        check("the odor tile says something",
              not any(w in strip for w in ("no sensor", "sensor failed")),
              " | ".join(strip.splitlines()))

    go("Readings")
    # Bracketed, because `latest` keeps moving: the subscriber thread folds
    # in a new payload whenever the device publishes, so pinning this to the
    # sample captured before the navigation is a race on whether a publish
    # landed in between. The point is that the screen shows a reading THIS
    # device sent, which any of the samples around the read establishes.
    before = latest["payload"]
    body = page.locator(".wrap").inner_text()
    after = latest["payload"]
    rh_seen = {"%.1f%%" % s["rh"] for s in (p, before, after)
               if s and s.get("rh") is not None}
    check("humidity matches telemetry", any(v in body for v in rh_seen),
          "screen shows none of %s" % sorted(rh_seen))
    # Same bracketing for the same reason. The raw light reading in
    # particular moves on every sample, and it has already failed a run
    # here purely on a publish landing mid-navigation.
    def any_of(fmt, key):
        vals = {fmt(s[key]) for s in (p, before, after)
                if s and s.get(key) is not None}
        return any(v in body for v in vals), sorted(vals)

    ok, vals = any_of(lambda v: "%.1f%%" % v, "base")
    check("baseline is shown in its own right", ok,
          "screen shows none of %s" % vals)
    ok, vals = any_of(lambda v: "{:,}".format(v), "light")
    check("raw light is shown in its own right", ok,
          "screen shows none of %s" % vals)
    # Only rendered while boosting before; a sensor that vanishes when its
    # value is uninteresting still has to have somewhere to live.
    #
    # Matched case-insensitively: inner_text() returns text AS RENDERED, and
    # these labels are uppercased by text-transform in CSS. The DOM says
    # "Boost remaining", the browser reports "BOOST REMAINING", and a
    # case-sensitive check fails on a label that is plainly on screen.
    check("boost remaining has a home when idle",
          "boost remaining" in body.lower())

    go("Health")
    body = page.locator(".wrap").inner_text()
    check("build fingerprint matches telemetry", p["fw"] in body, p["fw"])
    check("last restart is shown in its own right", p["boot"] in body,
          p["boot"])

    go("Thresholds")
    # A threshold on screen that disagrees with the device is worse than no
    # threshold shown: it describes a fan that does not exist.
    rise = page.locator("#RH_RISE_ON").input_value()
    check("thresholds match the device's own config",
          float(rise) == float(p["cfg"]["RH_RISE_ON"]),
          "%s vs %s" % (rise, p["cfg"]["RH_RISE_ON"]))

    # The odor pair has to be tunable from here too. They were announced to
    # Home Assistant from the start but missing from this page, so the one
    # threshold most likely to need a field adjustment was the one that
    # needed a second app to adjust it.
    vrise = page.locator("#VOC_RISE_ON").input_value()
    vfall = page.locator("#VOC_FALL_OFF").input_value()
    check("the odor thresholds are on the page, and match the device",
          float(vrise) == float(p["cfg"]["VOC_RISE_ON"])
          and float(vfall) == float(p["cfg"]["VOC_FALL_OFF"]),
          "%s/%s vs %s/%s" % (vrise, vfall, p["cfg"]["VOC_RISE_ON"],
                              p["cfg"]["VOC_FALL_OFF"]))

    # --- charts -----------------------------------------------------------
    go("Charts")
    cards = page.locator(".chart-card")
    # Deterministic, because the fixture carries all fifteen columns
    # including the odor triple. This used to be predicted from the live
    # sensor, and it was wrong in both directions within one afternoon:
    # the sensor publishes None through its 10 s conditioning window, and
    # then the part was unplugged mid-session. The charts follow the DATA,
    # so giving the fixture the data is what makes the count knowable.
    expect = 1 + BASE_CHARTS + ODOR_CHARTS
    check("every chart renders", cards.count() == expect,
          "%d cards, expected %d" % (cards.count(), expect))
    # A chart that silently falls back to the empty state still "renders".
    check("charts actually draw data",
          page.locator(".chart-card svg path.line").count() >= 5,
          "%d lines" % page.locator(".chart-card svg path.line").count())
    check("no chart is stuck on the empty state",
          page.locator(".chart-card .nodata").count() == 0)

    # By name, not position: the activity timeline is the first card, and
    # Excess is the one whose detail has to draw the thresholds.
    page.get_by_role("button", name="Excess above baseline").click()
    page.wait_for_timeout(400)
    check("tapping a chart opens detail",
          page.get_by_role("dialog", name="Excess above baseline").count() > 0)
    # The thresholds are the whole point of that chart: without them the
    # reader sees the shape but not whether it mattered.
    check("detail draws the fan's thresholds",
          page.locator(".sheet svg line.ref").count() == 2,
          "%d lines" % page.locator(".sheet svg line.ref").count())
    check("detail reports min, average and max",
          "avg" in page.locator(".stats").inner_text())

    six = page.locator(".samples").inner_text()
    page.get_by_role("button", name="1 h", exact=True).click()
    page.wait_for_timeout(300)
    one = page.locator(".samples").inner_text()
    check("range buttons change the window", six != one, "%s -> %s" % (six, one))

    # Exactly one control announces as "Close chart". The backdrop used to
    # carry the same role and label, which put two identical controls in
    # the accessibility tree.
    closers = page.get_by_role("button", name="Close chart")
    check("only one control is labelled Close chart", closers.count() == 1,
          "%d found" % closers.count())
    closers.click()
    page.wait_for_timeout(250)
    check("detail closes", page.locator(".sheet.wide").count() == 0)

    cards.nth(0).click()
    page.wait_for_timeout(300)
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    check("escape closes the detail", page.locator(".sheet.wide").count() == 0)

    go("Status")

    check("no horizontal scroll at 420px",
          page.evaluate("document.documentElement.scrollWidth") <= 420,
          "%dpx" % page.evaluate("document.documentElement.scrollWidth"))

    page.screenshot(path=os.path.join(SHOTS, "app.png"), full_page=True)
    print("     -> screenshots/app.png")

    # --- a control actually reaches the fan -------------------------------
    latest["state"] = None
    page.get_by_role("button", name="Boost").first.click()
    got_boost = wait_for(lambda: latest["state"] == "BOOST", 45)
    check("pressing Boost reaches the fan", got_boost,
          latest["state"] or "no change in 45 s")

    if got_boost:
        check("fan is actually running during boost",
              bool(latest["payload"].get("fan_on")),
              "fan_on=%s" % latest["payload"].get("fan_on"))
        check("the app shows the boost",
              appears(page.get_by_text("BOOST", exact=True).first))
        # Configured to appear only while boosting.
        check("cancel becomes available while boosting",
              page.get_by_role("button",
                               name="Cancel boost").first.is_enabled())

        page.screenshot(path=os.path.join(SHOTS, "app-boosting.png"),
                        full_page=True)
        print("     -> screenshots/app-boosting.png")

        latest["state"] = None
        page.get_by_role("button", name="Cancel boost").first.click()
        check("cancel returns it to automatic",
              wait_for(lambda: latest["state"] not in (None, "BOOST"), 45),
              latest["state"] or "still boosting")

    # --- settings round-trip ----------------------------------------------
    page.get_by_role("button", name="Settings").click()
    check("settings opens", page.locator("#host").is_visible())
    check("settings shows the port in use",
          page.locator("#port").input_value() == "1884")
    page.get_by_role("button", name="Cancel", exact=True).click()

    # --- contrast, measured on the rendered page ---------------------
    # Not against the token list: a token that passes in isolation still
    # fails when it lands on a different surface, and the palette is
    # deliberately divergent from the brand's on exactly this point.
    rows = []
    for label in ("Status", "Readings", "Thresholds", "Charts", "Health"):
        go(label)
        rows += page.evaluate(CONTRAST_AUDIT)
    go("Status")
    aa = [r for r in rows if not r["aa"]]
    aaa = [r for r in rows if not r["aaa"]]
    worst = min((r["ratio"] for r in rows), default=0)
    # Named, not counted. "25 of 268 fail" says nothing a reader can act
    # on; the ratio, the size and the text locate the rule to change.
    def worst_few(bad, n=4):
        seen, out = set(), []
        for r in sorted(bad, key=lambda r: r["ratio"]):
            key = (r["ratio"], r["t"][:18])
            if key in seen:
                continue
            seen.add(key)
            out.append("%.2f:1 %.0fpx %r %s on %s [%s]"
                       % (r["ratio"], r["px"], r["t"][:18],
                          r.get("fg"), r.get("bg"), r.get("cls")))
            if len(out) >= n:
                break
        return "; ".join(out)

    check("every text element passes WCAG AA", not aa,
          "%d of %d fail -- %s" % (len(aa), len(rows), worst_few(aa)) if aa
          else "%d elements, all views" % len(rows))
    check("every text element passes WCAG AAA", not aaa,
          "%d of %d fail -- %s" % (len(aaa), len(rows), worst_few(aaa)) if aaa
          else "lowest %.2f:1 across all views" % worst)

    check("no uncaught JS errors", not errors, "; ".join(errors[:2]))

    # --- first run on a browser that has never seen this app -------------
    # A fresh context, so no localStorage. The app shipped connecting with
    # blank credentials against a broker that requires them, which greeted
    # every new user with an authentication failure as the first thing on
    # screen -- "broken" rather than "not set up yet".
    fresh = browser.new_context(viewport={"width": 393, "height": 852})
    first = fresh.new_page()
    first_errors = []
    first.on("pageerror", lambda e: first_errors.append(str(e)))
    first.goto("http://127.0.0.1:%d/" % PORT, wait_until="load")
    first.wait_for_timeout(4000)

    first_body = first.locator(".wrap").inner_text()
    check("first run opens settings", first.locator("#host").is_visible())
    check("first run offers setup, not an error",
          "Set up connection" in first_body or
          "Point the app at your fan" in first_body)
    check("first run shows no broker rejection",
          "reject" not in first_body.lower(), first_body[:60])

    # The default is the fan itself, because that is the setup that needs
    # nothing else running. Served from 127.0.0.1 this page cannot have come
    # off a device, so it still asks -- what it must not do is ask for broker
    # credentials as though they were the only way in.
    check("first run offers both ways to connect",
          first.get_by_role("radio", name="The fan itself").is_visible() and
          first.get_by_role("radio", name="An MQTT broker").is_visible())
    check("the fan itself is the default",
          first.get_by_role("radio", name="The fan itself")
          .get_attribute("aria-checked") == "true")
    check("device mode asks for an address and no password",
          first.locator("#httpPort").is_visible() and
          first.locator("#pass").count() == 0)

    first.get_by_role("radio", name="An MQTT broker").click()
    check("choosing a broker prefills its defaults",
          first.locator("#port").input_value() == "1884")
    check("choosing a broker asks for credentials",
          first.locator("#pass").is_visible())
    check("no JS errors on first run", not first_errors,
          "; ".join(first_errors[:2]))
    fresh.close()

    browser.close()

httpd.shutdown()

# --- leave the fan as it was found -----------------------------------------
if latest["state"] == "BOOST":
    witness.publish("fan/bathroom/cmd", "cancel")
    wait_for(lambda: latest["state"] != "BOOST", 30)
witness.loop_stop()
print("\nstarted in %s, left in %s" % (started_in, latest["state"]))

passed = sum(1 for _, ok, _ in results if ok)
print("%d/%d passed" % (passed, len(results)))
fails = [n for n, ok, _ in results if not ok]
if fails:
    print("failed: " + ", ".join(fails))
sys.exit(0 if not fails else 1)
