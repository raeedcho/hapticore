**Status:** Accepted
**Date:** 2026-05-08

# ADR-016: ZMQ-based data logging instead of LSL

## Context

Phase 5 originally included an `LSLMarkerProcess` for redundant event logging
and real-time monitoring via LabRecorder. The primary value of LSL in a
multi-machine recording rig is software clock synchronization across machines.

During implementation we determined that this role is already covered by the
Teensy's hardware 1 Hz sync pulse (ADR-013): every recording system (Ripple,
SpikeGLX, camera trigger) receives the same physical TTL edge, so
cross-machine alignment is a hardware property requiring no software clock
protocol.

A ZMQ subscriber writing directly to disk is simpler, has no external
dependency (no `pylsl`, no LabRecorder), and covers the same use cases:

- **Redundant event record**: behavioral events and state transitions written to
  TSV alongside the neural recording, suitable for post-hoc NWB conversion.
- **Data integrity verification**: haptic state logged at high rate (float64
  binary) so the behavioral record can be cross-checked against the neural
  stream independently.

Future real-time monitoring (e.g., online raster plots) will use ZMQ
subscribers directly over TCP for cross-machine access, not LSL outlets.
LSL can be revisited if third-party tool integration (e.g., BCI2000, OpenViBE)
becomes a concrete need.

## Decision

Replace `LSLMarkerProcess` with `DataLoggerProcess`, a ZMQ subscriber
subprocess that writes behavioral events (TSV) and haptic state (flat binary
+ JSON sidecar) to the session's `behavior/` directory.

`DataLoggerProcess`:

- Subscribes to `event_pub_address` on `TOPIC_EVENT` and `TOPIC_SESSION`.
- Subscribes to `haptic_state_address` on `TOPIC_STATE`.
- Starts with the session (subscriptions stay warm during test trials to avoid
  the ZMQ slow-joiner problem), but only writes to disk between
  `start_recording` and `stop_recording` `SessionControl` messages.
- Writes `{session_id}_events.tsv` (one row per state transition or trial
  event) and `{session_id}_haptic.bin` (little-endian float64, 10 columns,
  80 bytes/sample) plus a JSON sidecar describing columns and sample count.

`SessionManager` starts `DataLoggerProcess` when
`config.recording.data_logging_enabled` is `True` (default).

## Consequences

- No `pylsl` dependency, no LabRecorder required.
- Behavioral data record is always available for sessions with
  `data_logging_enabled=True`, regardless of whether Ripple or SpikeGLX are
  configured.
- The `lsl/` session subdirectory is removed from `_create_session_dirs()`.
  Existing data on disk is unaffected.
- Monitoring during acquisition requires a ZMQ subscriber (over TCP for
  cross-machine use) instead of LabRecorder.
