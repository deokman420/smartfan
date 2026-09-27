"""Wi-Fi provisioning over SoftAP with a captive portal. Phase 4a/4b.

Runs only when there are no usable credentials, before the control loop
starts, and never alongside it. On success it writes secrets.py and resets.

Why an access point and not Bluetooth
-------------------------------------
Web Bluetooth does not exist in iOS Safari and never has, so BLE
provisioning on an iPhone requires a native app. The app is optional by
design, so the primary path cannot be one that makes it mandatory. An
access point plus a captive portal works from any phone with nothing
installed. See docs/phase-4-app-plan.md.

The AP is WPA2, not open
------------------------
An open setup AP is a window in which anyone in range can post credentials
to the device. The PSK is derived from the chip's unique id and is meant to
be printed on the label, so it is stable for the life of the device and
recoverable without a serial cable.

Be accurate about what that buys: the PSK's secrecy rests on the label, and
the derivation lives in open source. It is a setup-window credential, not a
long-term secret. What it does is stop a passer-by joining and provisioning
the device, which is exactly the hole it exists to close.

One radio, one mode at a time
-----------------------------
The CYW43439 has a single antenna. AP and station modes are used strictly
sequentially: the scan happens before the AP comes up, and the AP is torn
down before the join is attempted. Running both at once forces them onto a
shared channel and drops portal clients whenever the router is elsewhere.

The consequence is visible to the user and the portal says so plainly: the
phone loses the setup network while the device tries the credentials.
"""

import hashlib
import machine
import network
import os
import socket
import time

import config as cfg

AP_IP = "192.168.4.1"
JOIN_TIMEOUT_S = 25
DEFAULT_PORTAL_S = 600

# No 0/O/1/I/L -- these get read off a label and typed by hand.
_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def _pick(digest, n):
    return "".join(_ALPHABET[digest[i] % len(_ALPHABET)] for i in range(n))


def identity():
    """(ssid, psk) for this board, stable for its lifetime.

    Two different hashes with distinct prefixes, so the SSID -- which is
    broadcast to everyone in range -- reveals nothing about the PSK.
    Ten characters of a 31-symbol alphabet is about 49 bits, ample for a
    credential that only has to survive a setup window.
    """
    uid = machine.unique_id()
    ssid = _pick(hashlib.sha256(b"smartfan-ap-v1|" + uid).digest(), 4)
    psk = _pick(hashlib.sha256(b"smartfan-psk-v1|" + uid).digest(), 10)
    return "Smartfan-" + ssid, psk


def needs_provisioning():
    """True when there is nothing usable to connect with."""
    try:
        import secrets
    except Exception:
        return True
    ssid = getattr(secrets, "WIFI_SSID", "")
    return not ssid or ssid == "your-ssid"


def _carry_over():
    """Existing non-WiFi secrets, so re-provisioning does not wipe MQTT."""
    keep = {}
    try:
        import secrets
        for k in ("WIFI_COUNTRY", "MQTT_HOST", "MQTT_PORT", "MQTT_USER",
                  "MQTT_PASS", "MQTT_CLIENT_ID"):
            if hasattr(secrets, k):
                keep[k] = getattr(secrets, k)
    except Exception:
        pass
    return keep


def _write_secrets(ssid, psk):
    """Write secrets.py. repr() keeps quotes and backslashes valid."""
    keep = _carry_over()
    lines = ['"""Written by provision.py. Edit by hand if you prefer."""',
             "",
             "WIFI_SSID = %r" % ssid,
             "WIFI_PASS = %r" % psk,
             'WIFI_COUNTRY = %r' % keep.get("WIFI_COUNTRY", "US"),
             ""]
    for k in ("MQTT_HOST", "MQTT_PORT", "MQTT_USER", "MQTT_PASS",
              "MQTT_CLIENT_ID"):
        if k in keep:
            lines.append("%s = %r" % (k, keep[k]))
    body = "\n".join(lines) + "\n"

    # Write to a temporary name and rename, so a power cut mid-write cannot
    # leave a half-written secrets.py that refuses to import.
    with open("secrets.new", "w") as f:
        f.write(body)
    try:
        os.remove("secrets.py")
    except OSError:
        pass
    os.rename("secrets.new", "secrets.py")


