"""WiFi and MQTT supervision for the fan controller.

Design rule, inherited from the dhy22-wifi netlink module: every public call
returns a value, none of them raise. A controller in a ceiling is not
reachable, so an exception escaping this module is a device needing physical
intervention. Failures are reported upward and the caller decides.

The controller's second design rule is stronger: **the fan must work with the
network down**. So this module is a state machine polled from the control
loop, never a blocking call inside it. It gives up its turn quickly, and the
one operation that genuinely blocks -- the MQTT socket connect -- is bounded
by a short timeout and rate-limited by backoff, so a dead broker costs a few
seconds once a minute rather than a stall every pass.
"""

import time

try:
    import network
    import ntptime
except ImportError:                       # running off-device, e.g. syntax check
    network = ntptime = None

from mqtt import MQTTClient

OFFLINE, WIFI_WAIT, MQTT_WAIT, ONLINE = "offline", "wifi", "mqtt", "online"

# WiFi is up and no broker was configured, which is a working steady state
# rather than a half-finished one. The device runs its own HTTP server, so a
# user with no broker and no Home Assistant still has an app; MQTT is the
# optional extra, not the point. `online` stays False here on purpose --
# nothing should publish -- and `wifi_ok` is what the server waits on.
WIFI_ONLY = "wifi-only"

_BACKOFF_START_S = 2
_BACKOFF_MAX_S = 60
_WIFI_TIMEOUT_S = 20
_MQTT_TIMEOUT_S = 5                       # socket connect + CONNACK
_PING_INTERVAL_S = 30


def _now():
    return time.ticks_ms()


def _since(t):
    return time.ticks_diff(time.ticks_ms(), t)


