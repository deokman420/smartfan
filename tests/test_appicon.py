"""The tab icon and the status-bar tint, in a real browser.

    python tests/test_appicon.py [--headed]

Needs a built app (`cd app && npm run build`) and playwright. Deliberately
needs NOTHING else -- no broker, no fan, no secrets.py -- because the thing
under test is a function of one boolean, and wiring a real fan into it would
only add ways for the test to fail that have nothing to do with the icon.

The module is loaded verbatim as a blob module rather than reached through
the bundle, so this tests app/src/lib/appicon.js as written and does not care
what vite named the chunk it landed in.

It runs against the built index.html with its <script> tags stripped, which
is not squeamishness about the bundle: +layout.svelte starts a watcher of its
own, and two of them on one page overwrite each other's <head>. The rest of
the shell is the real artifact -- the same icon link, the same theme-color
tag, the same tokens.css -- so a change to any of those still fails here.

What is worth asserting here
----------------------------
That it paints, that it stops, and that it cleans up after itself. The
favicon is decoration, and decoration that leaks a 13 fps timer into a
backgrounded tab for the life of a session is worse than no decoration.
"""

import http.server
import io
import json
import os
import re
import socketserver
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "app", "build")
SRC = os.path.join(ROOT, "app", "src", "lib", "appicon.js")
LED_SRC = os.path.join(ROOT, "app", "src", "lib", "statusled.js")
SHOTS = os.path.join(ROOT, "tests", "screenshots")
PORT = 8779
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
    from playwright.sync_api import sync_playwright
except ImportError as e:
    bail("missing dependency: %s" % e)

os.makedirs(SHOTS, exist_ok=True)

HARNESS_PATH = "/appicon-harness.html"


class Handler(http.server.SimpleHTTPRequestHandler):
    """The built app, plus one page: the real shell with no app on it."""

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=BUILD, **kw)

    def log_message(self, *a):
        pass                    # the request log is noise against 18 checks

    def do_GET(self):
        if self.path != HARNESS_PATH:
            return super().do_GET()
        html = io.open(os.path.join(BUILD, "index.html"), encoding="utf-8").read()
        html = re.sub(r"<script\b.*?</script>", "", html, flags=re.S)
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


