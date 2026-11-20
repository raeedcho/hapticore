// Compile-time timing constants for the sync hub firmware.
//
// These follow ADR-014 § "Decision" for the event-code strobe sequence.
// Bench validation in Phase 5A.6 may tighten these once measured.

#pragma once

#include <cstdint>

namespace hapticore::timing {

// 1 Hz cross-system sync. 500 ms half-period gives a 50% duty cycle.
constexpr uint32_t SYNC_PULSE_PERIOD_US = 500'000;

// Event-code strobe sequence (ADR-014). Total ~2 ms.
constexpr uint32_t EVENT_CODE_SETTLE_US = 500;
constexpr uint32_t EVENT_CODE_STROBE_US = 1000;
constexpr uint32_t EVENT_CODE_CLEAR_US  = 500;

// Camera trigger pulse width. Short pulse independent of trigger rate;
// Blackfly S cameras only need ~10 µs minimum trigger pulse width.
constexpr uint32_t CAMERA_PULSE_US = 100;

// Reward watchdog margin. The reward pulse is ended by a one-shot
// IntervalTimer; if that timer never fires, loop() closes the solenoid once the
// reward has been active for the commanded duration plus this margin. This is
// a backstop for a timer that never fired, not a policy limit on reward length.
constexpr uint32_t REWARD_WATCHDOG_MARGIN_MS = 500;

// Serial command buffer. Maximum command is `R10000\n` = 7 chars; 16 is plenty.
constexpr uint32_t SERIAL_BUFFER_SIZE = 16;

}  // namespace hapticore::timing