class Link:
    """WiFi + MQTT connection, driven by repeated poll() calls."""

    def __init__(self, secrets, status_topic, log=None, subscribe=None,
                 on_msg=None, hostname=None):
        self._s = secrets
        self.status_topic = status_topic
        self._log = log or (lambda *a: None)
        self._subs = list(subscribe or ())
        self._on_msg = on_msg
        self._hostname = hostname
        self.clock_ok = False
        self.ip = None

        # An empty or absent MQTT_HOST is a statement, not an omission: run
        # WiFi only. Before this, secrets.py without a broker meant the
        # network layer failed on every pass and backed off forever.
        self.mqtt_enabled = bool(getattr(secrets, "MQTT_HOST", ""))

        self.state = OFFLINE
        self.client = None
        self._wlan = None
        self._entered = _now()
        self._backoff = _BACKOFF_START_S
        self._retry_at = _now()
        self._last_ping = _now()

        self.connects = 0                 # surfaced as telemetry: link churn
        self.failures = 0
        self.last_error = ""
        self._down_since = _now()         # offline until proven otherwise
        self.outage_s = 0                 # length of the most recent outage

        # Credentials to use INSTEAD of secrets.py, while wifi.py is trying a
        # new network. Held here rather than by mutating the secrets module
        # so that a failed attempt needs nothing undone: drop the override and
        # the next associate is the old network again, with no file written
        # and nothing to roll back. See wifi.py.
        self._try = None

        # Consecutive association failures. The count survives backoff, so it
        # measures "this network has stopped working" rather than "one attempt
        # was unlucky", which is what boot.py's AP fallback keys off.
        self.wifi_fails = 0

    # -- state ---------------------------------------------------------------

    @property
    def online(self):
        """MQTT is up. Publishing is gated on this and nothing else."""
        return self.state == ONLINE

    @property
    def wifi_ok(self):
        """The radio is associated, whether or not a broker is in use."""
        if self.state not in (ONLINE, WIFI_ONLY):
            return False
        try:
            return bool(self._wlan and self._wlan.isconnected())
        except Exception:
            return False

    @property
    def ssid(self):
        """The network being used right now, override included."""
        if self._try:
            return self._try[0]
        return getattr(self._s, "WIFI_SSID", "")

    @property
    def trying(self):
        return self._try is not None

    def try_wifi(self, ssid, psk):
        """Associate with these instead, starting now.

        Deliberately does not touch secrets.py. Whether these credentials are
        worth keeping is not known until the radio says so, and writing first
        is how a typo becomes a fan that needs a ladder.
        """
        self._try = (ssid, psk)
        self.drop("trying %s" % ssid)

    def stop_trying(self):
        """Give up on the override and go back to what secrets.py says."""
        if self._try is None:
            return
        self._try = None
        self.drop("back to %s" % getattr(self._s, "WIFI_SSID", ""))

    def adopt(self):
        """The override worked and has been written. It is just the network now."""
        self._try = None

    def drop(self, why="asked to"):
        """Disassociate and reconnect immediately, skipping the backoff.

        The backoff exists to stop a dead broker or a missing access point
        costing a stall every pass. A human changing the network is the one
        case where the next attempt should be now.
        """
        try:
            if self._wlan is not None:
                self._wlan.disconnect()
        except Exception:
            pass
        if self.client:
            self.client.close()
            self.client = None
        self._backoff = _BACKOFF_START_S
        self._retry_at = _now()
        self._goto(OFFLINE, "(%s)" % why)

    def _goto(self, state, why=""):
        if state == self.state:
            return
        self._log("net: %s -> %s %s" % (self.state, state, why))
        self.state = state
        self._entered = _now()

    def _fail(self, why):
        """Tear down and schedule a retry with exponential backoff."""
        self.failures += 1
        self.last_error = why
        if self._down_since is None:
            self._down_since = _now()
        if self.client:
            self.client.close()
            self.client = None
        self._retry_at = time.ticks_add(_now(), self._backoff * 1000)
        self._backoff = min(self._backoff * 2, _BACKOFF_MAX_S)
        self._goto(OFFLINE, "(%s, retry in %ds)" % (why, self._backoff))

    # -- the poll ------------------------------------------------------------

    def poll(self):
        """Advance the connection. Call every loop. Returns True if online.

        Cheap in every state except the two connect attempts, and those are
        gated behind backoff so they cannot run on consecutive passes.
        """
        try:
            if self.state == OFFLINE:
                self._poll_offline()
            elif self.state == WIFI_WAIT:
                self._poll_wifi()
            elif self.state == MQTT_WAIT:
                self._poll_mqtt()
            elif self.state == WIFI_ONLY:
                self._poll_wifi_only()
            elif self.state == ONLINE:
                self._poll_online()
        except Exception as e:                     # never propagate
            self._fail("poll: %s" % e)
        return self.online

    def _poll_offline(self):
        if time.ticks_diff(self._retry_at, _now()) > 0:
            return                                  # still backing off
        if network is None:
            return
        if self._wlan is None:
            # The hostname must be set BEFORE the interface comes up, and
            # that ordering is the whole reason http://fan.local/ works.
            #
            # MicroPython on this port has its own mDNS responder built in --
            # which is why a responder of ours could never bind port 5353 --
            # and it registers the name when the netif is brought up. Setting
            # the hostname afterwards leaves it registered under the old one.
            # Measured on the bench: with `network.hostname()` after
            # `active(True)`, fan.local did not resolve; moved before it, the
            # same board answers a ping in 111 ms.
            #
            # It is also the DHCP hostname, so the router's client list says
            # "fan" rather than an anonymous MAC.
            if self._hostname:
                try:
                    network.hostname(self._hostname)
                except Exception:
                    pass
            self._wlan = network.WLAN(network.STA_IF)
        self._wlan.active(True)
        try:
            self._wlan.config(pm=0xa11140)          # disable power save: latency
        except Exception:
            pass
        if not self._wlan.isconnected():
            ssid, psk = self._try or (self._s.WIFI_SSID, self._s.WIFI_PASS)
            self._wlan.connect(ssid, psk)
        self._goto(WIFI_WAIT)

    def _poll_wifi(self):
        if self._wlan.isconnected():
            self.ip = self._wlan.ifconfig()[0]
            self.wifi_fails = 0
            self._log("net: wifi up, ip %s" % self.ip)
            self._sync_clock()
            if not self.mqtt_enabled:
                self._backoff = _BACKOFF_START_S
                self._goto(WIFI_ONLY, "(no broker configured)")
                return
            self._goto(MQTT_WAIT)
            return
        if _since(self._entered) > _WIFI_TIMEOUT_S * 1000:
            try:
                self._wlan.disconnect()
            except Exception:
                pass
            self.wifi_fails += 1
            self._fail("wifi timeout")

    def _sync_clock(self):
        """One NTP attempt per association. Failure is not fatal."""
        if self.clock_ok or ntptime is None:
            return
        try:
            ntptime.settime()
            self.clock_ok = time.localtime()[0] >= 2024
            self._log("net: clock %s" % ("synced" if self.clock_ok else "still unset"))
        except Exception as e:
            self._log("net: ntp failed (%s)" % e)

    def _poll_mqtt(self):
        """The one genuinely blocking step, bounded and backoff-gated."""
        c = MQTTClient(
            client_id=self._s.MQTT_CLIENT_ID,
            server=self._s.MQTT_HOST,
            port=getattr(self._s, "MQTT_PORT", 1883),
            user=getattr(self._s, "MQTT_USER", None),
            password=getattr(self._s, "MQTT_PASS", None),
            keepalive=60,
            will_topic=self.status_topic,
            will_msg="offline",
        )
        try:
            c.connect()
        except Exception as e:
            self._fail("mqtt connect: %s" % e)
            return
        try:
            c.sock.settimeout(_MQTT_TIMEOUT_S)
        except Exception:
            pass

        if self._on_msg:
            c.set_callback(self._on_msg)
        try:
            for t in self._subs:
                c.subscribe(t)
                self._log("net: subscribed %s" % t)
        except Exception as e:
            c.close()                          # not yet self.client; close it here
            self._fail("subscribe: %s" % e)
            return

        self.client = c
        self.connects += 1
        if self._down_since is not None:
            self.outage_s = _since(self._down_since) // 1000
            self._down_since = None
        self._backoff = _BACKOFF_START_S
        self._last_ping = _now()
        self._goto(ONLINE)

    def _poll_wifi_only(self):
        """Hold WiFi up and keep the clock honest. Cheap, and never blocks.

        The clock retry matters more here than on the MQTT path: with no
        broker, the stored history is the only record of what happened, and
        store.py writes nothing until the year looks real.
        """
        if not self._wlan.isconnected():
            self.ip = None
            self._fail("wifi dropped")
            return
        ip = self._wlan.ifconfig()[0]
        if ip != self.ip:
            self.ip = ip
            self._log("net: ip now %s" % ip)
        if not self.clock_ok and _since(self._entered) > 60000:
            self._entered = _now()
            self._sync_clock()

    def _poll_online(self):
        try:
            self.client.check_msg()          # non-blocking; dispatches commands
        except Exception as e:
            self._fail("recv: %s" % e)
            return
        if _since(self._last_ping) < _PING_INTERVAL_S * 1000:
            return
        self._last_ping = _now()
        if self._wlan is not None and not self._wlan.isconnected():
            self._fail("wifi dropped")
            return
        try:
            self.client.ping()                      # verifies PINGRESP
        except Exception as e:
            self._fail("ping: %s" % e)

    # -- publishing ----------------------------------------------------------

    def publish(self, topic, payload, retain=False):
        """Returns True if it reached the socket. False is not fatal."""
        if not self.online:
            return False
        try:
            self.client.publish(topic, payload, retain=retain)
            return True
        except Exception as e:
            self._fail("publish: %s" % e)
            return False
