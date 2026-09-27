"""Improv Serial protocol, tested on the host against real packet bytes.

    python tests/test_improv.py

No board required and no board touched. improv.py holds no transport on
purpose, so the codec and the state machine are fully exercisable here --
which is the point, because protocol bugs produce packets that look
plausible and decode to nothing.

The framing is anchored to a hand-computed byte string rather than to
whatever build() happens to emit. A test that encodes with the same
function it decodes with passes just as happily when both are wrong.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import improv                                                 # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("  %-48s %s %s" % (name, "PASS" if ok else "FAIL", detail))
    return ok


def cmd(command, payload=b""):
    """An RPC_COMMAND packet, built the way a browser would send one."""
    body = bytes([command, len(payload)]) + bytes(payload)
    return improv.build(improv.TYPE_RPC_COMMAND, body)


def wifi_payload(ssid, psk):
    s, p = ssid.encode(), psk.encode()
    return bytes([len(s)]) + s + bytes([len(p)]) + p


print("improv serial protocol (host, no board)\n")

# --- framing, anchored to bytes computed by hand ---------------------------
# 'IMPROV' + version 1 + type 0x01 + length 1 + state 0x02, checksum 0xE2.
EXPECTED = bytes([0x49, 0x4D, 0x50, 0x52, 0x4F, 0x56,
                  0x01, 0x01, 0x01, 0x02, 0xE2, 0x0A])
built = improv.state(improv.STATE_READY)
check("framing matches a hand-computed packet", built == EXPECTED,
      " ".join("%02X" % b for b in built))
check("packet ends with a newline", built.endswith(b"\n"))
check("checksum excludes the newline",
      improv.checksum(built[:-2]) == built[-2])

# --- parse ------------------------------------------------------------------
ptype, data, rest = improv.parse(built)
check("parses its own framing", ptype == improv.TYPE_CURRENT_STATE
      and data == bytes([improv.STATE_READY]) and rest == b"")

ptype, data, rest = improv.parse(b"\x00\x00garbage" + built)
check("resyncs past leading garbage", ptype == improv.TYPE_CURRENT_STATE)

ptype, data, rest = improv.parse(built[:5])
check("holds an incomplete header", ptype is None)
ptype, data, rest = improv.parse(built[:9])
check("holds an incomplete body", ptype is None and rest == built[:9])

# A single corrupt byte must not wedge the stream forever.
bad = bytearray(built)
bad[-2] ^= 0xFF
ptype, data, rest = improv.parse(bytes(bad) + built)
check("drops a bad checksum without wedging", ptype is None)
ptype, data, rest = improv.parse(rest)
check("recovers on the next good packet",
      ptype == improv.TYPE_CURRENT_STATE, "")

# Noise with no header at all must not grow without bound.
_, _, rest = improv.parse(b"x" * 400)
check("does not hoard headerless noise", len(rest) <= 5, "%d bytes" % len(rest))

# --- the state machine ------------------------------------------------------
joined = []


def on_wifi(ssid, psk):
    joined.append((ssid, psk))
    return "http://192.168.0.42" if psk == "correct-horse" else None


def scan():
    return [("Hollow Bastion", -41, True), ("Guest", -70, False)]


blinks = []
im = improv.Improv(("Smartfan", "2c.2", "RP2350", "Bathroom Smartfan"),
                   on_wifi, scan=scan, identify=lambda: blinks.append(1))

out = im.feed(cmd(improv.CMD_GET_CURRENT_STATE))
check("reports READY before provisioning",
      out == improv.state(improv.STATE_READY))

out = im.feed(cmd(improv.CMD_GET_DEVICE_INFO))
ptype, data, _ = improv.parse(out)
check("device info comes back as an RPC result",
      ptype == improv.TYPE_RPC_RESULT and data[0] == improv.CMD_GET_DEVICE_INFO)
# Four length-prefixed strings, nested inside the RPC body.
body = data[2:2 + data[1]]
fields, i = [], 0
while i < len(body):
    n = body[i]
    fields.append(body[i + 1:i + 1 + n].decode())
    i += 1 + n
check("device info carries all four fields",
      fields == ["Smartfan", "2c.2", "RP2350", "Bathroom Smartfan"],
      str(fields))

out = im.feed(cmd(improv.CMD_IDENTIFY))
check("identify runs the callback", len(blinks) == 1)

# --- network list -----------------------------------------------------------
out = im.feed(cmd(improv.CMD_GET_WIFI_NETWORKS))
packets, buf = [], out
while True:
    ptype, data, buf = improv.parse(buf)
    if ptype is None:
        break
    packets.append(data)
check("one result per network, plus a terminator", len(packets) == 3,
      "%d packets" % len(packets))
# The empty terminator is what tells the browser the list ended; without it
# it waits for more networks until it times out.
check("list is terminated by an empty result", packets[-1][1] == 0)
check("a network result carries ssid, rssi and lock state",
      b"Hollow Bastion" in packets[0] and b"-41" in packets[0]
      and b"YES" in packets[0])
check("open networks report NO", b"NO" in packets[1])

# --- credentials ------------------------------------------------------------
out = im.feed(cmd(improv.CMD_WIFI_SETTINGS,
                  wifi_payload("Hollow Bastion", "correct-horse")))
check("extracts the credentials from the nesting",
      joined and joined[-1] == ("Hollow Bastion", "correct-horse"),
      str(joined[-1]) if joined else "nothing")

seq, buf = [], out
while True:
    ptype, data, buf = improv.parse(buf)
    if ptype is None:
        break
    seq.append((ptype, data))
check("announces PROVISIONING before trying",
      seq[0] == (improv.TYPE_CURRENT_STATE,
                 bytes([improv.STATE_PROVISIONING])))
check("announces PROVISIONED on success",
      seq[1] == (improv.TYPE_CURRENT_STATE,
                 bytes([improv.STATE_PROVISIONED])))
check("returns the device URL", b"http://192.168.0.42" in seq[2][1])

# Asking again must repeat the URL, or the browser shows a provisioned
# device with nowhere to go.
out = im.feed(cmd(improv.CMD_GET_CURRENT_STATE))
check("repeats the URL when asked again", b"http://192.168.0.42" in out)

# --- the failure path -------------------------------------------------------
im2 = improv.Improv(("Smartfan", "2c.2", "RP2350", "Smartfan"), on_wifi)
out = im2.feed(cmd(improv.CMD_WIFI_SETTINGS, wifi_payload("Net", "wrong")))
seq, buf = [], out
while True:
    ptype, data, buf = improv.parse(buf)
    if ptype is None:
        break
    seq.append((ptype, data))
check("a failed join reports UNABLE_TO_CONNECT",
      any(t == improv.TYPE_ERROR_STATE
          and d == bytes([improv.ERR_UNABLE_TO_CONNECT]) for t, d in seq))
check("and returns to READY so it can be retried",
      seq[-1] == (improv.TYPE_CURRENT_STATE, bytes([improv.STATE_READY])))
check("state is READY again", im2.state == improv.STATE_READY)


def raises(ssid, psk):
    raise OSError("radio busy")


im3 = improv.Improv(("S", "1", "R", "S"), raises)
out = im3.feed(cmd(improv.CMD_WIFI_SETTINGS, wifi_payload("N", "p")))
check("a raising callback does not escape feed()",
      improv.ERR_UNABLE_TO_CONNECT in out and im3.state == improv.STATE_READY)

# --- malformed input --------------------------------------------------------
check("unknown command is rejected cleanly",
      improv.ERR_UNKNOWN_RPC in im.feed(cmd(0x7E)))

for label, payload in (("empty", b""),
                       ("ssid length past the end", b"\x40ab"),
                       ("missing password length", b"\x02ab"),
                       ("password length past the end", b"\x02ab\x40x")):
    body = bytes([improv.CMD_WIFI_SETTINGS, len(payload)]) + payload
    pkt = improv.build(improv.TYPE_RPC_COMMAND, body)
    try:
        r = im.feed(pkt)
        ok = improv.ERR_INVALID_RPC in r or improv.ERR_UNABLE_TO_CONNECT in r
    except Exception as e:
        ok, r = False, str(e)
    check("truncated credentials: %s" % label, ok)

# A packet split across two reads, which is what a serial port actually does.
im4 = improv.Improv(("S", "1", "R", "S"), on_wifi, scan=scan)
whole = cmd(improv.CMD_GET_CURRENT_STATE)
check("no reply from a half packet", im4.feed(whole[:6]) == b"")
check("replies once the rest arrives",
      im4.feed(whole[6:]) == improv.state(improv.STATE_READY))

# --- summary ----------------------------------------------------------------
passed = sum(1 for _, ok, _ in results if ok)
print("\n%d/%d passed" % (passed, len(results)))
fails = [n for n, ok, _ in results if not ok]
if fails:
    print("failed: " + ", ".join(fails))
sys.exit(0 if not fails else 1)
