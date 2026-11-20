// firmware/teensy/src/reward.cpp
#include "reward.h"

#include <cstdint>

#include "timing.h"

namespace hapticore::reward {

bool RewardController::request(uint32_t duration_ms, uint32_t now_ms) {
    if (active_) {
        ++dropped_count_;
        return false;
    }
    active_ = true;
    start_ms_ = now_ms;
    duration_ms_ = duration_ms;
    return true;
}

void RewardController::on_complete() {
    active_ = false;
}

bool RewardController::watchdog_expired(uint32_t now_ms) const {
    if (!active_) return false;
    // Unsigned subtraction gives the correct elapsed time across a millis()
    // wrap. The limit is summed in 64 bits so a huge duration cannot wrap it.
    const uint32_t elapsed_ms = now_ms - start_ms_;
    const uint64_t limit_ms =
        static_cast<uint64_t>(duration_ms_) + timing::REWARD_WATCHDOG_MARGIN_MS;
    return elapsed_ms > limit_ms;
}

}  // namespace hapticore::reward
