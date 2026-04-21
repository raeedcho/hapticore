# ADR-014: 8-bit parallel event codes via the Scout D-sub port

**Status:** Accepted
**Date:** 2026-04-20
**Context:** ADR-013 established that the Teensy 4.1 generates all non-safety rig TTL signals. One of those signals is the behavioral event code — a tag emitted on each task-controller state transition (and on demand by task code) that needs to appear in both the Ripple and SpikeGLX data streams with sub-millisecond timing accuracy. This ADR fixes the wire format the Teensy uses to emit those codes. The question is not *what* codes mean or *how* Python schedules them — it is the physical encoding scheme: parallel bus with a strobe, or serial on a single line.

## Decision

Use an **8-bit parallel data bus + 1 strobe line**, driven by the Teensy and routed symmetrically to both recording systems. An event is defined as the rising edge of the strobe; the code is the value of the 8 data lines latched at that edge.

- **Ripple side:** 8 data lines wired to 8 of the 16 parallel inputs on the Scout's 25-pin D-sub connector; strobe wired to Strobe A. xipppy surfaces the latched value as a `SegmentEventPacket` with `reason=1` and `.parallel` populated (the high byte reads zero when only 8 of 16 lines are wired).
- **SpikeGLX side:** 8 data lines + strobe wired to 9 DIO-capable channels on whatever non-neural acquisition hardware runs alongside the Neuropixels stream. For an NI-DAQ-based rig (PXIe card in the chassis), this is 9 DIO lines with the strobe configured as a change-detection source via NI-DAQmx. For a OneBox-based rig, this is 9 of the OneBox's 12 ADC channels configured as digital inputs. Either path is supported natively by SpikeGLX; the choice follows from the Neuropixels acquisition hardware in use on the rig (see `docs/rig-setup.md` for the current configuration).
- **Teensy `SyncProcess` serial protocol:** `E<code>` takes an 8-bit code value (0–255). The firmware sets the 8 data pins, waits a settle interval, pulses the strobe, and clears the data lines. Exact settle / pulse-width / clear timing is specified in the firmware protocol doc, not here.

The Scout's default "capture on strobe" mode is used. The optional Trellis setting that captures on any bit change is explicitly *not* enabled — it would generate a spurious event on every data-line transition during a code set-up.

## Rationale

**Timing precision is the dominant constraint.** With a parallel bus the event is a single physical edge: one timestamp, one code value, no decoding. With any single-line serial encoding (UART, Manchester, pulse-width, pulse-count), a multi-bit code spreads across milliseconds and the receiver has to decide what counts as the event time — first bit? last bit? middle of the word? Every choice has downsides when correlating with neural spikes at sub-millisecond resolution. A 16-bit code at 10 kbps takes 1.6 ms to transmit; two rapid state transitions can run into each other. The parallel scheme has none of these problems: emit, strobe, done.

**Ripple's data model is already parallel-native.** The Scout's parallel port latches the 16-bit data value on Strobe A or Strobe B and exposes it directly through xipppy's `SegmentEventPacket.parallel` field. No offline decoding is required on the Ripple side — the code is a field in every event packet, timestamped at the strobe edge. Any other encoding would require writing a Ripple-side decoder that consumes the digital stream as time-series data and reconstructs codes, which is strictly more code and strictly more ways to be wrong.

**This is the field standard.** Parallel event codes with a strobe are how every established primate neurophysiology system — MonkeyLogic, REX, TEMPO, Plexon-era rigs, NIMH setups — encode behavioral events. Downstream analysis tools (NeuroExplorer, custom MATLAB/Python code in most labs) already expect this pattern. A custom single-line serial encoding is self-documenting only to whoever designed it.

**8 bits fits the hardware path, and 256 codes is plenty.** The OneBox has 12 ADC channels total; 8 data + 1 strobe = 9 channels with 3 to spare. A 16-bit bus (17 channels) does not fit the OneBox at all. Since the current SpikeGLX-side hardware direction is OneBox, 8 bits is the ceiling imposed by hardware, not a soft starting point to be revisited. Independently, 256 codes comfortably covers state transitions across every task currently planned with healthy headroom, and the ceiling enforces useful discipline — it prevents packing rich payloads into spare bits (e.g., "bits 12–15 are an outcome flag") and keeps the compound semantic information where it belongs, in the behavior events CSV cross-referenced by timestamp.

