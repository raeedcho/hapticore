# Hapticore Teensy 4.1 sync hub firmware

Hardware-timed TTL signal generator for the Hapticore rig. Receives ASCII
commands over USB serial from the Python `SyncProcess` and drives:

- 1 Hz cross-system sync pulse (Ripple ↔ SpikeGLX alignment).
- 30–120 Hz camera frame trigger (Blackfly S external trigger input).
- 8-bit parallel event-code bus with strobe (behavioral event tagging into both
  recording streams).
- Reward TTL (solenoid driver).

See `docs/adr/013-teensy-sync-hub.md` for architectural context and
`docs/adr/014-parallel-event-codes.md` for event-code wire format.

## Build

This project uses PlatformIO. Install with:

```
pip install platformio
```

Then from this directory:

```
pio run                     # compile for Teensy 4.1
pio run -e native           # compile host-side parser tests (optional)
pio test -e native          # run host-side parser tests (optional)
```

## Flash

```
pio run -t upload           # flash via teensy-cli (Teensy must be plugged in)
```

The `teensy-cli` upload protocol requires the Teensy bootloader. Press the
button on the Teensy 4.1 if upload doesn't auto-detect.

## Pin assignments

See `src/pins.h`. All assignments are placeholder until bench validation in
Phase 5A.6.

## Serial protocol

Reference: `docs/architecture.md` § "Serial protocol" and
`python/hapticore/sync/protocol.py`. All commands are ASCII, terminated by `\n`.

| Command | Effect |
|---|---|
| `S1\n` | Start 1 Hz sync pulse on `SYNC_PULSE` pin. |
| `S0\n` | Stop 1 Hz sync pulse. |
| `C<rate>\n` | Set camera trigger rate to `<rate>` Hz (1–500). |
| `T1\n` | Start camera trigger. |
| `T0\n` | Stop camera trigger. |
| `E<code>\n` | Emit 8-bit event code (0–255) with strobe. |
| `R<duration_ms>\n` | Pulse reward TTL for `<duration_ms>` (1–10000). |

The firmware is one-way (host → firmware). It does not reply over serial.
