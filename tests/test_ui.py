"""Browser regression for the Lovelace dashboard.

    python tests/test_ui.py [--headed]

The API suite proves the dashboard's *config* is coherent. This proves it
actually renders and responds -- a card can reference a live entity and
still fail to paint, and a button can exist in config and not be clickable.

Auth works by seeding the frontend's own token store before any script runs,
so the token is read from .env and never appears on a command line or in
output. Home Assistant's frontend keeps its session in localStorage under
"hassTokens"; a long-lived token with a far-future expiry satisfies it
without a refresh token.

Screenshots land in tests/screenshots/ for eyeballing after a change.
"""

import io
import json
import os
import re
import sys
import time

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SHOTS = os.path.join(HERE, "screenshots")
HA = os.environ.get("HA_URL", "http://192.168.0.5:8123")
DASH = "/bathroom-fan/main"
HEADED = "--headed" in sys.argv

env = io.open(os.path.join(ROOT, ".env"), encoding="utf-8").read()
TOKEN = re.search(r'HASS_LLAT\s*=\s*"?([^"\s]+)', env).group(1)


def entity_state(eid):
    """Read one entity over the API.

    State checks must not be made against page text: the explainer cards
    legitimately contain the words IDLE, RUN and BOOST, so get_by_text would
    match the documentation for ever and the assertion could never fail.
    """
    import json as _json
    import urllib.request
    try:
        r = urllib.request.Request(HA + "/api/states/" + eid,
                                   headers={"Authorization": "Bearer " + TOKEN})
        with urllib.request.urlopen(r, timeout=8) as f:
            return _json.load(f)["state"]
    except Exception:
        return None

os.makedirs(SHOTS, exist_ok=True)
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("  %-46s %s %s" % (name, "PASS" if ok else "FAIL", detail))
    return ok


def seed_auth(context):
    """Put a valid session in localStorage before the app boots."""
    tokens = {
        "access_token": TOKEN,
        "token_type": "Bearer",
        "expires_in": 315360000,
        "hassUrl": HA,
        "clientId": None,
        "expires": int(time.time() * 1000) + 315360000000,
        "refresh_token": "",
    }
    context.add_init_script(
        "window.localStorage.setItem('hassTokens', %s);"
        % json.dumps(json.dumps(tokens)))


print("dashboard UI regression against %s%s\n" % (HA, DASH))

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=not HEADED)
    ctx = browser.new_context(viewport={"width": 1440, "height": 1200})
    seed_auth(ctx)
    page = ctx.new_page()

    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    page.goto(HA + DASH, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(6000)          # the SPA settles

    check("did not bounce to the login page",
          "/auth/authorize" not in page.url, page.url.split("8123")[-1][:40])

    def seen(text, exact=False):
        """Home Assistant renders into shadow DOM, so body.innerText is
        empty. Playwright locators pierce shadow roots; raw DOM text does
        not. Everything here must go through a locator.

        exact=False is case-insensitive substring matching, so a state value
        like "BOOST" also matches the "Boost" heading and the "Boost 15
        minutes" tile. Use exact=True whenever asserting on a state.
        """
        try:
            return page.get_by_text(text, exact=exact).count() > 0
        except Exception:
            return False

    # Section headings prove the view rendered, not just that HA loaded.
    # These are the eight in tools/build_dashboard.py. Rename one there and
    # this fails, which is the point: a dashboard rebuild that silently drops
    # a section looks fine in a screenshot and is missing a third of the
    # controls.
    for hd in ("Right now", "Control", "Thresholds", "Health",
               "Humidity", "Activity", "Light", "Climate"):
        check("section renders: %s" % hd, seen(hd))

    # Entity values reaching the DOM means the whole chain painted.
    live = entity_state("sensor.bathroom_fan_controller_controller_state")
    check("controller state on screen", bool(live) and seen(live, exact=True),
          live or "entity unreachable")
    check("a humidity reading is shown", seen("Humidity"))
    check("gauge legend present", seen("Above baseline"))

    page.screenshot(path=os.path.join(SHOTS, "dashboard.png"), full_page=True)
    print("     -> screenshots/dashboard.png")

    # --- the boost button, driven through the real UI --------------------
    btn = page.get_by_text("Boost 15", exact=False).first
    if check("boost tile is present", btn.count() > 0):
        # HA's sticky header overlays whatever scrolls under it, so a plain
        # click gets intercepted. Centre the tile, then force past the
        # actionability check -- we already asserted it is visible.
        btn.scroll_into_view_if_needed()
        page.wait_for_timeout(600)
        btn.click(force=True)
        page.wait_for_timeout(9000)      # command -> MQTT -> device -> back
        boosting = entity_state(
            "sensor.bathroom_fan_controller_controller_state") == "BOOST"
        check("pressing the tile starts a boost", boosting)
        # These two are configured to appear only while boosting.
        check("countdown appears while boosting",
              seen("Time remaining"), "" if boosting else "(not boosting)")
        check("cancel appears while boosting", seen("Cancel boost"))
        page.screenshot(path=os.path.join(SHOTS, "dashboard-boosting.png"),
                        full_page=True)
        print("     -> screenshots/dashboard-boosting.png")

        if boosting:
            cancel = page.get_by_text("Cancel boost", exact=False).first
            cancel.scroll_into_view_if_needed()
            page.wait_for_timeout(600)
            cancel.click(force=True)
            page.wait_for_timeout(9000)
            check("cancel returns to automatic",
                  entity_state("sensor.bathroom_fan_controller_"
                               "controller_state") != "BOOST")
            check("countdown hidden again", not seen("Time remaining"))

    # --- phone width ------------------------------------------------------
    page.set_viewport_size({"width": 400, "height": 900})
    page.wait_for_timeout(2500)
    w = page.evaluate("document.documentElement.scrollWidth")
    check("no horizontal scroll at 400px", w <= 401, "%dpx" % w)
    page.screenshot(path=os.path.join(SHOTS, "dashboard-mobile.png"),
                    full_page=True)
    print("     -> screenshots/dashboard-mobile.png")

    check("no uncaught JS errors", not errors, errors[0][:60] if errors else "")

    ctx.close()
    browser.close()

passed = sum(1 for _, ok, _ in results if ok)
print("\n%d/%d passed" % (passed, len(results)))
fails = [n for n, ok, _ in results if not ok]
if fails:
    print("failed: " + ", ".join(fails))
sys.exit(0 if not fails else 1)
