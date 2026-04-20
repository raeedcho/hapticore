# ADR-013: Teensy 4.1 as centralized sync hub

**Status:** Accepted
**Date:** 2026-04-20
**Context:** The rig needs hardware-timed TTL signals for several independent purposes: a continuous cross-system sync signal for offline alignment of Ripple and SpikeGLX (and, later, Neuropixels) data streams; frame-rate triggers for five Blackfly S cameras; behavioral event codes tagged into both neural recording streams; and reward TTL to the solenoid driver. Earlier planning (Phase 5A in the development roadmap) assumed these signals would come from Ripple Scout's digital I/O driven by `xipppy.digout()` calls from the Python task controller, with no new hardware required. Once camera triggering became a concrete requirement and Neuropixels integration moved onto the near-term roadmap, that plan no longer fit the problem.

## Decision

Use a single Teensy 4.1 microcontroller as the centralized hardware timing source for all non-safety rig TTL signals. All outputs derive from the Teensy's crystal oscillator. The Teensy is controlled by Hapticore's Python `SyncProcess` via USB serial. The firmware lives in `firmware/teensy/` in the Hapticore monorepo.

## Rationale

**Software-driven signal generation cannot meet the timing requirements.** The Phase 5A plan of calling `xipppy.digout()` from the task controller loop has ~ms-scale jitter from Python scheduling, USB transactions, and the Ripple control-network round trip. That jitter is acceptable for marking state transitions within a trial but not for driving camera frame triggers at 30–120 Hz or for generating a stable cross-system sync reference. Camera triggering in particular requires hardware timers — there is no software path on a general-purpose OS that produces jitter-free frame triggers across a multi-hour session.

**A single oscillator eliminates inter-signal drift by construction.** When the 1 Hz cross-system sync, camera triggers, and event-code strobes all come from the same crystal, there is zero relative drift between them regardless of session duration. Distributing signal generation across subsystems (e.g., Scout-generated sync, a separate timer card for cameras) would introduce a second clock domain that drifts measurably over a multi-hour recording and complicates offline alignment of camera frames to neural data.

**Peer recording systems are easier to align than master/slave.** With Ripple driving the sync, SpikeGLX's NI-DAQ has to sample Ripple's digital output as its sync input — the sync edges pass through Ripple's sample clock before reaching the NI-DAQ, adding one more layer of uncertainty. With a Teensy outside both systems, Ripple and the NI-DAQ are symmetric peers sampling the same physical edge, and the existing CatGT/TPrime workflow for Neuropixels alignment applies unchanged when that hardware comes online.

**Teensy 4.1 over Arduino Uno.** The Uno's ATmega328P has three hardware timers, and Timer2 is 8-bit without an exact prescaler for common camera rates — 60 Hz and 120 Hz require software division that reintroduces jitter, and ISR interactions between concurrent timers can reach 4–10 µs of jitter. The Teensy 4.1 (NXP i.MX RT1062, 600 MHz) provides four independent PIT-based `IntervalTimer` channels at ~6.67 ns resolution, 32 FlexPWM channels, and sub-microsecond ISR jitter, with >40 GPIO for future expansion. The per-unit cost difference (roughly $30 vs. $15) is negligible.

**Keep the 4 kHz haptic loop focused.** Hosting the camera trigger timer inside the C++ haptic server process is technically possible but mixes microsecond-scale TTL generation with the already-tight 250 µs haptic tick budget and couples camera timing to the haptic server's lifecycle. A separate microcontroller with a dedicated hardware-timer architecture is the right tool for this job and keeps the haptic server's responsibilities narrow.

**Teensyduino is well-supported by AI coding agents.** The realistic development workflow relies on Copilot for firmware iteration. Teensyduino + PlatformIO has deep training-data coverage; bespoke bare-metal or RTOS projects do not. This is a practical consideration, not an architectural one, but it meaningfully affects how much of the firmware can be delegated.

### What stays out of the Teensy path

The **FTDI FT232H beam-break sensor** is deliberately not routed through the Teensy. The beam break must be read by the C++ haptic server's 4 kHz loop with sub-0.5 ms latency for the safety field to engage before the monkey's hand clears the workspace. Routing it as Teensy → USB serial → Python `SyncProcess` → ZeroMQ → C++ would add ~5–10 ms of latency and place the Python layer on the safety path. The FTDI FT232H provides direct USB-to-GPIO access from C++ (via libftdi or libusb), keeping the read path inside the real-time process. See `rig-setup.md` § FTDI FT232H beam break sensor.

## Consequences

- The Phase 5A xipppy-DIO plan is superseded. xipppy remains the API Hapticore uses to *read* Ripple digital inputs (via `digin()`) and configure the Scout, but Hapticore does not drive Scout digital outputs for sync purposes.
- The Teensy is a single point of failure for sync, camera triggering, and reward delivery simultaneously. Mitigation: keep a spare pre-flashed Teensy on the rig; the serial protocol is stateless, so hot-swap requires only reconnecting USB and restarting `SyncProcess`.
- `firmware/teensy/` ships in the Hapticore monorepo (see ADR-006). The firmware, `SyncProcess`, and the event-code map must evolve atomically because a new task adding an event code requires firmware awareness and Python-side registration in the same commit.
- CI compiles the firmware without flashing hardware via a `pio run` job in `.github/workflows/ci.yml`. This catches firmware-breaking changes at PR time.
- The `SyncProcess` ↔ Teensy serial protocol becomes a new interface contract that requires its own documentation, parallel to `haptic_server_protocol.md`. This is tracked separately.
- If future requirements exceed what a single Teensy 4.1 can handle (e.g., more than 4 independent timer channels, or additional high-bandwidth triggers), the architecture admits a second microcontroller on a second USB port without structural changes — `SyncProcess` would own two serial connections. This is not anticipated.
- The event-code wire format (parallel vs. serial, bit width, strobe timing) is a separate decision tracked in a follow-up ADR. This ADR does not constrain it.
