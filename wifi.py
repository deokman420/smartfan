"""Changing the WiFi network from the app, without a ladder.

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
        self._opened_at = None
        self._started_at = None
        self.candidate = ""          # SSID being tried, for the app to echo
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
        return self.state in (OPEN, TRYING) or (
            self.state in (OK, FAILED) and self.seconds_left > 0)

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
        self._country = country
        self._started_at = _now()
        self.state = TRYING
        self.error = ""
        self._log("wifi: trying %s" % ssid)
        self.link.try_wifi(ssid, psk)
        self._psk = psk
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
        if self.link.wifi_ok:
            self._commit()
            return
        if _since(self._started_at) > TRY_S * 1000:
            self._give_up(self.link.last_error or "could not join")

    def _commit(self):
        """It associated. Only now is anything written."""
        try:
            import provision
            provision._write_secrets(self.candidate, self._psk, self._country)
        except Exception as e:
            # Associated but could not be saved: the fan is on the new network
            # right now and will be on the old one after a reboot. Say so
            # rather than reporting a success that will not survive.
            self._log("wifi: joined %s but could not write secrets: %s"
                      % (self.candidate, e))
            self.state = FAILED
            self.error = "joined, but saving failed: %s" % e
            return
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
        self.link.stop_trying()

    # -- what the app reads ------------------------------------------------

    def status(self):
        return {
            "state": self.state,
            "open": self.is_open,
            "secs_left": self.seconds_left,
            "window_s": WINDOW_S,
            "ssid": self.link.ssid,
            "trying": self.candidate if self.state == TRYING else "",
            "error": self.error,
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
