"""Publish an OTA update: build a manifest, serve it, tell the device.

    python tools/ota_publish.py              # serve and trigger
    python tools/ota_publish.py --dry-run    # show what would change
    python tools/ota_publish.py --serve-only # serve, trigger it yourself

Builds manifest.json from the repo's Python files, serves the directory over
HTTP on this machine, and publishes an `update <url>` command to the
device's MQTT topic. The device pulls what it needs, verifies each file's
sha256, and installs only once every file has verified.

Nothing here can brick the device. If the new code fails to come up, boot.py
rolls it back on its own -- see ota.py for why that works even when main.py
cannot import.
"""

import argparse
import hashlib
import http.server
import io
import json
import os
import re
import socket
import socketserver
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Shipped to the device. boot.py and ota.py are excluded on purpose: they are
# the recovery path, and a recovery path you can break remotely is not one.
# Push those over USB when they genuinely change.
SHIP = ("main.py", "config.py", "settings.py", "telemetry.py", "light.py",
        "sgp41.py", "voc.py", "net.py", "mqtt.py", "ringbuf.py", "calibrate.py",
        "provision.py", "improv.py", "httpd.py", "webapi.py", "store.py",
        "wifi.py")

EXCLUDE_REASON = {
    "www/": "the app bundle -- pushed by tools/build_www.py, versioned apart",
    "boot.py": "recovery path -- flash over USB",
    "ota.py": "recovery path -- flash over USB",
    "secrets.py": "device-specific credentials",
    "settings.json": "device's own runtime settings",
    "identity.py": "the room this unit was named at setup",
}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def fingerprint():
    """The same calculation the device runs, so the two can be compared.

    TRACKED is read out of telemetry.py rather than duplicated here. Two
    copies of a list like this drift, and a drifted copy reports a mismatch
    that is not one — worse than having no check at all.
    """
    src = io.open(os.path.join(ROOT, "telemetry.py"), encoding="utf-8").read()
    m = re.search(r"TRACKED = [(]([^)]*)[)]", src, re.S)
    names = sorted(re.findall(r'"([^"]+)"', m.group(1))) if m else sorted(SHIP)
    outer = hashlib.sha256()
    for name in names:
        path = os.path.join(ROOT, name)
        digest = sha256(path) if os.path.exists(path) else "-"
        outer.update((name + digest).encode())
    return outer.hexdigest()[:8]


def device_build(host="fan.local"):
    """What build the device says it is running.

    The device's own /api/info first: it is on the same LAN the update is
    about to be served over, it needs no token, and it touches no USB.
    Home Assistant stays as the fallback for a board this machine cannot
    reach directly.
    """
    try:
        import urllib.request
        with urllib.request.urlopen("http://%s/api/info" % host, timeout=5) as f:
            fw = json.load(f).get("fw")
        if fw:
            return fw
    except Exception:
        pass
    try:
        import urllib.request
        env = io.open(os.path.join(ROOT, ".env"), encoding="utf-8").read()
        tok = re.search(r'HASS_LLAT\s*=\s*"?([^"\s]+)', env).group(1)
        req = urllib.request.Request(
            "https://ha.local.phbeks.com/api/states/"
            "sensor.bathroom_fan_controller_build",
            headers={"Authorization": "Bearer " + tok})
        with urllib.request.urlopen(req, timeout=8) as f:
            return json.load(f)["state"]
    except Exception:
        return None


def build_manifest():
    files = {}
    for name in SHIP:
        p = os.path.join(ROOT, name)
        if not os.path.exists(p):
            print("  WARNING %s is missing, skipping" % name)
            continue
        files[name] = {"sha": sha256(p), "size": os.path.getsize(p)}
    man = {"built": time.strftime("%Y-%m-%dT%H:%M:%S"), "files": files}
    with io.open(os.path.join(ROOT, "manifest.json"), "w",
                 encoding="utf-8", newline="\n") as f:
        json.dump(man, f, indent=1, sort_keys=True)
    return man


