"""Runs before main.py on every boot. The recovery path.

Deliberately tiny, and excluded from OTA. This is what rescues the device
when an update does not come up, so it must not be replaceable by the thing
it exists to rescue you from.

Everything here is wrapped: a failure in boot.py must never stop main.py
from running, because a controller that boots on old code is fine and one
that does not boot at all is a ladder trip.
"""

try:
    import ota
    ota.boot_check()
except Exception as e:
    try:
        print("boot: ota check skipped (%s)" % e)
    except Exception:
        pass
