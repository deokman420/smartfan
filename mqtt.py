"""Minimal MQTT 3.1.1 publisher for MicroPython.

Reused verbatim from the pico2w-thb1s-bridge project. Publish-only for
now; phase 2b adds SUBSCRIBE and inbound PUBLISH handling so Home
Assistant can write parameters back.

Deliberately dependency-free: umqtt.simple is not bundled in the RPI_PICO2_W
build and mip-installing it needs a working network at first boot, which is
exactly what we cannot assume. Publish-only, QoS 0, with LWT support.
"""

import select
import socket
import struct


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

    def connect(self):
        ai = socket.getaddrinfo(self.server, self.port)[0][-1]
        self.sock = socket.socket()
        self.sock.settimeout(10)
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

    def ping(self):
        """PINGREQ, then verify PINGRESP.

        Writing to a half-open TCP socket succeeds silently for a long time,
        so without reading the reply the bridge can keep publishing into a
        dead connection while the broker has already fired our LWT.
        """
        self.sock.write(bytes([0xC0, 0x00]))
        poller = select.poll()
        poller.register(self.sock, select.POLLIN)
        if not poller.poll(5000):
            raise OSError("MQTT: no PINGRESP")
        resp = self.sock.read(2)
        if not resp or resp[0] != 0xD0:
            raise OSError("MQTT: bad PINGRESP")

    def close(self):
        try:
            if self.sock:
                self.sock.write(b"\xe0\x00")           # DISCONNECT
                self.sock.close()
        except Exception:
            pass
        self.sock = None
