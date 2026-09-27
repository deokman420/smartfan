"""Improv Wi-Fi over serial. Phase 4c.

The open standard from Nabu Casa that ESPHome devices use, so Home
Assistant owners have met this flow before. It provisions over USB from a
browser using Web Serial, which makes it the right tool at flash time and
the right recovery path when nobody remembers which network a unit is on.

Deliberately transport-agnostic. The codec and the state machine take bytes
in and hand bytes back, so all of it can be tested on a host against real
packet bytes -- which is where protocol bugs actually live. The serial
plumbing is a thin adapter in provision.py.

Packet layout
-------------
    'IMPROV' | version | type | length | data[length] | checksum | '\\n'

The checksum is the low byte of the sum of everything from the 'I' through
the last data byte. It does not cover the trailing newline.

RPC_COMMAND and RPC_RESULT both carry  command | data_len | payload , and
strings inside a payload are each length-prefixed. Getting that nesting
wrong produces packets that look plausible and decode to nothing, so the
tests assert on exact bytes.
"""

HEADER = b"IMPROV"
VERSION = 1

# Packet types
TYPE_CURRENT_STATE = 0x01
TYPE_ERROR_STATE = 0x02
TYPE_RPC_COMMAND = 0x03
TYPE_RPC_RESULT = 0x04

# States
STATE_READY = 0x02
STATE_PROVISIONING = 0x03
STATE_PROVISIONED = 0x04

# Errors
ERR_NONE = 0x00
ERR_INVALID_RPC = 0x01
ERR_UNKNOWN_RPC = 0x02
ERR_UNABLE_TO_CONNECT = 0x03
ERR_UNKNOWN = 0xFF

# RPC commands
CMD_WIFI_SETTINGS = 0x01
CMD_IDENTIFY = 0x02
CMD_GET_CURRENT_STATE = 0x03
CMD_GET_DEVICE_INFO = 0x04
CMD_GET_WIFI_NETWORKS = 0x05

MAX_BUFFER = 512


def checksum(payload):
    total = 0
    for b in payload:
        total += b
    return total & 0xFF


def build(ptype, data=b""):
    """One framed packet, checksum and newline included."""
    body = HEADER + bytes([VERSION, ptype, len(data)]) + bytes(data)
    return body + bytes([checksum(body)]) + b"\n"


def state(s):
    return build(TYPE_CURRENT_STATE, bytes([s]))


def error(e):
    return build(TYPE_ERROR_STATE, bytes([e]))


def _lenstr(s):
    """One length-prefixed string, as every Improv payload nests them."""
    raw = s.encode("utf-8") if isinstance(s, str) else bytes(s)
    return bytes([len(raw)]) + raw


def rpc_result(command, strings=()):
    payload = b""
    for s in strings:
        payload += _lenstr(s)
    return build(TYPE_RPC_RESULT, bytes([command, len(payload)]) + payload)


def parse(buf):
    """Pull the first complete packet out of buf.

    Returns (ptype, data, rest). Returns (None, None, rest) when there is
    no complete packet yet, with rest advanced past anything that can never
    become one -- otherwise a single stray byte wedges the stream forever.
    """
    buf = bytes(buf)
    idx = buf.find(HEADER)
    if idx < 0:
        # Keep only a possible partial header at the tail.
        keep = len(HEADER) - 1
        return None, None, buf[-keep:] if len(buf) > keep else buf
    buf = buf[idx:]
    if len(buf) < 9:
        return None, None, buf
    length = buf[8]
    end = 9 + length
    if len(buf) < end + 1:
        if length > MAX_BUFFER:
            return None, None, buf[len(HEADER):]      # nonsense, resync
        return None, None, buf
    body, got = buf[:end], buf[end]
    rest = buf[end + 1:]
    if rest[:1] == b"\n":
        rest = rest[1:]
    if got != checksum(body):
        return None, None, rest                        # drop, do not wedge
    return buf[7], buf[9:end], rest


