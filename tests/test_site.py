"""The public site at smartfan.phbeks.com, built or live.

    python tests/test_site.py              # the build in _site/, served locally
    python tests/test_site.py --live       # also https://smartfan.phbeks.com
    python tests/test_site.py --headed

Needs a full build first (`python tools/build_site.py`), plus playwright and
Pillow. No broker, no fan, no network unless --live.

What is worth asserting here
----------------------------
The site is the project's public face, and it is assembled from five
sources (site/, docs/, the app's demo build, two hardware viewers), so its
failures are mostly ones of drift:

  * **Leaks.** _site/ is built next to secrets.py, .env and ota.key, and the
    whole folder is uploaded. The build script scans for private addresses;
    this also scans for the actual secret values, without printing them.
  * **Broken links.** Every local href and src in every page has to resolve
    inside _site/, and every github.com/deokman420/smartfan/{tree,blob}/main
    link has to name a path that exists in the repo.
  * **Facts that rot.** The landing page states numbers that live elsewhere:
    a week of history (config.py), four days of demo data (the fixture), the
    model's download size, the 6.4 mm mains gap (generate.py). Each is read
    from its source here, so changing the source fails this until the page
    is updated too.
  * **Layout.** No sideways scroll at phone, tablet and desktop widths, every
    image loads, nothing logs an error, and the 3D model loads on request.

Screenshots land in tests/screenshots/site-*.png for eyeballing.
"""

import functools
import html.parser
import http.server
import json
import os
import re
import socketserver
import sys
import threading
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "_site"
SHOTS = ROOT / "tests" / "screenshots"
PORT = 8791
LIVE_URL = "https://smartfan.phbeks.com"
REPO = "deokman420/smartfan"
HEADED = "--headed" in sys.argv
LIVE = "--live" in sys.argv

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("  %-56s %s %s" % (name, "PASS" if ok else "FAIL", detail))
    return ok


def bail(msg):
    print("\n%s" % msg)
    sys.exit(2)


if not (SITE / "index.html").exists() or not (SITE / "kb").is_dir() or not (SITE / "demo").is_dir():
    bail("No full build at _site/. Run:  python tools/build_site.py")

try:
    from PIL import Image
    from playwright.sync_api import sync_playwright
except ImportError as e:
    bail("missing dependency: %s" % e)


# --- Parsing ----------------------------------------------------------------

class Page(html.parser.HTMLParser):
    """Collects the links, images and ids of one HTML file."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.refs = []      # (tag, attr, value)
        self.imgs = []      # attrs of each <img>
        self.ids = set()
        self.meta = {}
        self.text = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "id" in a:
            self.ids.add(a["id"])
        for attr in ("href", "src"):
            if a.get(attr):
                self.refs.append((tag, attr, a[attr]))
        if a.get("srcset"):
            for part in a["srcset"].split(","):
                self.refs.append((tag, "srcset", part.strip().split(" ")[0]))
        if tag == "img":
            self.imgs.append(a)
        if tag == "meta":
            key = a.get("property") or a.get("name")
            if key:
                self.meta[key] = a.get("content", "")
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.text.append(data)


def parse(path):
    p = Page()
    p.feed(path.read_text(encoding="utf-8", errors="replace"))
    p.plain = re.sub(r"\s+", " ", " ".join(p.text))
    return p


def resolve(page_path, ref):
    """The file a local reference points at, or None if it is not local."""
    if re.match(r"^[a-z][a-z0-9+.-]*:", ref, re.I) or ref.startswith("//"):
        return None
    ref = ref.split("#")[0].split("?")[0]
    if not ref:
        return page_path
    target = (SITE / ref.lstrip("/")) if ref.startswith("/") else (page_path.parent / ref)
    target = Path(os.path.normpath(target))
    if target.is_dir() or ref.endswith("/"):
        target = target / "index.html"
    return target


PAGES = sorted(SITE.rglob("*.html"))
PAGES = [p for p in PAGES if ".vercel" not in p.parts]
LANDING = SITE / "index.html"
parsed = {p: parse(p) for p in PAGES}
landing = parsed[LANDING]


# --- 1. Nothing private ships ------------------------------------------------

print("\nnothing private ships")

BANNED_NAMES = {"secrets.py", "ota.key", "settings.json", "identity.py", ".env"}
banned = [str(f.relative_to(SITE)) for f in SITE.rglob("*")
          if f.is_file() and ".vercel" not in f.parts
          and (f.name in BANNED_NAMES or f.name.startswith(".env") or f.suffix in (".key", ".pem"))]
check("no secret files in _site/", not banned, ", ".join(banned))


def secret_values():
    """Quoted strings from secrets.py and values from .env, 8 chars or longer."""
    vals = {}
    sp = ROOT / "secrets.py"
    if sp.exists():
        for m in re.finditer(r"""^\s*(\w+)\s*=\s*['"]([^'"]{8,})['"]""", sp.read_text(errors="replace"), re.M):
            vals["secrets.py " + m.group(1)] = m.group(2)
    env = ROOT / ".env"
    if env.exists():
        for m in re.finditer(r"""^\s*(\w+)\s*=\s*['"]?([^'"\s]{8,})""", env.read_text(errors="replace"), re.M):
            if not m.group(2).startswith("http"):
                vals[".env " + m.group(1)] = m.group(2)
    # A value that is also in secrets.example.py is a published default
    # (MQTT_CLIENT_ID, say), not a secret.
    ex = ROOT / "secrets.example.py"
    public = ex.read_text(errors="replace") if ex.exists() else ""
    vals = {k: v for k, v in vals.items() if v not in public}
    key = ROOT / "ota.key"
    if key.exists():
        raw = key.read_bytes().strip()
        if len(raw) >= 16:
            vals["ota.key"] = raw
    return vals


