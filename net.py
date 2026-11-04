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

import sys
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
_PING_INTERVAL_S = 30

# Everything here runs between two feeds of an 8 s watchdog (WDT_TIMEOUT_MS).
# feed -> poll() -> web step -> sensors -> publish() -> feed. A timeout that
# looks generous on its own can still add up to a reset, and did: a 10 s connect
# timeout could not finish inside an 8 s watchdog at all.
_CONNECT_TIMEOUT_S = 2                    # TCP connect, then CONNACK
_SOCKET_TIMEOUT_S = 1.5                   # each read or write once connected
_SUBACK_TIMEOUT_MS = 2000
_PINGRESP_TIMEOUT_MS = 3000
_SUBSCRIBE_BUDGET_MS = 4000               # no new SUBSCRIBE after this long
_PUBLISH_BUDGET_MS = 4000                 # no new write after this long

# Which blocking step the link is in, kept where it survives a watchdog reset:
# WATCHDOG SCRATCH0 on the RP2350, which only a power-on clears. It costs no
# flash wear, so it can be updated this often. After a reset it names the step
# the loop was stuck in, and "idle" means the stall was outside this module.
_CRUMB_ADDR = 0x400D800C                  # WATCHDOG_BASE + SCRATCH0
_CRUMB_MAGIC = 0x0FA10000                 # small enough to stay a small int
_STEPS = ("idle", "wifi", "ntp", "connect", "subscribe", "recv", "ping",
          "publish", "close")
_IDLE, _WIFI, _NTP, _CONNECT, _SUBSCRIBE, _RECV, _PING, _PUBLISH, _CLOSE = range(9)

try:
    from machine import mem32 as _mem32
    if "RP2350" not in sys.implementation._machine:
        _mem32 = None                     # the address is RP2350-specific
except (ImportError, AttributeError):     # off-device, or a port without it
    _mem32 = None


def _mark(step):
    if _mem32 is not None:
        _mem32[_CRUMB_ADDR] = _CRUMB_MAGIC | step


