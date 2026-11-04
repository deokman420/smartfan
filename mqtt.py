"""Minimal MQTT 3.1.1 publisher for MicroPython.

Started as the publish-only client from pico2w-thb1s-bridge. Phase 2b adds
SUBSCRIBE and inbound PUBLISH handling so Home Assistant can send commands.

All socket reads go through _recv() so that an inbound PUBLISH arriving
between a PINGREQ and its PINGRESP is dispatched rather than mistaken for a
malformed reply -- which is exactly the sort of bug that only shows up once
the broker is busy.

Deliberately dependency-free: umqtt.simple is not bundled in the RPI_PICO2_W
build and mip-installing it needs a working network at first boot, which is
exactly what we cannot assume. Publish-only, QoS 0, with LWT support.
"""

import select
import socket
import struct
import time


class MQTTClient:
    def __init__(self, client_id, server, port=1883, user=None, password=None,
                 keepalive=60, will_topic=None, will_msg=None):
        self.client_id = client_id
        self.server = server
        self.port = port
        self.user = user
        self.password = password
        self.keepalive = keepalive
        self.will_topic = will_topic
        self.will_msg = will_msg
        self.sock = None
        self.cb = None              # called as cb(topic:str, payload:bytes)
        self._pid = 0

    @staticmethod
    def _rlen(n):
        """Remaining Length: 7 bits per byte, high bit = continuation."""
        out = bytearray()
        while True:
            b = n % 128
            n //= 128
            if n:
                b |= 0x80
            out.append(b)
            if not n:
                return bytes(out)

    @staticmethod
    def _str(s):
        if isinstance(s, str):
            s = s.encode()
        return struct.pack("!H", len(s)) + s

    def connect(self, timeout_s=2):
        """TCP connect and CONNACK, each bounded by timeout_s.

        This runs inside the control loop, so together with everything else in
        the pass it has to stay well under the 8 s watchdog. net.py holds the
        budget. A broker this slow gets retried, which beats a reset.
        """
        ai = socket.getaddrinfo(self.server, self.port)[0][-1]
        self.sock = socket.socket()
        self.sock.settimeout(timeout_s)
        self.sock.connect(ai)

        flags = 0x02                                   # clean session
        payload = self._str(self.client_id)
        if self.will_topic:
            flags |= 0x04                              # will flag, QoS 0, not retained
            payload += self._str(self.will_topic) + self._str(self.will_msg)
        if self.user:
            flags |= 0x80
            payload += self._str(self.user)
            if self.password:
                flags |= 0x40
                payload += self._str(self.password)

        var = self._str("MQTT") + bytes([4, flags]) + struct.pack("!H", self.keepalive)
        pkt = b"\x10" + self._rlen(len(var) + len(payload)) + var + payload
        self.sock.write(pkt)

        resp = self.sock.read(4)
        if not resp or resp[0] != 0x20:
            raise OSError("MQTT: no CONNACK")
        if resp[3] != 0:
            raise OSError("MQTT: connection refused, code %d" % resp[3])
        return True

    def publish(self, topic, msg, retain=False):
        if isinstance(msg, str):
            msg = msg.encode()
        var = self._str(topic)
        hdr = 0x30 | (0x01 if retain else 0x00)        # PUBLISH, QoS 0
        pkt = bytes([hdr]) + self._rlen(len(var) + len(msg)) + var + msg
        self.sock.write(pkt)

    def set_callback(self, cb):
        """cb(topic, payload) for every inbound PUBLISH. Keep it fast."""
        self.cb = cb

    def subscribe(self, topic, qos=0, timeout_ms=2000):
        """SUBSCRIBE and wait for its SUBACK."""
        self._pid = (self._pid % 65534) + 1
        var = struct.pack("!H", self._pid)
        payload = self._str(topic) + bytes([qos])
        self.sock.write(bytes([0x82]) + self._rlen(len(var) + len(payload))
                        + var + payload)
        if not self._recv(timeout_ms, want=0x90):
            raise OSError("MQTT: no SUBACK for %s" % topic)
        return True

    def _readlen(self):
        """Remaining Length off the wire: 7 bits per byte, high bit continues."""
        n = 0
        sh = 0
        while True:
            b = self.sock.read(1)
            if not b:
                raise OSError("MQTT: truncated length")
            n |= (b[0] & 0x7F) << sh
            if not b[0] & 0x80:
                return n
            sh += 7
            if sh > 21:
                raise OSError("MQTT: bad length")

    def _recv(self, timeout_ms, want=None):
        """Read packets until `want` arrives or the budget runs out.

        Inbound PUBLISHes are dispatched to the callback as they are met, so
        a command landing mid-handshake is handled rather than discarded.
        Returns the matched packet type, or None on timeout.
        """
        poller = select.poll()
        poller.register(self.sock, select.POLLIN)
        deadline = time.ticks_add(time.ticks_ms(), timeout_ms)

        while True:
            left = time.ticks_diff(deadline, time.ticks_ms())
            if left < 0:
                left = 0
            if not poller.poll(left):
                return None          # nothing pending, or the budget ran out

            hdr = self.sock.read(1)
            if not hdr:
                raise OSError("MQTT: connection closed")
            kind = hdr[0] & 0xF0
            rlen = self._readlen()
            body = self.sock.read(rlen) if rlen else b""
            if rlen and (body is None or len(body) < rlen):
                raise OSError("MQTT: short read")

            if kind == 0x30:                       # PUBLISH
                tlen = (body[0] << 8) | body[1]
                topic = body[2:2 + tlen].decode()
                off = 2 + tlen
                if hdr[0] & 0x06:                  # QoS > 0 carries a packet id
                    off += 2
                if self.cb:
                    try:
                        self.cb(topic, body[off:])
                    except Exception:
                        pass                       # a bad handler must not kill the link
                continue

            if want is not None and kind == want:
                return kind
            # anything else (PUBACK, SUBACK we did not ask for) is ignored

    def check_msg(self):
        """Dispatch any already-arrived PUBLISH. Never blocks."""
        self._recv(0, want=None)

    def ping(self, timeout_ms=3000):
        """PINGREQ, then verify PINGRESP.

        Writing to a half-open TCP socket succeeds silently for a long time,
        so without reading the reply we would keep publishing into a dead
        connection while the broker has already fired our LWT.
        """
        self.sock.write(bytes([0xC0, 0x00]))
        if not self._recv(timeout_ms, want=0xD0):
            raise OSError("MQTT: no PINGRESP")

    def close(self):
        try:
            if self.sock:
                # Usually called because the socket has already stalled, so
                # the courtesy DISCONNECT gets very little time.
                self.sock.settimeout(0.5)
                self.sock.write(b"\xe0\x00")           # DISCONNECT
                self.sock.close()
        except Exception:
            pass
        self.sock = None