def local_ip():
    """The address this machine has on the device's network."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.0.5", 1))       # no traffic sent
        return s.getsockname()[0]
    finally:
        s.close()


def device_files():
    """sha256 of each shipped file as it exists on the device, over USB.

    OPT-IN, because it costs far more than it looks. `mpremote exec` takes
    the REPL, which means sending Ctrl-C and STOPPING main.py. So probing
    the device to print a prettier table also halts the controller, and the
    update published a second later goes to a board that is no longer
    running its loop and will never see the command. That is most of what
    "OTA cannot land while USB is attached" has always meant, and it cost
    an afternoon on 2026-11-03 before the cause was spotted.

    It is also the lesser source: /api/info carries `fw`, which answers "is
    the device current" over the network without disturbing anything. This
    exists for the per-file breakdown alone, behind --probe-usb.
    """
    import subprocess
    code = (
        "import hashlib, binascii, os\n"
        "for n in %r:\n"
        "    try:\n"
        "        h = hashlib.sha256()\n"
        "        f = open(n,'rb')\n"
        "        while True:\n"
        "            b = f.read(512)\n"
        "            if not b: break\n"
        "            h.update(b)\n"
        "        f.close()\n"
        "        print(n, binascii.hexlify(h.digest()).decode())\n"
        "    except OSError:\n"
        "        print(n, 'ABSENT')\n" % (list(SHIP),))
    try:
        p = subprocess.run([sys.executable, "-m", "mpremote", "connect",
                            "COM5", "exec", code],
                           capture_output=True, text=True, timeout=60)
        out = {}
        for line in p.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2:
                out[parts[0]] = parts[1]
        return out
    except Exception:
        return {}


def publish_command(url):
    """Tell the device to update, via MQTT."""
    sys.path.insert(0, ROOT)
    import secrets                                     # gitignored
    import paho.mqtt.publish as publish                # host-side only
    import config as cfg
    publish.single(
        "fan/%s/cmd" % cfg.ROOM, "update %s" % url,
        hostname=secrets.MQTT_HOST, port=getattr(secrets, "MQTT_PORT", 1883),
        auth={"username": secrets.MQTT_USER, "password": secrets.MQTT_PASS},
        client_id="ota-publisher")


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *a):
        sys.stdout.write("  served %s\n" % (a[0] if a else fmt))
        sys.stdout.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8123 + 1000)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--serve-only", action="store_true")
    ap.add_argument("--seconds", type=int, default=120)
    ap.add_argument("--probe-usb", action="store_true",
                    help="per-file diff over USB. HALTS THE CONTROLLER: "
                         "mpremote takes the REPL, so the update that "
                         "follows will not be seen. Detach first, or use "
                         "the build line, which comes over HTTP.")
    args = ap.parse_args()

    print("building manifest from %s\n" % ROOT)
    man = build_manifest()
    for name, reason in sorted(EXCLUDE_REASON.items()):
        print("  excluded  %-16s %s" % (name, reason))
    print()

    on_device = device_files() if args.probe_usb else {}
    changed = []
    for name, meta in sorted(man["files"].items()):
        cur = on_device.get(name)
        if cur is None:
            mark, note = "?", ("device not reachable over USB" if args.probe_usb
                               else "not probed (--probe-usb halts the board)")
        elif cur == "ABSENT":
            mark, note = "+", "new file"
        elif cur != meta["sha"]:
            mark, note = "*", "changed"
            changed.append(name)
        else:
            mark, note = " ", "same"
        print("  %s %-16s %6d B  %s" % (mark, name, meta["size"], note))

    want = fingerprint()
    have = device_build()
    print()
    if have is None:
        print("  build   local %s   device unreachable" % want)
    elif have == want:
        print("  build   local %s   device %s   MATCH" % (want, have))
    else:
        print("  build   local %s   device %s   DIFFERS" % (want, have))

    if on_device and not changed:
        print("\nnothing to update")
        if not args.serve_only:
            return 0

    if args.dry_run:
        print("\ndry run, nothing served")
        return 0

    ip = local_ip()
    url = "http://%s:%d" % (ip, args.port)
    os.chdir(ROOT)
    httpd = socketserver.TCPServer(("", args.port), Quiet)
    httpd.allow_reuse_address = True
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    print("\nserving %s on %s" % (ROOT, url))

    if args.serve_only:
        print("send this yourself:")
        print('  mosquitto_pub -t fan/bathroom/cmd -m "update %s"' % url)
    else:
        try:
            publish_command(url)
            print("published: update %s" % url)
        except ImportError as e:
            print("cannot publish (%s)" % e)
            print("send it yourself:")
            print('  mosquitto_pub -t fan/bathroom/cmd -m "update %s"' % url)
        except Exception as e:
            print("publish failed: %s" % e)

    print("\nserving for %ds -- watch the device log\n" % args.seconds)
    try:
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass
    httpd.shutdown()
    print("\nstopped serving")
    return 0


if __name__ == "__main__":
    sys.exit(main())