def _read_crumb():
    """The step recorded before this boot, or None if nothing was recorded."""
    if _mem32 is None:
        return None
    v = _mem32[_CRUMB_ADDR]
    if v & 0xFFFF0000 != _CRUMB_MAGIC or (v & 0xFFFF) >= len(_STEPS):
        return None
    return _STEPS[v & 0xFFFF]


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

        # What the loop was doing when the last reset happened. Sent retained
        # on the first connection after it, so it outlives the next clean boot.
        self.last_stall = _read_crumb()
        self._stall_topic = status_topic[:-len("status")] + "last_stall"
        self._spent = 0                   # ms blocked since the last feed
        _mark(_IDLE)

        # Broker settings to use INSTEAD of secrets.py, while wifi.py is
        # trying a new one. Same shape and same reason as _try below.
        self._try_broker = None

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

    def broker(self, name, default=None):
        """One broker setting, override first, then secrets.py."""
        if self._try_broker is not None:
            return self._try_broker.get(name, default)
        return getattr(self._s, name, default)

    @property
    def mqtt_enabled(self):
        """Whether there is a broker to talk to at all.

        An empty or absent MQTT_HOST is a statement, not an omission: run
        WiFi only. Before this the network layer failed on every pass and
        backed off forever.

        A property rather than a flag set once in __init__, because the app
        can now add a broker to a device that had none, or take one away, and
        a value captured at construction would have kept insisting on the
        answer that was true at boot.
        """
        return bool(self.broker("MQTT_HOST", ""))

    def try_broker(self, settings):
        """Use these broker settings instead, starting now.

        Unlike try_wifi this cannot strand the device -- WiFi stays up and
        the app is still reachable over HTTP whatever happens -- but it is
        built the same way so that a rejected broker leaves no trace and
        needs nothing undone.
        """
        self._try_broker = dict(settings)
        self.drop("trying broker %s" % (settings.get("MQTT_HOST") or "(none)"))

    def stop_trying_broker(self):
        if self._try_broker is None:
            return
        self._try_broker = None
        self.drop("back to broker %s"
                  % (getattr(self._s, "MQTT_HOST", "") or "(none)"))

    def adopt_broker(self):
        self._try_broker = None

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
            _mark(_CLOSE)
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
            _mark(_CLOSE)
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
        started = _now()
        self._spent = 0
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
        _mark(_IDLE)
        # The loop has just fed the watchdog, so this starts the pass's count.
        self._spent = _since(started)
        return self.online

    def _poll_offline(self):
        if time.ticks_diff(self._retry_at, _now()) > 0:
            return                                  # still backing off
        if network is None:
            return
        _mark(_WIFI)
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
        _mark(_WIFI)
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
        _mark(_NTP)
        try:
            ntptime.settime()
            self.clock_ok = time.localtime()[0] >= 2024
            self._log("net: clock %s" % ("synced" if self.clock_ok else "still unset"))
        except Exception as e:
            self._log("net: ntp failed (%s)" % e)

    def _poll_mqtt(self):
        """The one genuinely blocking step, bounded and backoff-gated."""
        c = MQTTClient(
            client_id=self.broker("MQTT_CLIENT_ID", "pico-fan"),
            server=self.broker("MQTT_HOST", ""),
            port=self.broker("MQTT_PORT", 1883),
            user=self.broker("MQTT_USER", None) or None,
            password=self.broker("MQTT_PASS", None) or None,
            keepalive=60,
            will_topic=self.status_topic,
            will_msg="offline",
        )
        started = _now()
        _mark(_CONNECT)
        try:
            c.connect(_CONNECT_TIMEOUT_S)
        except Exception as e:
            c.close()                          # not yet self.client; close it here
            self._fail("mqtt connect: %s" % e)
            return
        try:
            c.sock.settimeout(_SOCKET_TIMEOUT_S)
        except Exception:
            pass

        if self._on_msg:
            c.set_callback(self._on_msg)
        _mark(_SUBSCRIBE)
        try:
            for t in self._subs:
                if _since(started) > _SUBSCRIBE_BUDGET_MS:
                    raise OSError("over budget")
                c.subscribe(t, timeout_ms=_SUBACK_TIMEOUT_MS)
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
        self._report_stall()

    def _report_stall(self):
        """Publish the recorded step once. A failure just tries again next time."""
        if self.last_stall is None:
            return
        self._log("net: last reset was in step: %s" % self.last_stall)
        when = time.time() if self.clock_ok else 0
        if self.publish(self._stall_topic,
                        '{"step":"%s","reported":%d}' % (self.last_stall, when),
                        retain=True):
            self.last_stall = None

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
        _mark(_RECV)
        try:
            self.client.check_msg()          # non-blocking; dispatches commands
        except Exception as e:
            self._fail("recv: %s" % e)
            return
        if _since(self._last_ping) < _PING_INTERVAL_S * 1000:
            return
        self._last_ping = _now()
        _mark(_PING)
        if self._wlan is not None and not self._wlan.isconnected():
            self._fail("wifi dropped")
            return
        try:
            self.client.ping(_PINGRESP_TIMEOUT_MS)  # verifies PINGRESP
        except Exception as e:
            self._fail("ping: %s" % e)

    # -- publishing ----------------------------------------------------------

    def publish(self, topic, payload, retain=False):
        """Returns True if it reached the socket. False is not fatal."""
        if not self.online:
            return False
        # A pass that has already spent its time skips the write, and whatever
        # was going out goes on a later pass.
        if self._spent > _PUBLISH_BUDGET_MS:
            return False
        started = _now()
        _mark(_PUBLISH)
        try:
            self.client.publish(topic, payload, retain=retain)
            _mark(_IDLE)
            self._spent += _since(started)
            return True
        except Exception as e:
            self._fail("publish: %s" % e)
            _mark(_IDLE)
            return False
