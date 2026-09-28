"""Print the setup credentials for a board, for the label.

    python tools/device_label.py [COM5]

The setup AP is WPA2, and its PSK is derived from the chip's unique id. That
makes it stable for the life of the board and recoverable without guessing,
but it also means there is no way to read it off the silkscreen. It is
printed once in the serial log when provisioning starts, which is no use to
somebody holding a finished unit in a ceiling.

So: derive it here, from the same inputs and the same code as the firmware,
and print something you can stick on the case.

Runs the derivation ON the device rather than reimplementing it. A second
copy of that function on the host would drift, and a drifted copy prints a
password that does not work -- which is worse than printing nothing.
"""

import subprocess
import sys

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM5"

CODE = """
import provision, machine, binascii, config
s, p = provision.identity()
print('SSID', s)
print('PSK', p)
print('UID', binascii.hexlify(machine.unique_id()).decode())
print('HOST', getattr(config, 'HOSTNAME', 'fan'))
"""


def main():
    try:
        r = subprocess.run(
            [sys.executable, "-m", "mpremote", "connect", PORT, "exec", CODE],
            capture_output=True, text=True, timeout=45)
    except Exception as e:
        print("could not reach the board on %s: %s" % (PORT, e))
        return 1

    out = {}
    for line in r.stdout.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            out[parts[0]] = parts[1].strip()

    if "SSID" not in out or "PSK" not in out:
        print("no credentials came back from %s" % PORT)
        if r.stderr.strip():
            print(r.stderr.strip())
        print("\nIs provision.py on the device? Flash it with:")
        print("  python -m mpremote connect %s cp provision.py :" % PORT)
        return 1

    line = "+" + "-" * 38 + "+"
    print()
    print(line)
    print("| %-36s |" % "SMARTFAN  -  WI-FI SETUP")
    print("| %-36s |" % "")
    print("| %-36s |" % ("Network   %s" % out["SSID"]))
    print("| %-36s |" % ("Password  %s" % out["PSK"]))
    print("| %-36s |" % "")
    print("| %-36s |" % "Join from a phone. Setup opens")
    print("| %-36s |" % "by itself, or visit 192.168.4.1")
    print("| %-36s |" % "")
    print("| %-36s |" % "Afterwards, the app is at")
    print("| %-36s |" % ("http://%s.local/" % out.get("HOST", "fan")))
    print(line)
    print()
    print("chip id %s" % out.get("UID", "?"))
    print()
    print("This is the setup credential, not a long-term secret -- it only")
    print("guards the window in which the device has no Wi-Fi of its own.")
    print()
    print("The address follows the room chosen during setup, so print the")
    print("label after provisioning if the owner renamed the unit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
