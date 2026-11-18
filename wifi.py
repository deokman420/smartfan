"""Changing the WiFi network, and the broker, from the app -- without a ladder.

Until now a new router meant USB and `provision.py`. For a controller in a
ceiling that is a ladder, a laptop, and a length of cable, which is the wrong
amount of ceremony for typing a password.

Three things make that safe, and the endpoint is the least interesting of
them.

**A button press, not a password.**
`webapi` has no authentication, and that is a documented choice: a LAN device
whose physical control is a button on the wall, where the alternative is a
password on a thing with no screen. Reading humidity and pressing Boost sit
comfortably inside that. Rewriting the credentials does not -- anyone on the
network, guests included, could move the fan off it, and nothing about being
on the LAN says you are allowed to do that.

So this opens only after someone holds the button on the fan, and closes
again after WINDOW_S. Physical presence as authorisation. It is the same
reasoning that already refuses `update` over HTTP and keeps OTA on the
channel that has credentials.

**Try before commit.**
`provision.needs_provisioning()` fires when credentials are *absent*, never
when they are *wrong*. Writing a mistyped password would therefore produce
the exact failure this feature exists to prevent: a fan on no network, with
valid-looking secrets, no access point, and no way in but USB.

So nothing is written until the radio has actually associated. The candidate
credentials live in `Link._try` and never touch the filesystem; if they do
not work there is nothing to roll back, because nothing was done. This is the
shape `ota.py` already uses for firmware, for the same reason.

**The broker rides along, because it is the same door.**
Moving the fan to a new broker cannot strand it: WiFi stays up and the app
is still reachable over HTTP whatever the answer. So the ladder argument
does not apply -- but the button does, for the same reason it applies to the
WiFi. Being on someone's network is not a license to point their fan at your
broker, and the credentials in play are the ones that reach Home Assistant.

It is built the same way anyway. A rejected broker leaves no trace and needs
nothing undone, which is worth having for its own sake: the failure is then
always "it did not work", never "it did not work and something changed".

**The answer has to arrive before the network moves.**
A successful switch takes the device off the network the request came in on,
so the reply cannot be the result -- the socket dies with the association.
POST therefore returns "trying", and the outcome is read afterwards from
`GET /api/wifi`. If it failed, the device is back on the old network and the
app can ask. If it worked, the app has to follow it to the new network, and
that is the honest thing to tell the user rather than a spinner that never
resolves.
"""

import time

try:
    import network
except ImportError:                        # host tests, syntax checks
    network = None


# How long a press keeps the door open. Long enough to type a password on a
# phone, short enough that a wall button pressed by a curious guest is not a
# standing invitation.
WINDOW_S = 180

# An association attempt gets this long before it is called a failure. The
# radio's own timeout in net.py is 20 s; this covers a couple of tries plus
# DHCP on a slow router.
TRY_S = 45

CLOSED, OPEN, TRYING, OK, FAILED = "closed", "open", "trying", "ok", "failed"

# What an attempt is trying to change. One at a time: the states above are
# per-attempt, and two in flight would have to agree about which one OK
# refers to.
WIFI, BROKER = "wifi", "broker"

# A broker gets less time than a radio. An MQTT connect is one bounded
# socket operation against a host that is already on the network, not an
# association and a DHCP lease, and net.py bounds the connect itself at 5 s.
BROKER_TRY_S = 20


def _now():
    return time.ticks_ms()


def _since(t):
    return time.ticks_diff(time.ticks_ms(), t)


