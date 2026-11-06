"""An empty app/build must not become a bundle, and must not be pushed.

    python tests/test_build_www.py

Host only. No board, no npm. The real app/build and www/ are never touched;
SRC and OUT are pointed at a temp directory for the duration.

A failed `npm run build` can leave app/build present and empty. Packing
that used to succeed, and --clean then deleted the device's www/ before
the copy discovered it had nothing to send.
"""

import importlib.util
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("  %-46s %s %s" % (name, "PASS" if ok else "FAIL", detail))
    return ok


def load():
    path = os.path.join(ROOT, "tools", "build_www.py")
    spec = importlib.util.spec_from_file_location("build_www_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def caught(fn):
    try:
        fn()
    except SystemExit as e:
        return e
    return None


print("empty app bundle is refused (host, no board)\n")

bw = load()
tmp = tempfile.mkdtemp(prefix="fan-www-")
src = os.path.join(tmp, "build")
out = os.path.join(tmp, "www")
try:
    os.makedirs(src)
    os.makedirs(out)
    sentinel = os.path.join(out, "keep.txt")
    with open(sentinel, "w", encoding="utf-8") as f:
        f.write("staged from the last good build\n")
    bw.SRC = src
    bw.OUT = out

    missing = os.path.join(tmp, "absent")
    bw.SRC = missing
    err = caught(bw.build_tree)
    check("missing app/build is refused",
          err is not None and "run the build first" in str(err.code),
          str(getattr(err, "code", err)))
    check("a refusal leaves the staged www/ in place",
          os.path.isfile(sentinel))

    bw.SRC = src
    os.makedirs(os.path.join(src, "assets"))  # a directory, still no files
    err = caught(bw.build_tree)
    check("empty app/build is refused",
          err is not None and "no files in" in str(err.code)
          and "run the build first" in str(err.code),
          str(getattr(err, "code", err)))
    check("an empty app/build does not wipe www/",
          os.path.isfile(sentinel))

    sample = os.path.join(src, "index.html")
    with open(sample, "w", encoding="utf-8") as f:
        f.write("<p>hi</p>\n")
    entries, raw_total, out_total = bw.build_tree()
    check("a real file is packed",
          len(entries) == 1 and entries[0]["path"] == "index.html"
          and raw_total > 0 and out_total > 0,
          "%d files, %d raw" % (len(entries), raw_total))
    check("packing still replaces a stale www/",
          not os.path.isfile(sentinel)
          and os.path.isfile(os.path.join(out, "index.html")))

    called = []

    def mp(*args, **kwargs):
        called.append(args)
        raise AssertionError("mpremote should not run")

    bw.mp = mp
    err = caught(lambda: bw.push("COM5", [], True))
    check("an empty bundle is not pushed",
          err is not None and str(err.code) == "refusing to push an empty bundle"
          and called == [],
          str(getattr(err, "code", err)))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

passed = sum(1 for _, ok, _ in results if ok)
print("\n%d/%d passed" % (passed, len(results)))
fails = [n for n, ok, _ in results if not ok]
if fails:
    print("failed: " + ", ".join(fails))
sys.exit(0 if not fails else 1)