**Teensy pin cost is trivial.** The Teensy 4.1 has >40 GPIO; spending 9 (8 data + 1 strobe) on the event bus is free in practice. A 74AHCT541 buffer chip between the Teensy and the destination cables provides drive current for cable fan-out to both recording systems and protects the Teensy against any incidental 5 V on the downstream side, at ~$1 BOM cost.

### Alternatives considered and rejected

- **Single-pin asynchronous serial (UART-like).** Timing ambiguity and non-standard decoding path, as above.
- **Two-pin synchronous serial (SPI-like, clock + data + latch).** Cleaner than async but still requires a custom decoder on both recording systems and still spreads the code over multiple samples.
- **SMA-only (no parallel port), using the 4 SMA inputs as a 4-bit bus.** Only 16 codes; consumes all 4 SMA inputs that are needed for sync and camera strobes.
- **Separate event logger (RS-232 to a third computer).** Introduces a third time base requiring its own alignment, defeating the "both recording systems see the same strobe edge" property that makes ADR-013's centralized sync work.
- **Bit-change-triggered capture (Trellis setting).** Would generate spurious events during code setup as data lines transition to their final value before the strobe. Explicitly not used.

## Consequences

- The parallel bus carries a **compact code index, not a rich payload.** The semantic meaning of each code — which task, which state transition, which trial — is logged separately in the behavior events CSV and cross-referenced by timestamp. Never try to pack rich information into the 8 bits; add a new code and log the details elsewhere.
- The event-code value space (0–255) requires a registry. A global reserved range for framework-emitted codes (e.g., trial start, trial end, state transitions from the `transitions` machine) plus per-task ranges for task-specific events. Registry lives alongside the firmware or in `SyncConfig`; specification is out of scope for this ADR.
- Wiring: one 25-pin D-sub cable from the Teensy buffer board to the Scout (8 data lines + Strobe A + ground), plus a parallel 9-conductor run to the SpikeGLX-side acquisition hardware (ribbon header for an NI-DAQ DIO card, or 9 BNCs for a OneBox breakout board). The Ripple Grapevine BNC Breakout (PN R01396) is Micro-D, not D-sub, and does not fit the Scout's port — use a custom D-sub pigtail or contact Ripple for a Scout-compatible adapter.
- SpikeGLX-side change detection is configured once per rig: an NI-DAQ-based setup uses NI-DAQmx change-detection on the strobe line; a OneBox-based setup configures the strobe ADC channel in digital-input mode (0.5 V / 1 V thresholds — well below the 3.3 V Teensy output). Both are standard patterns supported by the respective hardware with no custom driver work.
- Strobe polarity, settle time, pulse width, and inter-code dead time are firmware-level concerns and are specified in the firmware protocol documentation, not here. The current placeholder timing in `copilot-instructions.md:123` (set data → 500 µs settle → 1 ms strobe → 500 µs clear) is conservative — tighter is fine at the cable lengths involved. Firmware implementers should measure.
- `architecture.md:114` `SyncProcess` serial protocol: `E<code>` is now fully specified as an 8-bit value and can be documented as such. `architecture.md:200` should be updated to say "8-bit parallel event codes on the Scout D-sub port" instead of the currently vague "outputs event codes to both systems."
- 16-bit extension is not a general upgrade path. On the Ripple side the Scout already captures 16 bits natively, so Ripple would absorb an extension at no cost. On the SpikeGLX side, a 16-bit bus fits an NI-DAQ-based setup (DIO lines are cheap) but does not fit a OneBox (17 channels required, 12 available). The current OneBox direction therefore caps the bus at 8 bits across the rig as a whole. Revisiting this cap would require moving SpikeGLX to NI-DAQ/PXI hardware or accepting asymmetric event-code width between Ripple and SpikeGLX — neither of which is on the table.
