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

    def __init__(self, secrets, status_topic, log=None, subscribe=None, on_msg=None):
        self._s = secrets
        self.status_topic = status_topic
        self._log = log or (lambda *a: None)
        self._subs = list(subscribe or ())
        self._on_msg = on_msg
        self.clock_ok = False

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

    # -- state ---------------------------------------------------------------

    @property
    def online(self):
        return self.state == ONLINE

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
            self._wlan = network.WLAN(network.STA_IF)
        self._wlan.active(True)
        try:
            self._wlan.config(pm=0xa11140)          # disable power save: latency
        except Exception:
            pass
        if not self._wlan.isconnected():
            self._wlan.connect(self._s.WIFI_SSID, self._s.WIFI_PASS)
        self._goto(WIFI_WAIT)

    def _poll_wifi(self):
        if self._wlan.isconnected():
            self._log("net: wifi up, ip %s" % self._wlan.ifconfig()[0])
            self._sync_clock()
            self._goto(MQTT_WAIT)
            return
        if _since(self._entered) > _WIFI_TIMEOUT_S * 1000:
            try:
                self._wlan.disconnect()
            except Exception:
                pass
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
        self._backoff = _BACKOFF_START_S
        self._last_ping = _now()
        self._goto(ONLINE)

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