secrets = secret_values()
hits = set()
for f in SITE.rglob("*"):
    if not f.is_file() or ".vercel" in f.parts:
        continue
    data = f.read_bytes()
    for label, v in secrets.items():
        if (v if isinstance(v, bytes) else v.encode()) in data:
            hits.add("%s in %s" % (label, f.relative_to(SITE)))
# Names only, never the values.
check("no secret value appears in any file (%d checked)" % len(secrets), not hits, "; ".join(sorted(hits)))

private = re.compile(rb"192\.168\.\d|\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b|\b172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b")
ip_hits = [str(f.relative_to(SITE)) for f in SITE.rglob("*")
           if f.is_file() and f.suffix in {".html", ".js", ".json", ".css", ".csv", ".xml", ".svg"}
           and private.search(f.read_bytes())]
check("no private LAN addresses", not ip_hits, ", ".join(ip_hits[:5]))


# --- 2. Links resolve ---------------------------------------------------------

print("\nlinks resolve")

broken = []
for p, pg in parsed.items():
    for tag, attr, ref in pg.refs:
        target = resolve(p, ref)
        if target is not None and not target.exists():
            broken.append("%s -> %s" % (p.relative_to(SITE), ref))
check("every local href/src in %d pages exists" % len(parsed), not broken,
      "%d broken, e.g. %s" % (len(broken), broken[:3]) if broken else "")

missing_ids = [ref for tag, attr, ref in landing.refs
               if ref.startswith("#") and len(ref) > 1 and ref[1:] not in landing.ids]
check("landing #anchors have targets", not missing_ids, ", ".join(missing_ids))

gh = re.compile(r"^https://github\.com/%s/(?:tree|blob)/main/(.+?)/?$" % re.escape(REPO))
gh_bad = []
gh_n = 0
for p, pg in parsed.items():
    for tag, attr, ref in pg.refs:
        m = gh.match(ref.split("#")[0])
        if m:
            gh_n += 1
            if not (ROOT / m.group(1)).exists():
                gh_bad.append("%s -> %s" % (p.relative_to(SITE), m.group(1)))
check("GitHub tree/blob links name real paths (%d)" % gh_n, not gh_bad, ", ".join(sorted(set(gh_bad))[:5]))

other_repos = sorted({ref for pg in parsed.values() for _, _, ref in pg.refs
                      if re.match(r"https://github\.com/deokman420/", ref)
                      and not ref.startswith("https://github.com/%s" % REPO)})
check("no links to other or old repos", not other_repos, ", ".join(other_repos[:3]))

hacs = [ref for _, _, ref in landing.refs if "hacs_repository" in ref]
check("HACS buttons name this repo", hacs and all(
    "owner=deokman420" in h and "repository=smartfan" in h and "category=integration" in h for h in hacs),
    "%d buttons" % len(hacs))


# --- 3. Landing images ---------------------------------------------------------

print("\nlanding images")

no_alt = [i.get("src") for i in landing.imgs if "alt" not in i]
check("every <img> has alt", not no_alt, ", ".join(no_alt))

