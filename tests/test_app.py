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

os.makedirs(SHOTS, exist_ok=True)
print("app regression against %s\n" % secrets.MQTT_HOST)

# --- a witness on the broker, independent of the app ------------------------
# The app's own screen cannot prove the fan got a command: the app would be
# marking its own homework. This subscriber is the second opinion.
latest = {"state": None, "payload": None}


def on_connect(c, u, f, rc):
    c.subscribe("fan/bathroom/state")


def on_message(c, u, m):
    try:
        d = json.loads(m.payload.decode())
        latest["payload"] = d
        latest["state"] = d.get("state")
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

cfg = {"host": secrets.MQTT_HOST, "port": 1884, "room": "bathroom",
       "username": secrets.MQTT_USER, "password": secrets.MQTT_PASS}

errors = []

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=not HEADED)
    page = browser.new_page(viewport={"width": 420, "height": 900})
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console",
            lambda m: errors.append(m.text) if m.type == "error" else None)
    page.add_init_script(
        "localStorage.setItem('smartfan.settings', %s)"
        % json.dumps(json.dumps(cfg)))

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

    # --- the screen agrees with the wire ---------------------------------
    p = latest["payload"]
    shown_state = page.locator(".state").inner_text().strip()
    check("controller state matches telemetry",
          shown_state == p["state"], "%s vs %s" % (shown_state, p["state"]))

    body = page.locator(".wrap").inner_text()
    check("humidity matches telemetry", "%.1f%%" % p["rh"] in body,
          "%.1f%%" % p["rh"])
    check("build fingerprint matches telemetry", p["fw"] in body, p["fw"])

    # A threshold on screen that disagrees with the device is worse than no
    # threshold shown: it describes a fan that does not exist.
    rise = page.locator("#RH_RISE_ON").input_value()
    check("thresholds match the device's own config",
          float(rise) == float(p["cfg"]["RH_RISE_ON"]),
          "%s vs %s" % (rise, p["cfg"]["RH_RISE_ON"]))

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
    page.get_by_role("button", name="Connection settings").click()
    check("settings opens", page.locator("#host").is_visible())
    check("settings shows the port in use",
          page.locator("#port").input_value() == "1884")
    page.get_by_role("button", name="Cancel", exact=True).click()

    check("no uncaught JS errors", not errors, "; ".join(errors[:2]))
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
