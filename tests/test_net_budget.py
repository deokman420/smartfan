#!/usr/bin/env python3
"""The network must never be able to outlast the watchdog.

Host only, no board needed. A fake socket and a fake clock put a stall at each
blocking step, and the test adds up the time one pass between watchdog feeds
could spend waiting.

    python tests/test_net_budget.py
"""

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

WDT_MS = 8000       # config.WDT_TIMEOUT_MS
MARGIN_MS = 1000    # sensors, flash and the web step need some of the pass

clock = [0]
time.ticks_ms = lambda: clock[0]
time.ticks_diff = lambda a, b: a - b
time.ticks_add = lambda a, b: a + b

import mqtt  # noqa: E402
import net   # noqa: E402

fails = 0


def check(name, ok, detail=""):
    global fails
    print("  %s  %s%s" % ("ok  " if ok else "FAIL", name, "  " + str(detail) if detail else ""))
    if not ok:
        fails += 1


class FakeSocket:
    """Answers the broker's half of the protocol unless told to stall."""

    def __init__(self, stall=()):
        self.stall = set(stall)
        self.timeout = None
        self.pending = b""

    def settimeout(self, s):
        self.timeout = s

    def _wait(self, what):
        if what in self.stall:
            clock[0] += int(self.timeout * 1000)
            raise OSError("ETIMEDOUT " + what)

    def connect(self, addr):
        self._wait("tcp")

    def write(self, data):
        self._wait("publish" if data[0] & 0xF0 == 0x30 else
                   "close" if data[0] == 0xE0 else "write")
        kind = data[0] & 0xF0
        if kind == 0x10:
            self.pending += b"\x20\x02\x00\x00"
        elif kind == 0x80 and "suback" not in self.stall:
            self.pending += b"\x90\x03" + data[2:4] + b"\x00"
        elif kind == 0xC0 and "pingresp" not in self.stall:
            self.pending += b"\xd0\x00"
        return len(data)

    def read(self, n):
        self._wait("connack" if self.pending[:1] == b"\x20" else "read")
        out, self.pending = self.pending[:n], self.pending[n:]
        return out

    def close(self):
        pass


class FakePoller:
    def __init__(self):
        self.sock = None

    def register(self, s, flags):
        self.sock = s

    def poll(self, timeout_ms):
        if self.sock.pending:
            return True
        clock[0] += timeout_ms
        return False


class FakeSocketModule:
    def __init__(self, sock):
        self.sock = sock

    def getaddrinfo(self, host, port):
        return [(0, 0, 0, "", (host, port))]

    def socket(self):
        return self.sock


class FakeSelectModule:
    POLLIN = 1

    def poll(self):
        return FakePoller()


class FakeWlan:
    def isconnected(self):
        return True


class Secrets:
    MQTT_HOST = "192.0.2.1"
    MQTT_PORT = 1883


TOPICS = ("fan/x/cmd", "fan/x/set")


def link(sock, online=True):
    mqtt.socket = FakeSocketModule(sock)
    mqtt.select = FakeSelectModule()
    k = net.Link(Secrets, "fan/x/status", subscribe=TOPICS)
    k._wlan = FakeWlan()
    k.state = net.MQTT_WAIT
    if online:
        k.poll()
        assert k.online
    return k


def budget(name, stall, online, act):
    sock = FakeSocket()
    k = link(sock, online=online)
    sock.stall = set(stall)
    start = clock[0]
    act(k)
    spent = clock[0] - start
    check("%s stays under the watchdog" % name, spent <= WDT_MS - MARGIN_MS, "%d ms" % spent)
    return k


print("timeouts")
print("  worst case for one pass, with a %d ms watchdog" % WDT_MS)


def fresh_connect(stall):
    sock = FakeSocket(stall)
    k = link(sock, online=False)
    start = clock[0]
    k.poll()
    return k, clock[0] - start


for name, stall in (("TCP connect", {"tcp"}), ("CONNACK", {"connack"}),
                    ("SUBACK", {"suback"}), ("close during failure", {"tcp", "close"})):
    k, spent = fresh_connect(stall)
    check("%s stays under the watchdog" % name, spent <= WDT_MS - MARGIN_MS, "%d ms" % spent)
    check("%s leaves the link offline" % name, not k.online)


def ping_due(k):
    k._last_ping -= net._PING_INTERVAL_S * 1000 + 1
    k.poll()


k = budget("PINGRESP", {"pingresp", "close"}, True, ping_due)
check("no PINGRESP takes the link down", not k.online)

k = budget("a stalled publish", {"publish", "close"}, True,
           lambda k: k.publish("t", "x"))
check("a stalled publish takes the link down", not k.online)


def slow_ping_then_publish(k):
    k._last_ping -= net._PING_INTERVAL_S * 1000 + 1
    sock = k.client.sock
    real = sock.read

    def slow(n):
        clock[0] += 900
        return real(n)
    sock.read = slow
    k.poll()
    sock.read = real
    for _ in range(40):
        k.publish("t", "x")


budget("a slow pass with an announce", set(), True, slow_ping_then_publish)

sock = FakeSocket()
k = link(sock)
k._spent = net._PUBLISH_BUDGET_MS + 1
check("a spent pass skips the write and keeps the link",
      k.publish("t", "x") is False and k.online)

print("record of the last stall")
store = {}


class Mem:
    def __getitem__(self, a):
        return store.get(a, 0)

    def __setitem__(self, a, v):
        store[a] = v


net._mem32 = Mem()
check("power-on leaves nothing to report", net._read_crumb() is None)
store[net._CRUMB_ADDR] = 0x12345678
check("unrelated contents are ignored", net._read_crumb() is None)
net._mark(net._PING)
check("the step is read back", net._read_crumb() == "ping", net._read_crumb())

sent = []
mqtt.MQTTClient.publish, real_publish = (
    lambda self, t, m, retain=False: sent.append((t, m, retain))), mqtt.MQTTClient.publish
net._mark(net._CONNECT)
k = link(FakeSocket())
check("it is reported once connected",
      sent and sent[0][0] == "fan/x/last_stall" and '"connect"' in sent[0][1] and sent[0][2], sent)
check("it is not reported twice", k.last_stall is None)
check("a clean pass reads as idle", net._read_crumb() == "idle", net._read_crumb())

sent.clear()
net._mark(net._IDLE)
k = link(FakeSocket())
check("a stall outside this module reads as idle", '"idle"' in sent[0][1], sent)
mqtt.MQTTClient.publish = real_publish
net._mem32 = None

print("%s" % ("all passed" if not fails else "%d FAILED" % fails))
sys.exit(1 if fails else 0)