def _unquote(s):
    """Decode one application/x-www-form-urlencoded field."""
    s = s.replace("+", " ")
    out = ""
    i = 0
    while i < len(s):
        if s[i] == "%" and i + 2 < len(s):
            try:
                out += chr(int(s[i + 1:i + 3], 16))
                i += 3
                continue
            except ValueError:
                pass
        out += s[i]
        i += 1
    return out


def _esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


# --- the portal page --------------------------------------------------------
# Inlined on purpose. During setup the device is an access point with no
# route to the internet, so every external stylesheet, font or script
# resolves to a blank page. It also has to render inside the iOS Captive
# Network Assistant, which is a restricted WebView and not Safari.

_CSS = """
*{box-sizing:border-box}
body{margin:0;padding:24px 16px;background:#11151c;color:#e8edf5;
font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.w{max-width:480px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px;letter-spacing:-.2px}
.sub{color:#8b97a8;font-size:14px;margin:0 0 24px}
label{display:block;font-size:13px;color:#8b97a8;margin:16px 0 6px;
text-transform:uppercase;letter-spacing:.6px}
input{width:100%;padding:13px 14px;border-radius:10px;border:1px solid #2b3442;
background:#1a2029;color:#e8edf5;font-size:16px}
input:focus{outline:2px solid #4c9aff;outline-offset:-1px;border-color:#4c9aff}
button{width:100%;margin-top:22px;padding:14px;border:0;border-radius:10px;
background:#3d82f7;color:#fff;font-size:16px;font-weight:600}
button:active{background:#2f6ad4}
ul{list-style:none;margin:0;padding:0;border:1px solid #2b3442;border-radius:10px;
overflow:hidden}
li{padding:12px 14px;border-bottom:1px solid #222a35;display:flex;
justify-content:space-between;gap:12px;align-items:center}
li:last-child{border-bottom:0}
li b{font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
li span{color:#8b97a8;font-size:12px;flex:none}
.err{background:#3a1c1f;border:1px solid #6b2b31;color:#ffb4ba;padding:12px 14px;
border-radius:10px;margin-bottom:20px;font-size:14px}
.note{color:#8b97a8;font-size:13px;margin-top:20px;padding-top:16px;
border-top:1px solid #222a35}
"""

# Event delegation, reading textContent -- deliberately NOT an inline
# onclick carrying the SSID. Nearby network names are attacker-controlled:
# an AP called  a\  would break out of a quoted JS string argument, and one
# called  '><script>  out of the attribute. textContent has no such surface
# because the name never re-enters a parser.
_JS = """
document.getElementById('nets').addEventListener('click',function(e){
var li=e.target.closest('li');if(!li||!li.dataset.pick)return;
document.getElementById('s').value=li.querySelector('b').textContent;
document.getElementById('p').focus();});
"""


def _page(networks, error=""):
    rows = ""
    for ssid, rssi, locked in networks:
        rows += ('<li data-pick=1><b>%s</b><span>%s%s</span></li>'
                 % (_esc(ssid), "%d dBm" % rssi,
                    " &middot; locked" if locked else ""))
    if not rows:
        rows = "<li><b>No networks found</b><span>type it below</span></li>"
    err = '<div class="err">%s</div>' % _esc(error) if error else ""
    return (
        "<!DOCTYPE html><html><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Smartfan setup</title><style>%s</style></head><body><div class=w>"
        "<h1>Smartfan setup</h1>"
        "<p class=sub>Choose your Wi-Fi network.</p>"
        "%s<ul id=nets>%s</ul>"
        "<form method=POST action=/join>"
        "<label for=s>Network</label>"
        "<input id=s name=ssid autocapitalize=off autocorrect=off "
        "autocomplete=off required>"
        "<label for=p>Password</label>"
        "<input id=p name=psk type=password autocapitalize=off "
        "autocorrect=off autocomplete=off>"
        "<button type=submit>Connect</button></form>"
        "<p class=note>This setup network will disappear while the fan tries "
        "your password. If it works, the green light turns solid and the fan "
        "is ready &mdash; nothing else to do. If it does not, the setup "
        "network comes back so you can try again.</p>"
        "</div><script>%s</script></body></html>" % (_CSS, err, rows, _JS))