# Declared width/height reserve the space before the image arrives. A stale
# pair after swapping a picture squashes it or makes the page jump.
ratio_bad = []
heavy = []
for i in landing.imgs:
    src = i.get("src", "")
    f = resolve(LANDING, src)
    if f is None or not f.exists() or f.suffix == ".svg":
        continue
    if f.stat().st_size > 400_000:
        heavy.append("%s %d kB" % (src, f.stat().st_size // 1000))
    if "width" not in i or "height" not in i:
        if src != "img/model.png":   # covered by .stage, which sets the box
            ratio_bad.append(src + " has no width/height")
        continue
    with Image.open(f) as im:
        real = im.width / im.height
    said = int(i["width"]) / int(i["height"])
    if abs(real - said) / real > 0.02:
        ratio_bad.append("%s says %s:%s, is %dx%d" % (src, i["width"], i["height"], im.width, im.height))
check("width/height match each image's shape", not ratio_bad, "; ".join(ratio_bad))
check("no landing image over 400 kB", not heavy, ", ".join(heavy))

og = landing.meta.get("og:image", "")
check("og:image is absolute and exists", og.startswith(LIVE_URL + "/")
      and (SITE / og[len(LIVE_URL) + 1:]).exists(), og)
check("title and description set", landing.meta.get("description") and "<title>Smartfan</title>"
      in LANDING.read_text(encoding="utf-8"))


# --- 4. Facts match their sources ---------------------------------------------

print("\nfacts match their sources")
text = landing.plain
cfg = (ROOT / "config.py").read_text(encoding="utf-8")
days = int(re.search(r"^HISTORY_DAYS\s*=\s*(\d+)", cfg, re.M).group(1))
check("'a week of history' matches HISTORY_DAYS",
      ("a week of history" in text) == (days == 7), "HISTORY_DAYS=%d" % days)

csv = (ROOT / "app/src/lib/demo/history.csv").read_text().splitlines()
rows = [l for l in csv if l and not l.startswith("#")]
span_days = (int(rows[-1].split(",")[0]) - int(rows[0].split(",")[0])) / 86400
words = {3: "three", 4: "four", 5: "five", 6: "six", 7: "seven"}
check("demo 'N days of real data' matches the fixture",
      "%s days of real data" % words.get(int(span_days), "?") in text, "fixture spans %.1f days" % span_days)

kb = re.search(r"About (\d+) kB", text)
model_kb = (SITE / "model/index.html").stat().st_size / 1000
check("3D model size note within 10 %", kb and abs(int(kb.group(1)) - model_kb) / model_kb < 0.10,
      "page says %s kB, file is %.0f kB" % (kb and kb.group(1), model_kb))

gen = (ROOT / "hardware/v2/generate.py").read_text(encoding="utf-8")
gap = re.search(r"^MAINS_GAP\s*=\s*([\d.]+)", gen, re.M).group(1)
check("mains gap matches generate.py", ("%s mm" % gap) in text, "MAINS_GAP=%s" % gap)

check("V2 is said to be unbuilt", "not built yet" in text)
check("the V2 board is labeled as a render", re.search(r"render(ed)? from the KiCad files", text)
      and "hasn't been made" in text)

# American English, the project rule. The frozen HA entity ids odour_* are
# the one allowed exception, so ids with an underscore are skipped.
brit = re.compile(r"\b(colour|odour|behaviour|centre|centreline|favourite|analyse|organise|licence|neighbour)s?\b(?!_)", re.I)
brit_hits = sorted({"%s: %s" % (p.relative_to(SITE), m.group(0))
                    for p, pg in parsed.items() for m in brit.finditer(pg.plain)})
check("American spelling throughout", not brit_hits, ", ".join(brit_hits[:5]))


# --- 5. In a browser -----------------------------------------------------------

class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionError, OSError):
            pass


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


httpd = Server(("127.0.0.1", PORT), functools.partial(Handler, directory=str(SITE)))
threading.Thread(target=httpd.serve_forever, daemon=True).start()
SHOTS.mkdir(parents=True, exist_ok=True)

WIDTHS = {"phone": (390, 844), "tablet": (820, 1180), "desktop": (1280, 900)}


def watch(page):
    errs = []
    page.on("console", lambda m: m.type == "error" and errs.append(m.text[:120]))
    page.on("pageerror", lambda e: errs.append(str(e)[:120]))
    page.on("requestfailed", lambda r: errs.append("failed " + r.url[-80:]))
    page.on("response", lambda r: r.status >= 400 and errs.append("%d %s" % (r.status, r.url[-80:])))
    return errs


def browse(base, tag):
    print("\nin a browser: %s" % base)
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=not HEADED)
        for name, (w, h) in WIDTHS.items():
            ctx = b.new_context(viewport={"width": w, "height": h})
            page = ctx.new_page()
            errs = watch(page)
            page.goto(base + "/", wait_until="networkidle")
            # Lazy images load as they scroll into view.
            page.evaluate("""async () => { for (let y = 0; y < document.body.scrollHeight; y += 600) {
                window.scrollTo(0, y); await new Promise(r => setTimeout(r, 60)); } window.scrollTo(0, 0); }""")
            page.wait_for_load_state("networkidle")
            over = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
            check("%s %s: no sideways scroll" % (tag, name), over <= 0, "%d px over" % over if over > 0 else "")
            broken_imgs = page.evaluate("""[...document.images].filter(i => {
                const s = getComputedStyle(i); return s.display !== 'none' && (!i.complete || !i.naturalWidth);
              }).map(i => i.currentSrc || i.src)""")
            check("%s %s: every image loaded" % (tag, name), not broken_imgs, ", ".join(broken_imgs))
            check("%s %s: no errors" % (tag, name), not errs, "; ".join(errs[:3]))
            page.screenshot(path=str(SHOTS / ("site-%s-%s.png" % (tag, name))), full_page=True)
            ctx.close()

        # The 3D model loads only on request, into an iframe.
        ctx = b.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        errs = watch(page)
        page.goto(base + "/#model", wait_until="networkidle")
        page.click("#load3d")
        frame_el = page.wait_for_selector("#stage iframe", timeout=5000)
        frame = frame_el.content_frame()
        frame.wait_for_load_state("load")
        canvas = frame.wait_for_selector("canvas", timeout=15000)
        check("%s: Load 3D model shows a canvas" % tag, canvas is not None)
        # Headless Chromium may have no GPU; WebGL warnings are not the site's fault.
        real = [e for e in errs if "WebGL" not in e and "GPU" not in e]
        check("%s: model loads without errors" % tag, not real, "; ".join(real[:3]))
        ctx.close()

        # The other sections of the site at least come up clean.
        for path, expect in (("/demo/", "Humidity"), ("/kb/", "Smartfan"), ("/parts/", None), ("/model/", None)):
            ctx = b.new_context(viewport={"width": 1280, "height": 900})
            page = ctx.new_page()
            errs = watch(page)
            resp = page.goto(base + path, wait_until="networkidle")
            page.wait_for_timeout(800)
            body = page.inner_text("body")
            ok = resp and resp.ok and (expect is None or expect.lower() in body.lower())
            check("%s %s: loads" % (tag, path), ok, "status %s" % (resp and resp.status))
            real = [e for e in errs if "WebGL" not in e and "GPU" not in e]
            check("%s %s: no errors" % (tag, path), not real, "; ".join(real[:3]))
            ctx.close()
        b.close()


