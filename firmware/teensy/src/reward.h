// firmware/teensy/src/reward.h
#pragma once

#include <cstdint>

namespace hapticore::reward {

// Policy for the single reward output: at most one reward in flight,
// overlapping requests dropped and counted (never queued), and a watchdog
// deadline that backstops the hardware timer ending the pulse.
//
// Hardware-free so it can be tested natively. It never touches a pin or timer;
// main.cpp starts the one-shot timer and reports its completion back here.
//
// Only loop() may call it, so it needs no volatile or atomic handling. The
// timer ISR never touches it: the ISR sets a flag, and loop() turns that flag
// into on_complete().
//
// All times are millis() values. Elapsed time is computed with unsigned
// subtraction (now_ms - start_ms), so it stays correct across the ~49.7-day
// millis() wrap.
class RewardController {
public:
    // Accept a reward of `duration_ms` that starts at `now_ms`. If a reward is
    // already active, returns false, increments dropped_count(), and leaves the
    // in-flight reward unchanged.
    bool request(uint32_t duration_ms, uint32_t now_ms);

    // Mark the active reward finished. No-op if no reward is active.
    void on_complete();

    // True if a reward has been active for longer than its duration plus
    // timing::REWARD_WATCHDOG_MARGIN_MS. Always false when no reward is active.
    bool watchdog_expired(uint32_t now_ms) const;

    bool active() const { return active_; }

    // Number of requests dropped because a reward was already active.
    uint32_t dropped_count() const { return dropped_count_; }

private:
    bool active_ = false;
    uint32_t start_ms_ = 0;
    uint32_t duration_ms_ = 0;
    uint32_t dropped_count_ = 0;
};

}  // namespace hapticore::reward
