"""Assemble the public site at smartfan.phbeks.com into _site/.

    _site/           landing page      site/
    _site/kb/        knowledge base    mkdocs build (docs/, mkdocs.yml)
    _site/demo/      the app, simulated  app: npm run build:demo
    _site/model/     V2 assembly       hardware/v2/model-v2.html
    _site/parts/     parts viewer      hardware/v2/cad/viewer.html, vendor/, *.stl

Usage:
    python tools/build_site.py            # build everything
    python tools/build_site.py --serve    # then serve _site/ on :8790
    vercel deploy --prod --cwd _site      # publish (project: smartfan)

Needs mkdocs-material (requirements-docs.txt) and the app's node_modules.
The site is hosted on Vercel (project smartfan, smartfan.phbeks.com) and
deployed from _site/ only -- never from the repo root, which holds secrets.
"""
import argparse
import http.server
import functools
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "_site"

# Strings that must never reach the public site.
PRIVATE = [
    re.compile(rb"192\.168\.\d"),
    re.compile(rb"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    re.compile(rb"\b172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b"),
]
SCAN = {".html", ".js", ".json", ".css", ".txt", ".xml", ".svg", ".webmanifest", ".csv"}


def run(cmd, cwd):
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True, shell=sys.platform == "win32")


def build():
    # _site/.vercel is the Vercel project link (from `vercel link`), kept
    # across rebuilds so `vercel deploy --prod --cwd _site` just works. The
    # CLI never uploads .vercel/ or .env* files.
    link = ROOT / ".vercel-link.tmp"
    if (OUT / ".vercel").exists():
        if link.exists():
            shutil.rmtree(link)
        shutil.move(str(OUT / ".vercel"), str(link))
    # A link left in the temp spot by a run that failed part-way is kept too.
    if not link.exists():
        link = None
    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(ROOT / "site", OUT)
    if link:
        shutil.move(str(link), str(OUT / ".vercel"))

    # mkdocs.yml writes straight to _site/kb.
    run([sys.executable, "-m", "mkdocs", "build", "--strict"], ROOT)

    run(["npm", "run", "build:demo"], ROOT / "app")
    shutil.copytree(ROOT / "app" / "build-demo", OUT / "demo")

    (OUT / "model").mkdir()
    shutil.copy(ROOT / "hardware/v2/model-v2.html", OUT / "model/index.html")

    cad = ROOT / "hardware/v2/cad"
    parts = OUT / "parts"
    parts.mkdir()
    shutil.copy(cad / "viewer.html", parts / "index.html")
    shutil.copytree(cad / "vendor", parts / "vendor")
    for stl in cad.glob("*.stl"):
        shutil.copy(stl, parts / stl.name)

    # Pages would otherwise run the output through Jekyll and drop _app/.
    (OUT / ".nojekyll").touch()

    hits = []
    for f in OUT.rglob("*"):
        if f.is_file() and f.suffix in SCAN:
            data = f.read_bytes()
            for rx in PRIVATE:
                m = rx.search(data)
                if m:
                    hits.append(f"{f.relative_to(OUT)}: {m.group().decode(errors='replace')}")
    if hits:
        print("\nbuild_site: refusing, private-looking addresses in the output:")
        for h in hits:
            print("  " + h)
        sys.exit(1)
    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"\nbuild_site: _site/ is clean, {size / 1e6:.1f} MB")


def serve(port):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(OUT))
    print(f"serving _site/ on http://localhost:{port}/")
    http.server.ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--no-build", action="store_true")
    a = ap.parse_args()
    if not a.no_build:
        build()
    if a.serve:
        serve(a.port)
