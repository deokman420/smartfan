"""What tools/ota_publish.py hands out while it serves an update.

    python tests/test_ota_publish.py

Host only. It used to serve the whole project folder on every interface,
so while an update ran anyone on the LAN could fetch .env (the Home
Assistant token) and secrets.py. This runs the real server on 127.0.0.1
against a scratch project and asks for everything it should refuse.
"""

import http.client
import io
import json
import os
import shutil
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import ota_publish                                   # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %-52s %s %s" % (name, "PASS" if ok else "FAIL", detail))


print("\nota_publish serves the update and nothing else\n")

# A scratch project with the things that must never leave it.
proj = tempfile.mkdtemp(prefix="ota-proj-")
for name, body in (("main.py", "print('new')\n"), ("wifi.py", "x = 1\n"),
                   (".env", 'HASS_LLAT="fixture-token"\n'),
                   ("secrets.py", "MQTT_PASS = 'fixture-pass'\n"),
                   ("boot.py", "# recovery\n")):
    # newline="": on Windows text mode would write \r\n and the byte-exact
    # "a shipped file is served" check would fail on identical content.
    io.open(os.path.join(proj, name), "w", newline="").write(body)
man = {"built": "x", "files": {"main.py": {}, "wifi.py": {}}}
io.open(os.path.join(proj, "manifest.json"), "w").write(json.dumps(man))

folder = ota_publish.stage(man, root=proj)
check("the staged folder holds only the update",
      sorted(os.listdir(folder)) == ["main.py", "manifest.json", "wifi.py"],
      str(sorted(os.listdir(folder))))
check("...and it is not the project folder",
      os.path.realpath(folder) != os.path.realpath(proj))

srv = ota_publish.make_server("127.0.0.1", 0, folder,
                              ["manifest.json"] + list(man["files"]))
port = srv.server_address[1]
check("it binds to the address it was given, not 0.0.0.0",
      srv.server_address[0] == "127.0.0.1", str(srv.server_address))
threading.Thread(target=srv.serve_forever, daemon=True).start()


def get(path, method="GET"):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request(method, path)
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, body


st, body = get("/manifest.json")
check("the manifest is served", st == 200 and b"main.py" in body)
st, body = get("/main.py")
check("a shipped file is served", st == 200 and body == b"print('new')\n")
check("...with a query string too", get("/wifi.py?x=1")[0] == 200)
check("...and to HEAD", get("/main.py", "HEAD")[0] == 200)

leaks = []
for path in ("/.env", "/secrets.py", "/boot.py", "/", "/..", "/../.env",
             "/%2e%2e/.env", "/..%2fsecrets.py", "/main.py/", "//etc/passwd",
             "/" + os.path.basename(proj) + "/.env", "/%2Eenv"):
    st, body = get(path)
    if st != 404 or b"fixture" in body:
        leaks.append((path, st))
check("everything else is a 404", not leaks, str(leaks))

# The second lock: a file that is in the folder but not in the list.
io.open(os.path.join(folder, "stray.py"), "w").write("fixture\n")
check("a file in the folder but not in the list is a 404",
      get("/stray.py")[0] == 404)

srv.shutdown()
srv.server_close()
shutil.rmtree(folder, ignore_errors=True)
shutil.rmtree(proj, ignore_errors=True)

# -- signing (audit NV-2 / NV-3) -------------------------------------------
# What the publisher signs must be exactly what the device verifies, and the
# signature must reach the device: staged, listed and served.

print("\nota_publish signs what it serves\n")
sys.path.insert(0, ROOT)
import ota                                           # noqa: E402

proj = tempfile.mkdtemp(prefix="ota-sign-")
for name in ota_publish.SHIP:
    io.open(os.path.join(proj, name), "w", newline="").write("# %s\n" % name)
key = b"\x11" * 32
man = ota_publish.build_manifest(key, root=proj)
raw = open(os.path.join(proj, "manifest.json"), "rb").read()
sig = open(os.path.join(proj, "manifest.sig")).read()
check("the manifest carries a build number", isinstance(man.get("seq"), int),
      str(man.get("seq")))
check("its signature verifies on the device's code",
      ota.verify(key, raw, sig))
check("...and not under another key", not ota.verify(b"\x22" * 32, raw, sig))
names = ota_publish.served_names(man, proj)
check("the signature is staged and served with the manifest",
      "manifest.sig" in names and "manifest.sig" in
      os.listdir(ota_publish.stage(man, root=proj)), str(names[:3]))
ota_publish.build_manifest(None, root=proj)
check("an unsigned rebuild leaves no stale signature behind",
      not os.path.exists(os.path.join(proj, "manifest.sig")))
kp = os.path.join(proj, "ota.key")
ota_publish.new_key(kp)
check("--new-key writes a 32-byte key the device can load",
      ota_publish.load_key(kp) is not None
      and len(ota_publish.load_key(kp)) == 32)
try:
    ota_publish.new_key(kp)
    check("...and refuses to overwrite one", False)
except SystemExit:
    check("...and refuses to overwrite one", True)
check("ota.key is never in what ships", "ota.key" not in ota_publish.SHIP)
shutil.rmtree(proj, ignore_errors=True)

failed = results.count(False)
print("\n%d/%d passed\n" % (len(results) - failed, len(results)))
sys.exit(1 if failed else 0)
