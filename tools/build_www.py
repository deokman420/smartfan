"""Put the built app on the device's flash.

    python tools/build_www.py                  # build, gzip, report sizes
    python tools/build_www.py --push COM5      # ...and copy it to the device
    python tools/build_www.py --push COM5 --clean   # remove stale files first

What it does, in order: runs `npm run build` in app/ unless told not to,
gzips every file into www/ beside the repo, writes www/build.json, and
optionally copies the tree to the board over USB.

Only the compressed copy is shipped. The bundle is 490 kB raw and 164 kB
gzipped, flash is the scarce resource on a 4 MB board, and httpd.py serves
`X.gz` with Content-Encoding for any request for `X`. The cost is that curl
needs --compressed; every browser since the nineties is fine.

Why this is not an OTA. `ota_publish.py` ships Python modules, verifies each
against a manifest, and can roll the whole set back at boot -- machinery that
exists because bad firmware means a ladder. The app cannot brick anything: a
broken bundle is a blank page on a device whose fan is still running on its
own logic, and the fix is to push again. Keeping them separate also keeps the
firmware fingerprint meaningful, instead of changing it every time a label
moves two pixels.
"""

import argparse
import gzip
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = os.path.join(ROOT, "app")
SRC = os.path.join(APP, "build")
OUT = os.path.join(ROOT, "www")

# Compressing an already-compressed file makes it bigger, and gzipping tiny
# files costs more in headers than it saves.
SKIP_GZIP = (".png", ".jpg", ".jpeg", ".webp", ".gz", ".woff2", ".ico")
MIN_GZIP = 256

# Free filesystem space, measured on a real RPI_PICO2_W running MicroPython
# 1.29.0 with the firmware on it: os.statvfs('/') reported 640 blocks of
# 4096 total, 612 of them free. This used to be a guess of 1.4 MB, which was
# both wrong and the kind of wrong that is invisible -- a percentage against
# an invented denominator. Pass --port to measure the board in front of you
# instead of trusting this.
FLASH_BUDGET = 2_506_752


def run_build():
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        sys.exit("npm not found; build app/ yourself or pass --no-build")
    print("building app/ ...")
    p = subprocess.run([npm, "run", "build"], cwd=APP)
    if p.returncode != 0:
        sys.exit("npm run build failed")


def walk(base):
    for dirpath, _dirs, files in os.walk(base):
        for name in files:
            full = os.path.join(dirpath, name)
            yield full, os.path.relpath(full, base).replace(os.sep, "/")


def build_tree(clean_out=True):
    """Gzip app/build into www/. Returns (entries, raw_total, out_total)."""
    if not os.path.isdir(SRC):
        sys.exit("no %s -- run the build first" % SRC)
    if clean_out and os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT, exist_ok=True)

    entries = []
    raw_total = out_total = 0
    for full, rel in sorted(walk(SRC), key=lambda t: t[1]):
        data = io.open(full, "rb").read()
        raw_total += len(data)
        ext = os.path.splitext(rel)[1].lower()
        gz = len(data) >= MIN_GZIP and ext not in SKIP_GZIP
        if gz:
            # mtime=0: otherwise every build produces different bytes for an
            # unchanged file, and the manifest hash stops meaning anything.
            body = gzip.compress(data, 9, mtime=0)
            if len(body) >= len(data):
                gz, body = False, data
        else:
            body = data
        dest = os.path.join(OUT, rel.replace("/", os.sep) + (".gz" if gz else ""))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        io.open(dest, "wb").write(body)
        out_total += len(body)
        entries.append({
            "path": rel,
            "raw": len(data),
            "size": len(body),
            "gz": gz,
            "sha256": hashlib.sha256(data).hexdigest()[:16],
        })
    return entries, raw_total, out_total