_WAIT = (
    "<!DOCTYPE html><html><head><meta charset=utf-8>"
    "<meta name=viewport content='width=device-width,initial-scale=1'>"
    "<title>Connecting</title><style>%s</style></head><body><div class=w>"
    "<h1>Connecting&hellip;</h1>"
    "<p class=sub>You can close this page.</p>"
    "<p class=note>This setup network is shutting down while the fan joins "
    "your Wi-Fi. Your phone will drop back to its usual network on its own."
    "<br><br><b>Solid green light:</b> connected, setup is done."
    "<br><b>Setup network reappears:</b> the password did not work. Rejoin it "
    "and try again.</p></div></body></html>" % _CSS)


class Portal:
    """SoftAP plus captive portal. Blocking; owns the board while it runs."""

    def __init__(self, log=print):
        self.log = log
        self.ssid, self.psk = identity()
        self.error = ""
        self.networks = []
        self._ap = None
        self._led_ok = machine.Pin(cfg.PIN_LED_OK, machine.Pin.OUT)
        self._led_run = machine.Pin(cfg.PIN_LED_RUN, machine.Pin.OUT)

    # --- radio ------------------------------------------------------------
    def scan(self):
        """Scan in station mode, before the AP exists. Never concurrently."""
        sta = network.WLAN(network.STA_IF)
        sta.active(True)
        time.sleep_ms(500)
        found = {}
        try:
            for net in sta.scan():
                name = net[0].decode("utf-8", "replace") if isinstance(
                    net[0], bytes) else str(net[0])
                if not name:
                    continue                      # hidden; type it by hand
                rssi, sec = net[3], net[4]
                if name not in found or rssi > found[name][0]:
                    found[name] = (rssi, sec != 0)
        except Exception as e:
            self.log("provision: scan failed (%s)" % e)
        sta.active(False)
        self.networks = sorted(((n, v[0], v[1]) for n, v in found.items()),
                               key=lambda r: -r[1])[:12]
        return self.networks

    def ap_up(self):
        ap = network.WLAN(network.AP_IF)
        ap.active(False)
        time.sleep_ms(200)
        ap.config(essid=self.ssid, password=self.psk)
        # Older ports infer WPA2 from the password; newer ones expose the
        # constant. Set it when it exists, never fail when it does not.
        try:
            ap.config(security=getattr(network.WLAN, "SEC_WPA_WPA2", 3))
        except Exception:
            pass
        ap.active(True)
        for _ in range(50):
            if ap.active():
                break
            time.sleep_ms(100)
        self._ap = ap
        self.log("provision: AP %s up on %s" % (self.ssid, AP_IP))
        return ap

    def ap_down(self):
        if self._ap:
            try:
                self._ap.active(False)
            except Exception:
                pass
            self._ap = None
        time.sleep_ms(300)

    def join(self, ssid, psk):
        """Try the credentials with the AP already down. True on success."""
        sta = network.WLAN(network.STA_IF)
        sta.active(True)
        try:
            sta.connect(ssid, psk)
        except Exception as e:
            self.log("provision: connect refused (%s)" % e)
            sta.active(False)
            return False
        t = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), t) < JOIN_TIMEOUT_S * 1000:
            if sta.isconnected():
                self.log("provision: joined %s as %s"
                         % (ssid, sta.ifconfig()[0]))
                return True
            self._led_run.value(not self._led_run.value())
            time.sleep_ms(250)
        # MicroPython cannot reliably tell a wrong password from a router
        # that is simply too far away, so the message must not claim either.
        sta.active(False)
        return False

    # --- servers ----------------------------------------------------------
    def _dns(self):
        """Answer every query with our own address, so the sheet opens."""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", 53))
        s.settimeout(0)
        return s

    def _dns_step(self, sock):
        try:
            data, addr = sock.recvfrom(256)
        except Exception:
            return
        if len(data) < 12:
            return
        try:
            # Echo the question, append one A record pointing here.
            resp = data[:2] + b"\x81\x80" + data[4:6] + data[4:6]
            resp += b"\x00\x00\x00\x00" + data[12:]
            resp += b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04"
            resp += bytes(int(o) for o in AP_IP.split("."))
            sock.sendto(resp, addr)
        except Exception:
            pass

    def _http(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", 80))
        s.listen(4)
        s.settimeout(0.4)
        return s

    def _serve(self, conn):
        """One request. Returns (ssid, psk) if the user submitted, else None."""
        conn.settimeout(4)
        try:
            raw = conn.recv(1024)
            if not raw:
                return None
            text = raw.decode("utf-8", "replace")
            head = text.split("\r\n", 1)[0]
            parts = head.split(" ")
            method = parts[0] if parts else "GET"
            path = parts[1] if len(parts) > 1 else "/"

            if method == "POST" and path.startswith("/join"):
                body = text.split("\r\n\r\n", 1)[1] if "\r\n\r\n" in text else ""
                need = 0
                for line in text.split("\r\n"):
                    if line.lower().startswith("content-length:"):
                        need = int(line.split(":", 1)[1].strip())
                while len(body.encode()) < need:
                    more = conn.recv(512)
                    if not more:
                        break
                    body += more.decode("utf-8", "replace")
                fields = {}
                for pair in body.split("&"):
                    if "=" in pair:
                        k, v = pair.split("=", 1)
                        fields[k] = _unquote(v)
                self._send(conn, _WAIT)
                ssid = fields.get("ssid", "").strip()
                return (ssid, fields.get("psk", "")) if ssid else None

            # Every other path, including the captive probes iOS, Android and
            # Windows fire, gets the portal. Returning what those probes
            # expect is precisely what stops the sign-in sheet appearing.
            self._send(conn, _page(self.networks, self.error))
            return None
        except Exception:
            return None
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _send(self, conn, html):
        payload = html.encode()
        conn.send(b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8"
                  b"\r\nContent-Length: " + str(len(payload)).encode() +
                  b"\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n")
        conn.send(payload)

    # --- the flow ---------------------------------------------------------
    def run(self, timeout_s=DEFAULT_PORTAL_S):
        """Serve until provisioned or timed out. True if credentials stuck."""
        self.log("")
        self.log("=" * 52)
        self.log(" SETUP NEEDED - no Wi-Fi credentials")
        self.log(" join this network from a phone:")
        self.log("   network  %s" % self.ssid)
        self.log("   password %s" % self.psk)
        self.log(" then open http://%s/ if it does not appear" % AP_IP)
        self.log("=" * 52)

        self.scan()
        self.log("provision: %d networks in range" % len(self.networks))
        self.ap_up()
        dns, http = self._dns(), self._http()
        started = time.ticks_ms()
        blink = time.ticks_ms()

        try:
            while time.ticks_diff(time.ticks_ms(), started) < timeout_s * 1000:
                self._dns_step(dns)
                try:
                    conn, _ = http.accept()
                except Exception:
                    conn = None
                if conn is not None:
                    creds = self._serve(conn)
                    if creds:
                        ssid, psk = creds
                        self.log("provision: trying %s" % ssid)
                        self._led_ok.value(0)
                        # Sequential, never concurrent: the AP goes down
                        # before the radio is asked to be a station.
                        dns.close()
                        http.close()
                        self.ap_down()
                        if self.join(ssid, psk):
                            _write_secrets(ssid, psk)
                            self._led_ok.value(1)
                            self._led_run.value(0)
                            self.log("provision: saved, restarting")
                            return True
                        self.error = ("Could not join %s. Check the password, "
                                      "or move the fan closer to the router."
                                      % ssid)
                        self.log("provision: join failed, AP back up")
                        self.ap_up()
                        dns, http = self._dns(), self._http()
                # Slow green pulse: waiting for someone to set me up.
                if time.ticks_diff(time.ticks_ms(), blink) > 700:
                    self._led_ok.value(not self._led_ok.value())
                    blink = time.ticks_ms()
        finally:
            try:
                dns.close()
                http.close()
            except Exception:
                pass
            self.ap_down()
            self._led_ok.value(0)
            self._led_run.value(0)

        self.log("provision: timed out, continuing offline")
        return False


def run(timeout_s=DEFAULT_PORTAL_S, log=print):
    """Provision if needed. Resets on success; returns False otherwise.

    The fan is forced off first. Provisioning owns the board, so nothing
    else is driving the output, and an energised fan with no control loop
    behind it is the one state that must never happen.
    """
    machine.Pin(cfg.PIN_FAN, machine.Pin.OUT, value=0)
    p = Portal(log=log)
    if p.run(timeout_s):
        time.sleep(1)
        machine.reset()
    return False
