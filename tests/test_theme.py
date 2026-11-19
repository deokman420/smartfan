"""Light, dark and system, in a real browser.

    python tests/test_theme.py [--headed]

Needs a built app (`cd app && npm run build`) and playwright. No broker, no
fan: the scheme is a function of one saved string and one OS preference, and
neither of them involves the device.

What is worth asserting here
----------------------------
Not "does light mode look light" -- the screenshots show that, and
check-contrast.mjs proves the palette clears AAA without a browser at all.
What only a browser can prove is the wiring:

  * that the saved choice survives a reload, and survives it WITHOUT a flash
    of the wrong scheme -- the inline script in app.html is the only thing
    standing between a light-mode user and a dark frame on every load, and
    it is duplicated from theme.js, so it is exactly the kind of thing that
    rots silently;
  * that `system` actually follows the OS, and keeps following it while the
    app is open rather than only reading it once at boot;
  * that the status-bar tint tracks the scheme as well as the fan, which is
    two independent inputs into one <meta> tag.
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
SHOTS = os.path.join(ROOT, "tests", "screenshots")
PORT = 8781
HEADED = "--headed" in sys.argv

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("  %-52s %s %s" % (name, "PASS" if ok else "FAIL", detail))
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


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=BUILD, **kw)

    def log_message(self, *a):
        pass

    def handle_one_request(self):
        # Closing a browser context aborts whatever it was still fetching, and
        # the default handler prints a full traceback for each one. They are
        # not failures -- this suite opens and closes eight contexts -- and a
        # screenful of them buries the results they are printed above.
        try:
            super().handle_one_request()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            self.close_connection = True


socketserver.TCPServer.allow_reuse_address = True
httpd = socketserver.TCPServer(("127.0.0.1", PORT), Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
URL = "http://127.0.0.1:%d/" % PORT

SCHEME = "() => document.documentElement.dataset.theme"
SAVED = "() => localStorage.getItem('smartfan.theme')"
BAR = "() => document.querySelector('meta[name=theme-color]').content"
BG = "() => getComputedStyle(document.body).backgroundColor"

# The two palettes' page colors, read off tokens.css rather than retyped, so
# a change to either surface fails here as a mismatch rather than passing
# against a stale literal.
tokens = io.open(os.path.join(ROOT, "design-tokens", "tokens.css"), encoding="utf-8").read()


def token(block_selector, name):
    at = tokens.index(block_selector + " {")
    body = tokens[at:tokens.index("\n}", at)]
    return re.search(r"^\s*%s:\s*(#[0-9a-fA-F]{6})\s*;" % name, body, re.M).group(1)


DARK_BG = token(":root", "--bg")
LIGHT_BG = token(":root[data-theme='light']", "--bg")
LIGHT_BAR = token(":root[data-theme='light']", "--bar")


def rgb(hex_):
    return "rgb(%d, %d, %d)" % tuple(int(hex_[i : i + 2], 16) for i in (1, 3, 5))


print("theme regression\n")
print("  dark --bg %s   light --bg %s\n" % (DARK_BG, LIGHT_BG))

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=not HEADED)

    # --- an explicit choice, and its persistence -----------------------
    for choice, want_bg in (("light", LIGHT_BG), ("dark", DARK_BG)):
        ctx = browser.new_context(color_scheme="dark")
        page = ctx.new_page()
        page.add_init_script("localStorage.setItem('smartfan.theme', %r)" % choice)
        page.goto(URL, wait_until="load")
        page.wait_for_timeout(500)
        check("saved '%s' is applied" % choice, page.evaluate(SCHEME) == choice)
        check(
            "saved '%s' paints its own --bg" % choice,
            page.evaluate(BG) == rgb(want_bg),
            page.evaluate(BG),
        )
        # The OS says dark in both of these; an explicit light must win.
        if choice == "light":
            check("an explicit choice overrides the OS", page.evaluate(SCHEME) == "light")
        ctx.close()

    # --- no flash of the wrong scheme ---------------------------------
    # The attribute must be right in the very first frame. Reading it after
    # load proves only that theme.js eventually ran; this asks the document
    # while it is still parsing, which is the window the inline script in
    # app.html exists to cover.
    ctx = browser.new_context(color_scheme="dark")
    page = ctx.new_page()
    page.add_init_script("localStorage.setItem('smartfan.theme', 'light')")
    early = []
    page.expose_function("_early", lambda v: early.append(v))
    page.add_init_script(
        "document.addEventListener('readystatechange', () => {"
        "  if (document.readyState === 'interactive')"
        "    window._early(document.documentElement.dataset.theme || '(unset)');"
        "});"
    )
    page.goto(URL, wait_until="load")
    page.wait_for_timeout(400)
    check(
        "the scheme is set before the document finishes parsing",
        early and early[0] == "light",
        "saw %r" % (early[0] if early else None),
    )
    ctx.close()

    # --- system follows the OS ----------------------------------------
    for os_scheme, want in (("light", "light"), ("dark", "dark")):
        ctx = browser.new_context(color_scheme=os_scheme)
        page = ctx.new_page()
        page.add_init_script("localStorage.setItem('smartfan.theme', 'system')")
        page.goto(URL, wait_until="load")
        page.wait_for_timeout(500)
        check("system on a %s OS resolves to %s" % (os_scheme, want),
              page.evaluate(SCHEME) == want, page.evaluate(SCHEME))
        ctx.close()

    # system must keep following, not just read once at boot
    ctx = browser.new_context(color_scheme="dark")
    page = ctx.new_page()
    page.add_init_script("localStorage.setItem('smartfan.theme', 'system')")
    page.goto(URL, wait_until="load")
    page.wait_for_timeout(500)
    before = page.evaluate(SCHEME)
    page.emulate_media(color_scheme="light")
    page.wait_for_timeout(400)
    check("system follows the OS changing while the app is open",
          before == "dark" and page.evaluate(SCHEME) == "light",
          "%s -> %s" % (before, page.evaluate(SCHEME)))

    ctx.close()

    # ...and an explicit choice must NOT follow it. Its own context: an
    # add_init_script runs again on every navigation, so reloading the page
    # above would have quietly rewritten the choice back to 'system' and this
    # would have been a failure in the test rather than in the app.
    ctx = browser.new_context(color_scheme="dark")
    page = ctx.new_page()
    page.add_init_script("localStorage.setItem('smartfan.theme', 'dark')")
    page.goto(URL, wait_until="load")
    page.wait_for_timeout(500)
    page.emulate_media(color_scheme="light")
    page.wait_for_timeout(400)
    check("an explicit 'dark' ignores the OS turning light",
          page.evaluate(SCHEME) == "dark", page.evaluate(SCHEME))
    ctx.close()

    # --- the control in Settings --------------------------------------
    ctx = browser.new_context(color_scheme="dark")
    page = ctx.new_page()
    page.goto(URL, wait_until="load")
    page.wait_for_timeout(900)
    # Unconfigured, so the sheet opens itself; that is the path a new user
    # takes to this control and therefore the one worth driving.
    sheet = page.locator(".modes.three")
    check("the appearance control is in the settings sheet", sheet.count() == 1)

    page.locator(".modes.three button", has_text="Light").click()
    page.wait_for_timeout(400)
    check("choosing Light applies immediately", page.evaluate(SCHEME) == "light")
    check("choosing Light persists immediately", page.evaluate(SAVED) == "light")
    check("the status bar follows the scheme",
          page.evaluate(BAR) == LIGHT_BAR, page.evaluate(BAR))
    check("the chosen option is marked for a screen reader",
          page.locator(".modes.three button[aria-checked=true]").inner_text().strip() == "Light")

    page.screenshot(path=os.path.join(SHOTS, "theme-light.png"))

    # Cancel must not revert it: the theme is deliberately not part of the
    # connection draft, and a scheme that snaps back on Cancel is the bug
    # that arrangement exists to prevent.
    page.locator(".sheet-actions button", has_text="Cancel").click()
    page.wait_for_timeout(300)
    check("Cancel does not revert the scheme", page.evaluate(SCHEME) == "light")

    page.reload(wait_until="load")
    page.wait_for_timeout(600)
    check("and it survives a reload", page.evaluate(SCHEME) == "light")
    ctx.close()

    # --- a hostile localStorage ---------------------------------------
    ctx = browser.new_context(color_scheme="light")
    page = ctx.new_page()
    page.add_init_script("localStorage.setItem('smartfan.theme', 'chartreuse')")
    page.goto(URL, wait_until="load")
    page.wait_for_timeout(500)
    check("a junk saved value falls back to system, not to junk",
          page.evaluate(SCHEME) == "light", page.evaluate(SCHEME))
    ctx.close()

    browser.close()

httpd.shutdown()

failed = [n for n, ok, _ in results if not ok]
print("\n%d/%d passed" % (len(results) - len(failed), len(results)))
sys.exit(1 if failed else 0)