browse("http://127.0.0.1:%d" % PORT, "local")
httpd.shutdown()


# --- 6. Live, on request --------------------------------------------------------

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "smartfan-site-test"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status, r.read()


if LIVE:
    print("\nlive: %s" % LIVE_URL)
    try:
        status, body = fetch(LIVE_URL + "/")
        check("live landing answers 200", status == 200)
        check("live landing matches the build", body == LANDING.read_bytes(),
              "deploy is behind or ahead of _site/" if body != LANDING.read_bytes() else "")
    except Exception as e:
        check("live landing answers 200", False, str(e)[:80])

    # HACS installs only tagged releases (hide_default_branch), so a release
    # must exist and match the integration's version.
    version = json.loads((ROOT / "custom_components/smartfan/manifest.json").read_text())["version"]
    try:
        _, rel = fetch("https://api.github.com/repos/%s/releases/latest" % REPO)
        tag = json.loads(rel)["tag_name"]
        check("latest GitHub release matches manifest.json", tag.lstrip("v") == version,
              "release %s, manifest %s" % (tag, version))
    except Exception as e:
        check("latest GitHub release matches manifest.json", False, str(e)[:80])

    browse(LIVE_URL, "live")


failed = [r for r in results if not r[1]]
print("\n%d checks, %d failed" % (len(results), len(failed)))
for name, _, detail in failed:
    print("  FAIL %s %s" % (name, detail))
sys.exit(1 if failed else 0)