class Improv:
    """The Improv Serial state machine.

    Holds no transport. feed() takes whatever bytes arrived and returns the
    bytes to send back, so the whole protocol is exercisable on a host.
    """

    def __init__(self, device_info, on_wifi, scan=None, identify=None):
        """
        device_info  (firmware, version, chip, name) -- shown by the browser
        on_wifi      f(ssid, psk) -> url string on success, None on failure
        scan         f() -> [(ssid, rssi, locked), ...]
        identify     f() -- blink something so the user knows which unit
        """
        self.info = device_info
        self.on_wifi = on_wifi
        self.scan = scan
        self.identify = identify
        self.state = STATE_READY
        self.provisioned_url = None
        self._buf = b""

    def feed(self, data):
        out = b""
        if data:
            self._buf += bytes(data)
            if len(self._buf) > MAX_BUFFER:
                self._buf = self._buf[-MAX_BUFFER:]
        while True:
            ptype, payload, self._buf = parse(self._buf)
            if ptype is None:
                break
            if ptype == TYPE_RPC_COMMAND:
                out += self._rpc(payload)
        return out

    def _rpc(self, payload):
        if not payload:
            return error(ERR_INVALID_RPC)
        cmd = payload[0]

        if cmd == CMD_GET_CURRENT_STATE:
            out = state(self.state)
            # A device already on Wi-Fi must repeat the URL here, or the
            # browser shows "provisioned" with nowhere to go.
            if self.state == STATE_PROVISIONED and self.provisioned_url:
                out += rpc_result(CMD_WIFI_SETTINGS, [self.provisioned_url])
            return out

        if cmd == CMD_GET_DEVICE_INFO:
            return rpc_result(CMD_GET_DEVICE_INFO, list(self.info))

        if cmd == CMD_IDENTIFY:
            if self.identify:
                try:
                    self.identify()
                except Exception:
                    pass
            return rpc_result(CMD_IDENTIFY)

        if cmd == CMD_GET_WIFI_NETWORKS:
            out = b""
            if self.scan:
                try:
                    for ssid, rssi, locked in self.scan():
                        out += rpc_result(CMD_GET_WIFI_NETWORKS,
                                          [ssid, str(rssi),
                                           "YES" if locked else "NO"])
                except Exception:
                    pass
            # An empty result terminates the list. Without it the browser
            # waits for more networks until it times out.
            return out + rpc_result(CMD_GET_WIFI_NETWORKS)

        if cmd == CMD_WIFI_SETTINGS:
            creds = self._credentials(payload)
            if creds is None:
                return error(ERR_INVALID_RPC)
            ssid, psk = creds
            self.state = STATE_PROVISIONING
            out = state(STATE_PROVISIONING)
            try:
                url = self.on_wifi(ssid, psk)
            except Exception:
                url = None
            if url:
                self.state = STATE_PROVISIONED
                self.provisioned_url = url
                return out + state(STATE_PROVISIONED) + rpc_result(
                    CMD_WIFI_SETTINGS, [url])
            self.state = STATE_READY
            return out + error(ERR_UNABLE_TO_CONNECT) + state(STATE_READY)

        return error(ERR_UNKNOWN_RPC)

    @staticmethod
    def _credentials(payload):
        """ssid_len | ssid | pass_len | pass, nested inside the RPC body.

        Every length is validated against what actually arrived. A truncated
        packet that indexes past the end would otherwise raise inside the
        serial loop and take provisioning down with it.
        """
        try:
            if len(payload) < 3:
                return None
            body = payload[2:2 + payload[1]]
            if not body:
                return None
            sl = body[0]
            if len(body) < 1 + sl + 1:
                return None
            ssid = body[1:1 + sl]
            pl = body[1 + sl]
            if len(body) < 2 + sl + pl:
                return None
            psk = body[2 + sl:2 + sl + pl]
            return (ssid.decode("utf-8", "replace"),
                    psk.decode("utf-8", "replace"))
        except Exception:
            return None