class Provisioner:
    """The provisioning window and the credential swap, polled from the loop.

    Owns no radio of its own: `Link` is the only thing that talks to the WLAN
    interface, and this asks it. Two objects driving one interface is how you
    get a device that is associated according to one of them and not the
    other.
    """

    def __init__(self, link, log=None):
        self.link = link
        self._log = log or (lambda *a: None)
        self.state = CLOSED
        self.kind = None             # WIFI or BROKER, while an attempt runs
        self._opened_at = None
        self._started_at = None
        self.candidate = ""          # SSID or host being tried, to echo back
        self.error = ""
        self.changed_at = None       # ticks of the last OK, for the app

    # -- the window --------------------------------------------------------

    def open(self, why="button"):
        """Called from the button handler. Restarts the clock if already open."""
        self._opened_at = _now()
        if self.state in (CLOSED, OK, FAILED):
            self.state = OPEN
            self.error = ""
        self._log("wifi: provisioning window open for %ds (%s)" % (WINDOW_S, why))
        return True

    def close(self, why="closed"):
        if self.state == TRYING:
            return False             # never abandon an attempt mid-flight
        self._opened_at = None
        self.state = CLOSED
        self._log("wifi: window %s" % why)
        return True

    @property
    def is_open(self):
        # A change that worked spends the press. OK used to keep the door
        # open for the rest of the 180 s, so after the owner's change anyone
        # on the LAN could make a second one -- including pointing the fan at
        # their own broker. A failure leaves it open for a retry.
        return self.state in (OPEN, TRYING) or (
            self.state == FAILED and self.seconds_left > 0)

    @property
    def changing_broker(self):
        """True while a broker is being tried, or the window could start one.

        main.py refuses OTA while this holds: a broker on trial, or one that
        could be swapped in, is not the authenticated channel OTA relies on.
        """
        return self.is_open or (self.state == TRYING and self.kind == BROKER)

    @property
    def seconds_left(self):
        if self._opened_at is None:
            return 0
        left = WINDOW_S - _since(self._opened_at) / 1000.0
        return int(left) if left > 0 else 0

    # -- the change --------------------------------------------------------

    def start(self, ssid, psk, country=None):
        """Begin trying `ssid`. Returns (ok, message); never blocks."""
        if not self.is_open:
            return False, "the window is closed"
        if self.state == TRYING:
            return False, "already trying %s" % self.candidate
        if not ssid:
            return False, "no ssid"
        # The radio takes bytes; a 32-byte SSID and a 63-byte PSK are the
        # standards' limits, and something longer is a client bug rather than
        # a network nobody can reach.
        if len(ssid) > 32:
            return False, "ssid too long"
        if psk and len(psk) > 63:
            return False, "password too long"

        self.candidate = ssid
        self.kind = WIFI
        self._country = country
        self._started_at = _now()
        self.state = TRYING
        self.error = ""
        self._log("wifi: trying %s" % ssid)
        self.link.try_wifi(ssid, psk)
        self._psk = psk
        return True, "trying"

    def start_broker(self, host, port=1883, user="", password=None,
                     client_id=None):
        """Begin trying a broker. Returns (ok, message); never blocks.

        An empty host is a legitimate answer, not a missing field: it means
        "no broker", which is a supported way to run this device rather than
        a half-finished setup. It needs no connection to prove, so it commits
        at once.
        """
        if not self.is_open:
            return False, "the window is closed"
        if self.state == TRYING:
            return False, "already trying %s" % self.candidate

        host = (host or "").strip()
        if len(host) > 120:
            return False, "host too long"
        # An absent port defaults; a port of 0 does not. `int(port or 1883)`
        # conflated the two and quietly turned "0" into 1883, which is a
        # setting silently ignored rather than a setting refused.
        if port in (None, ""):
            port = 1883
        try:
            port = int(port)
        except (TypeError, ValueError):
            return False, "port must be a number"
        if not 1 <= port <= 65535:
            return False, "port out of range"
        # None means "leave the password alone", which is what the app sends
        # when its password box is untouched. An empty STRING is different:
        # it means a broker that wants no password. Conflating them would
        # wipe the credential every time somebody corrected a typo in the
        # hostname, and the wipe would only show up as a broker that stopped
        # accepting the fan.
        if password is None:
            password = self.link.broker("MQTT_PASS", "") or ""
        if len(user or "") > 120 or len(password) > 120:
            return False, "credentials too long"

        self._broker = {
            "MQTT_HOST": host,
            "MQTT_PORT": port,
            "MQTT_USER": user or "",
            "MQTT_PASS": password,
        }
        if client_id:
            self._broker["MQTT_CLIENT_ID"] = client_id
        self.kind = BROKER
        self.candidate = host or "(none)"
        self._started_at = _now()
        self.state = TRYING
        self.error = ""

        if not host:
            # Nothing to connect to and nothing to prove. Write it and stop.
            self._log("wifi: turning the broker off")
            self._commit()
            return True, "saved"

        self._log("wifi: trying broker %s:%d" % (host, port))
        self.link.try_broker(self._broker)
        return True, "trying"

    def poll(self):
        """Advance the attempt. Call every loop; cheap unless TRYING."""
        if self.state == TRYING:
            self._poll_trying()
            return
        # An open window closes itself. Nothing else expires: OK and FAILED
        # stay readable until the window runs out, so the app can still ask
        # what happened after the radio has settled.
        if self.state == OPEN and self.seconds_left <= 0:
            self.close("expired")

    def _poll_trying(self):
        if self.kind == BROKER:
            # `online` means the MQTT session is up, which is the only
            # evidence that a host, a port and an account are all right.
            if self.link.online:
                self._commit()
                return
            if _since(self._started_at) > BROKER_TRY_S * 1000:
                self._give_up(self.link.last_error or "could not reach the broker")
            return

        if self.link.wifi_ok:
            self._commit()
            return
        if _since(self._started_at) > TRY_S * 1000:
            self._give_up(self.link.last_error or "could not join")

    def _commit(self):
        """It worked. Only now is anything written."""
        try:
            import provision
            if self.kind == BROKER:
                provision.update_secrets(**self._broker)
            else:
                provision._write_secrets(self.candidate, self._psk, self._country)
        except Exception as e:
            # Associated but could not be saved: the fan is on the new network
            # right now and will be on the old one after a reboot. Say so
            # rather than reporting a success that will not survive.
            self._log("wifi: reached %s but could not write secrets: %s"
                      % (self.candidate, e))
            self.state = FAILED
            self.error = "connected, but saving failed: %s" % e
            if self.kind == BROKER:
                self.link.stop_trying_broker()
            else:
                self.link.stop_trying()
            return
        if self.kind == BROKER:
            self.link.adopt_broker()
        else:
            self.link.adopt()
        self.state = OK
        self.changed_at = _now()
        self.error = ""
        self._log("wifi: now on %s, secrets written" % self.candidate)

    def _give_up(self, why):
        self.state = FAILED
        self.error = why
        self._log("wifi: %s failed (%s); going back" % (self.candidate, why))
        # Nothing to undo on the filesystem -- that is the point of trying
        # first. Dropping the override is the whole rollback.
        if self.kind == BROKER:
            self.link.stop_trying_broker()
        else:
            self.link.stop_trying()

    # -- what the app reads ------------------------------------------------

    def status(self):
        return {
            "state": self.state,
            "kind": self.kind or "",
            "open": self.is_open,
            "secs_left": self.seconds_left,
            "window_s": WINDOW_S,
            "ssid": self.link.ssid,
            "trying": self.candidate if self.state == TRYING else "",
            "error": self.error,
        }

    def broker_status(self):
        """What the fan is pointed at, minus the password.

        The account name is shown because it is what you check when the
        broker refuses you; the password never leaves the device, the same
        way it never leaves it over MQTT.
        """
        host = self.link.broker("MQTT_HOST", "") or ""
        return {
            "host": host,
            "port": self.link.broker("MQTT_PORT", 1883),
            "user": self.link.broker("MQTT_USER", "") or "",
            "has_password": bool(self.link.broker("MQTT_PASS", "")),
            "enabled": bool(host),
            "state": getattr(self.link, "state", "offline"),
        }

    def scan(self):
        """Visible networks, strongest first.

        Only while the window is open. It is setup-time information, and a
        scan briefly takes the radio away from the association -- harmless
        occasionally, not something to leave open to anything on the LAN that
        feels like polling it.
        """
        if not self.is_open:
            return None
        if network is None:
            return []
        try:
            wlan = network.WLAN(network.STA_IF)
            wlan.active(True)
            seen = {}
            for net in wlan.scan():
                ssid = net[0].decode("utf-8", "ignore") if isinstance(net[0], bytes) else str(net[0])
                if not ssid:
                    continue            # hidden; nothing useful to show
                rssi = net[3]
                # One entry per name: a mesh shows the same SSID several
                # times and the list should not.
                if ssid not in seen or rssi > seen[ssid]["rssi"]:
                    seen[ssid] = {"ssid": ssid, "rssi": rssi,
                                  "secure": net[4] != 0}
            out = list(seen.values())
            out.sort(key=lambda n: -n["rssi"])
            return out[:24]
        except Exception as e:
            self._log("wifi: scan failed (%s)" % e)
            return []
