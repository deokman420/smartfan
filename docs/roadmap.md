# Roadmap

## Roadmap

- **Phase 1** — local control. ✅ Done.
- **Phase 2** — WiFi, MQTT, Home Assistant, remote configuration, logged
  history. Plan: [`docs/phase-2-plan.md`](https://github.com/deokman420/smartfan/blob/main/docs/phase-2-plan.md).
  - **2a** — telemetry only, no inbound control. ✅ Done.
  - **2b** — remote configuration via MQTT subscribe.
  - **2c** — watchdog, backoff, offline buffering, safe-mode boot pin.
  - **2d** — dashboard (Home Assistant's own first).
- **Phase 3** — a real ceiling extractor on mains, in a proper enclosure.
  Designed, not built: [`hardware/v2/`](https://github.com/deokman420/smartfan/tree/main/hardware/v2/).
- **Phase 4** — provisioning and the app.
- **Phase 5** — the device serves the app and its own history, so a broker
  and Home Assistant are both optional. ✅ Done. Plan:
  [`docs/phase-5-plan.md`](https://github.com/deokman420/smartfan/blob/main/docs/phase-5-plan.md).
- **Phase 6** — multi-room.

## Deferred work

Parked items, each with what unblocks it: [`docs/TODO.md`](https://github.com/deokman420/smartfan/blob/main/docs/TODO.md).