socketserver.TCPServer.allow_reuse_address = True
httpd = socketserver.TCPServer(("127.0.0.1", PORT), Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()

# A fake store, so the test drives the one input the module has. Same shape
# Svelte's own stores expose: subscribe(fn) -> unsubscribe, called at once
# with the current value.
#
# appicon.js imports $lib/statusled.js, which a blob module cannot resolve,
# so that one specifier is pointed at a blob of the real file -- still the
# source as written, just reached by a different URL.
HARNESS = """
  const blob = (s) => URL.createObjectURL(new Blob([s], {type: 'text/javascript'}));
  const ledUrl = blob(%s);
  const src = %s.replace("'$lib/statusled.js'", JSON.stringify(ledUrl));
  const mod = await import(blob(src));
  const store = (init) => {
    let v = init;
    const subs = new Set();
    return {
      set(next) { v = next; subs.forEach((f) => f(v)); },
      subscribe(f) { subs.add(f); f(v); return () => subs.delete(f); }
    };
  };
  const fan = store(false);
  const led = store(null);
  window.__set = (v) => fan.set(v);
  window.__led = (c) => led.set(c);
  window.__release = mod.watchAppIcon(fan, led);
"""

PROBE = """() => ({
  icon: document.querySelector('link[rel=icon]').href,
  theme: document.querySelector('meta[name=theme-color]').content,
  fan: document.documentElement.dataset.fan ?? null,
  bg: getComputedStyle(document.body).backgroundImage
})"""

# Chromium reports document.visibilityState from the real page state, which
# a headless run cannot background. Overriding the property and firing the
# event is what the browser itself does, and it is the listener we are
# testing, not Chromium's bookkeeping.
VISIBILITY = """(state) => {
  Object.defineProperty(document, 'visibilityState',
                        {value: state, configurable: true});
  document.dispatchEvent(new Event('visibilitychange'));
}"""

print("app icon regression\n")

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=not HEADED)
    page = browser.new_page(viewport={"width": 420, "height": 820})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto("http://127.0.0.1:%d%s" % (PORT, HARNESS_PATH), wait_until="load")

    src = io.open(SRC, encoding="utf-8").read()
    led_src = io.open(LED_SRC, encoding="utf-8").read()
    page.add_script_tag(type="module",
                        content=HARNESS % (json.dumps(led_src), json.dumps(src)))
    page.wait_for_function("typeof window.__release === 'function'", timeout=10000)

    idle = page.evaluate(PROBE)
    check("starts on the shipped icon", idle["icon"].endswith("icon.svg"), idle["icon"])
    check("starts on the shipped theme-color", idle["theme"] == "#141820", idle["theme"])
    check("no data-fan while idle", idle["fan"] is None)

    page.evaluate("window.__set(true)")
    page.wait_for_function(
        "document.querySelector('link[rel=icon]').href.startsWith('data:')",
        timeout=10000,
    )
    running = page.evaluate(PROBE)
    check("favicon becomes a rendered frame",
          running["icon"].startswith("data:image/png"))
    check("theme-color goes to --bar-active", running["theme"] == "#113233",
          running["theme"])
    check("data-fan set for the iOS backdrop", running["fan"] == "on")
    check("backdrop strengthens its teal stop",
          "rgba(0, 212, 170, 0.1)" in running["bg"], running["bg"][:48])

    # Animating at all. Two reads a few frames apart that match would mean a
    # still image on a timer, which is the failure this is here to catch.
    first = page.evaluate("document.querySelector('link[rel=icon]').href")
    time.sleep(0.25)
    second = page.evaluate("document.querySelector('link[rel=icon]').href")
    check("frames advance", first != second)

    page.screenshot(path=os.path.join(SHOTS, "appicon-running.png"))

    page.evaluate(VISIBILITY, "hidden")
    time.sleep(0.3)
    check("a backgrounded tab stops spinning",
          page.evaluate("document.querySelector('link[rel=icon]').href").endswith("icon.svg"))

    page.evaluate(VISIBILITY, "visible")
    time.sleep(0.3)
    check("coming back to the tab resumes it",
          page.evaluate("document.querySelector('link[rel=icon]').href").startswith("data:"))

    page.evaluate("window.__set(false)")
    time.sleep(0.2)
    off = page.evaluate(PROBE)
    check("fan off restores the icon", off["icon"].endswith("icon.svg"))
    check("fan off restores theme-color", off["theme"] == "#141820", off["theme"])
    check("fan off clears data-fan", off["fan"] is None)

    # -- the status LED ---------------------------------------------------
    # Fan off throughout, so any change in the icon is the LED's doing.
    ICON = "document.querySelector('link[rel=icon]').href"

    def corner():
        """The LED's pixel, read back off the favicon the tab would show."""
        return page.evaluate("""async () => {
          const img = new Image();
          img.src = document.querySelector('link[rel=icon]').href;
          await img.decode();
          const c = document.createElement('canvas');
          c.width = c.height = 64;
          const x = c.getContext('2d');
          x.drawImage(img, 0, 0, 64, 64);
          return Array.from(x.getImageData(54, 54, 1, 1).data.slice(0, 3));
        }""")

    def hue(rgb):
        # Green, red or dark: the board has nothing else, and anything else
        # (a yellow creeping back in) comes out as "?" and fails its check.
        r, g, b = rgb
        if r > 180 and g < 110:
            return "red"
        if g > 140 and r < 80:
            return "green"
        if max(rgb) < 70:
            return "off"
        return "?%s" % (rgb,)

    def sample(n=8, gap=0.2):
        seen = set()
        for _ in range(n):
            seen.add(hue(corner()))
            time.sleep(gap)
        return seen

    page.evaluate("window.__led('idle')")
    page.wait_for_function(ICON + ".startsWith('data:')", timeout=10000)
    check("idle LED is green, fan off", hue(corner()) == "green", str(corner()))

    page.evaluate("window.__led('run')")
    time.sleep(0.1)
    check("run LED is red", hue(corner()) == "red", str(corner()))

    # Red double-blink: two lit beats in six, so dark should dominate.
    page.evaluate("window.__led('unseen')")
    hits = []
    for _ in range(15):
        hits.append(hue(corner()))
        time.sleep(0.2)
    check("no current reading double-blinks red",
          set(hits) == {"red", "off"} and hits.count("off") > hits.count("red"),
          "%d red / %d dark" % (hits.count("red"), hits.count("off")))

    page.evaluate("window.__led('fault')")
    seen = sample()
    check("fault alternates green and red", seen == {"green", "red"}, str(sorted(seen)))
    page.screenshot(path=os.path.join(SHOTS, "appicon-fault.png"))

    page.evaluate("window.__led('learn')")
    seen = sample()
    check("learn blinks green against dark", seen == {"green", "off"}, str(sorted(seen)))

    # The whole point of the LED is the tab strip, which is a hidden tab.
    page.evaluate(VISIBILITY, "hidden")
    time.sleep(0.2)
    check("a backgrounded tab keeps the LED", page.evaluate(ICON).startswith("data:"))
    page.evaluate(VISIBILITY, "visible")

    page.emulate_media(reduced_motion="reduce")
    page.evaluate("window.__led('fault')")
    seen = sample(6)
    check("reduced motion holds fault steady red", seen == {"red"}, str(sorted(seen)))
    page.evaluate("window.__led('attention')")
    seen = sample(6)
    check("reduced motion holds attention dark, not RUN red", seen == {"off"},
          str(sorted(seen)))
    page.emulate_media(reduced_motion="no-preference")

    page.evaluate("window.__led(null)")
    time.sleep(0.2)
    check("no code puts the plain icon back", page.evaluate(ICON).endswith("icon.svg"))

    # Teardown from the running state, which is the case that can leak --
    # with the LED blinking too, so both timers are in play.
    page.evaluate("window.__led('fault')")
    page.evaluate("window.__set(true)")
    page.wait_for_function(
        "document.querySelector('link[rel=icon]').href.startsWith('data:')",
        timeout=10000,
    )
    page.evaluate("window.__release()")
    time.sleep(0.4)
    gone = page.evaluate(PROBE)
    check("teardown restores the icon", gone["icon"].endswith("icon.svg"), gone["icon"][:40])
    check("teardown restores theme-color", gone["theme"] == "#141820")
    check("teardown clears data-fan", gone["fan"] is None)
    still = page.evaluate("document.querySelector('link[rel=icon]').href")
    time.sleep(0.3)
    check("teardown leaves no timer running",
          still == page.evaluate("document.querySelector('link[rel=icon]').href"))

    check("no page errors", not errors, "; ".join(errors[:2]))
    browser.close()

httpd.shutdown()

failed = [n for n, ok, _ in results if not ok]
print("\n%d/%d passed" % (len(results) - len(failed), len(results)))
sys.exit(1 if failed else 0)
