# Updating over the air

```bash
python tools/ota_publish.py --dry-run    # what would change
python tools/ota_publish.py              # serve, and tell the device to pull
```

Builds and **signs** a manifest, copies it and the files it lists into a
temporary folder, serves only that folder on this machine's LAN address, and
publishes `update <url>` to the device. The device checks the manifest's
signature, downloads each changed file, verifies its sha256, and installs
**only once every file has verified** — a half-applied update is never a
state the device can reach.

## Only your build machine can update it

The manifest carries an HMAC-SHA256 made with `ota.key`, a secret that only
this machine and the fan hold. Without a valid signature the fan installs
nothing, whoever sent the `update` command and whatever server it names. Each
manifest also carries a build number (`seq`), and the fan refuses any build
older than the last one it installed, so a captured old update can't be
replayed. If `ota.key` is missing on the fan, it refuses every update rather
than falling back to unsigned ones.

Set it up once:

```bash
python tools/ota_publish.py --new-key    # writes ota.key (gitignored)
```

Then copy `ota.key` **and** `ota.py` to the fan's root over USB, the same way
as `boot.py` (see the flash procedure). `ota.py` is the recovery path and
never travels over the air, so the code that checks signatures and the key
arrive together. Treat `ota.key` like `secrets.py`: anyone who has it can
install code on the fan. A new key locks the fan out of updates until the
key is copied over USB again, so `--new-key` refuses to overwrite one.

## It cannot brick the device

That is the whole design, and it is tested by deliberately pushing a
`main.py` that cannot be imported:

```
ota: installed 1 file(s), resetting
[board resets]   [board resets]   [board resets]
fan controller starting; learning baseline for 60 s
telemetry: announced 30 entities
```

Three resets, then healthy code running again — unattended.

The failure that needs catching is `main.py` raising at import: MicroPython
then drops to the REPL and sits there, unreachable over the network, with no
second boot to trigger anything. So `boot.py` arms a watchdog whenever an
update is pending. Healthy code takes over feeding it; broken code does not,
the board resets, and after two failed attempts `boot.py` restores the
`.bak` files and reboots into the old build.

An update is only *kept* once the new code has run for 60 seconds without
faulting. Until then the rollback is still armed.

## Knowing what is actually on it

```
build   local 8e669867   device 8e669867   MATCH
```

The device hashes its own tracked files at boot and publishes the result as
a **Build** entity. `ota_publish.py` computes the same hash locally and
compares. Without this, the only way to know what an update really put on a
device in a ceiling is to go and get it.

`TRACKED` lives in `telemetry.py` and the publisher reads it from there
rather than keeping its own copy — two lists like that drift, and a drifted
copy reports a mismatch that is not one.

**`boot.py` and `ota.py` are never sent over the air.** They are the recovery
path, and a recovery path you can break remotely is not one. Flash those over
USB. `secrets.py` and `settings.json` are excluded too — they belong to the
device, not the build.