def write_manifest(entries):
    """www/build.json -- what telemetry.app_build() reports as the app version.

    The hash is over the file list and their content hashes, so it changes
    when and only when the served bytes change. The device reports it beside
    the firmware fingerprint, which is how a stale app on current firmware
    becomes visible from the app itself rather than being guessed at.
    """
    h = hashlib.sha256()
    for e in entries:
        h.update((e["path"] + e["sha256"]).encode())
    build = h.hexdigest()[:8]
    manifest = {
        "build": build,
        "files": len(entries),
        "bytes": sum(e["size"] for e in entries),
        "built": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    io.open(os.path.join(OUT, "build.json"), "w", newline="\n").write(
        json.dumps(manifest, indent=2) + "\n")
    return build


def device_free(port):
    """Free bytes on the board's filesystem, or None if it cannot be asked."""
    code = "import os; s = os.statvfs('/'); print('FREE', s[0] * s[3])"
    p = mp(port, "exec", code, check=False)
    for line in (p.stdout or "").splitlines():
        if line.startswith("FREE"):
            try:
                return int(line.split()[1])
            except (IndexError, ValueError):
                return None
    return None


def report(entries, raw_total, out_total, build, free=None):
    print()
    print("  %-46s %8s %8s" % ("file", "raw", "on flash"))
    for e in entries:
        print("  %-46s %8d %8d%s" % (e["path"][:46], e["raw"], e["size"],
                                     "  gz" if e["gz"] else ""))
    print("  %-46s %8d %8d" % ("TOTAL", raw_total, out_total))
    print()
    budget = free or FLASH_BUDGET
    pct = 100.0 * out_total / budget
    print("build %s: %d files, %.1f kB on flash (%.0f%% of the %.1f MB free%s)"
          % (build, len(entries), out_total / 1024.0, pct, budget / 1e6,
             "" if free else ", estimated"))
    # What else wants that space: a week of history is about 100 kB, and an
    # OTA stages a .new and keeps a .bak for every file it replaces.
    print("  also needs room for: ~100 kB of history, and an OTA's staged "
          "and backup copies")
    if pct > 60:
        print("WARNING: that leaves little room for history and staged OTA files.")


# --- pushing ----------------------------------------------------------------

def mp(port, *args, check=True):
    cmd = [sys.executable, "-m", "mpremote", "connect", port] + list(args)
    p = subprocess.run(cmd, capture_output=True, text=True)
    if check and p.returncode != 0:
        print("  ! %s" % " ".join(args))
        print("    %s" % (p.stderr or p.stdout).strip()[:300])
    return p


def push(port, entries, clean):
    """Copy www/ to the board.

    Files are copied one at a time with the directories created first.
    `fs cp -r` exists, but it is inconsistent about whether the destination
    directory has to be there already, and a half-copied bundle is a blank
    page that looks like a firmware fault.
    """
    print("\npushing to %s ..." % port)

    # The board must not be running main.py: the watchdog resets it out from
    # under a long copy, and a reset mid-file leaves a truncated asset.
    print("  entering safe mode (main.py would reset us mid-copy)")
    mp(port, "exec", "open('safemode','w').close()")
    mp(port, "exec", "import machine; machine.reset()", check=False)
    time.sleep(4)

    if clean:
        print("  removing the old www/")
        mp(port, "exec", _RMTREE, check=False)

    dirs = set()
    for e in entries:
        rel = e["path"] + (".gz" if e["gz"] else "")
        parts = ("www/" + rel).split("/")[:-1]
        for i in range(1, len(parts) + 1):
            dirs.add("/".join(parts[:i]))
    for d in sorted(dirs):
        mp(port, "fs", "mkdir", ":" + d, check=False)   # already-exists is fine

    ok = 0
    for e in entries:
        rel = e["path"] + (".gz" if e["gz"] else "")
        local = os.path.join(OUT, rel.replace("/", os.sep))
        p = mp(port, "fs", "cp", local, ":www/" + rel)
        if p.returncode == 0:
            ok += 1
            print("  %s" % rel)
    mp(port, "fs", "cp", os.path.join(OUT, "build.json"), ":www/build.json")

    print("  leaving safe mode")
    mp(port, "exec",
       "import os\ntry:\n os.remove('safemode')\nexcept OSError:\n pass")
    mp(port, "exec", "import machine; machine.reset()", check=False)
    print("pushed %d/%d files; the board is restarting" % (ok, len(entries)))
    return ok == len(entries)


_RMTREE = """
import os
def rm(p):
    try:
        for e in os.listdir(p):
            f = p + '/' + e
            if os.stat(f)[0] & 0x4000:
                rm(f)
            else:
                os.remove(f)
        os.rmdir(p)
    except OSError:
        pass
rm('www')
print('www removed')
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-build", action="store_true",
                    help="use app/build as it stands")
    ap.add_argument("--push", metavar="PORT",
                    help="copy the tree to this serial port, e.g. COM5")
    ap.add_argument("--clean", action="store_true",
                    help="delete www/ on the device before copying")
    ap.add_argument("--port", metavar="PORT",
                    help="ask this board how much flash is free, without pushing")
    args = ap.parse_args()

    if not args.no_build:
        run_build()
    entries, raw_total, out_total = build_tree()
    build = write_manifest(entries)
    free = device_free(args.push or args.port) if (args.push or args.port) else None
    report(entries, raw_total, out_total, build, free)

    if args.push:
        if not push(args.push, entries, args.clean):
            sys.exit(1)
    else:
        print("\nwww/ is ready. Push it with:  "
              "python tools/build_www.py --no-build --push COM5")


if __name__ == "__main__":
    main()
