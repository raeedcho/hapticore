// Host-side unit tests for the reward policy controller.
//
// Run with: pio test -e native -f test_reward
//
// These tests do not exercise hardware behavior — they only verify the
// hardware-free policy in src/reward.cpp: one reward at a time, overlapping
// requests dropped and counted, and the watchdog deadline that backstops the
// hardware timer.

#include <unity.h>

#include "../../src/reward.h"
#include "../../src/timing.h"

using hapticore::reward::RewardController;
using hapticore::timing::REWARD_WATCHDOG_MARGIN_MS;

namespace {

void test_accepted_request_sets_active() {
    RewardController rc;
    TEST_ASSERT_FALSE(rc.active());
    TEST_ASSERT_TRUE(rc.request(100, 1000));
    TEST_ASSERT_TRUE(rc.active());
    TEST_ASSERT_EQUAL_UINT32(0, rc.dropped_count());
}

void test_overlapping_request_is_dropped_and_counted() {
    RewardController rc;
    TEST_ASSERT_TRUE(rc.request(100, 1000));
    TEST_ASSERT_FALSE(rc.request(200, 1050));
    TEST_ASSERT_EQUAL_UINT32(1, rc.dropped_count());
    TEST_ASSERT_FALSE(rc.request(300, 1060));
    TEST_ASSERT_EQUAL_UINT32(2, rc.dropped_count());
    TEST_ASSERT_TRUE(rc.active());
}

void test_overlapping_request_keeps_original_duration() {
    // The in-flight reward's duration is observable through its watchdog
    // deadline. A dropped request with a different start and duration must not
    // move that deadline.
    RewardController rc;
    TEST_ASSERT_TRUE(rc.request(100, 1000));
    TEST_ASSERT_FALSE(rc.request(5000, 1050));
    const uint32_t deadline = 1000 + 100 + REWARD_WATCHDOG_MARGIN_MS;
    TEST_ASSERT_FALSE(rc.watchdog_expired(deadline - 1));
    TEST_ASSERT_TRUE(rc.watchdog_expired(deadline + 1));
}

void test_new_request_accepted_after_complete() {
    RewardController rc;
    TEST_ASSERT_TRUE(rc.request(100, 1000));
    rc.on_complete();
    TEST_ASSERT_FALSE(rc.active());

    TEST_ASSERT_TRUE(rc.request(200, 2000));
    TEST_ASSERT_TRUE(rc.active());
    TEST_ASSERT_EQUAL_UINT32(0, rc.dropped_count());

    // The new reward runs on its own start time and duration.
    const uint32_t deadline = 2000 + 200 + REWARD_WATCHDOG_MARGIN_MS;
    TEST_ASSERT_FALSE(rc.watchdog_expired(deadline - 1));
    TEST_ASSERT_TRUE(rc.watchdog_expired(deadline + 1));
}

void test_on_complete_without_active_reward_is_noop() {
    RewardController rc;
    rc.on_complete();
    TEST_ASSERT_FALSE(rc.active());
    TEST_ASSERT_EQUAL_UINT32(0, rc.dropped_count());

    // A stale completion after a finished reward changes nothing either.
    TEST_ASSERT_TRUE(rc.request(100, 1000));
    rc.on_complete();
    rc.on_complete();
    TEST_ASSERT_FALSE(rc.active());
    TEST_ASSERT_EQUAL_UINT32(0, rc.dropped_count());
}

void test_watchdog_false_when_no_active_reward() {
    RewardController rc;
    TEST_ASSERT_FALSE(rc.watchdog_expired(0));
    TEST_ASSERT_FALSE(rc.watchdog_expired(0xFFFFFFFFu));

    // Still false long after a reward that has completed.
    TEST_ASSERT_TRUE(rc.request(100, 1000));
    rc.on_complete();
    TEST_ASSERT_FALSE(rc.watchdog_expired(1000 + 100 + REWARD_WATCHDOG_MARGIN_MS + 1'000'000));
}

void test_watchdog_deadline_is_duration_plus_margin() {
    RewardController rc;
    TEST_ASSERT_TRUE(rc.request(1000, 5000));

    // The watchdog must not trip at the nominal end of the pulse: the timer
    // ISR ends it there. It only backstops a timer that never fired.
    TEST_ASSERT_FALSE(rc.watchdog_expired(5000));
    TEST_ASSERT_FALSE(rc.watchdog_expired(5000 + 1000));

    const uint32_t deadline = 5000 + 1000 + REWARD_WATCHDOG_MARGIN_MS;
    TEST_ASSERT_FALSE(rc.watchdog_expired(deadline - 1));
    TEST_ASSERT_TRUE(rc.watchdog_expired(deadline + 1));
}

void test_watchdog_does_not_trip_across_millis_rollover() {
    // Started 256 ms before millis() wraps to 0.
    const uint32_t start = 0xFFFFFF00u;
    RewardController rc;
    TEST_ASSERT_TRUE(rc.request(1000, start));

    TEST_ASSERT_FALSE(rc.watchdog_expired(start));
    TEST_ASSERT_FALSE(rc.watchdog_expired(0xFFFFFFFFu));  // just before the wrap
    TEST_ASSERT_FALSE(rc.watchdog_expired(0x00000100u));  // 512 ms elapsed, past the wrap
}

void test_watchdog_trips_past_deadline_across_millis_rollover() {
    const uint32_t start = 0xFFFFFF00u;
    RewardController rc;
    TEST_ASSERT_TRUE(rc.request(1000, start));

    // The deadline itself lies past the wrap (uint32_t arithmetic wraps).
    const uint32_t deadline = start + 1000 + REWARD_WATCHDOG_MARGIN_MS;
    TEST_ASSERT_FALSE(rc.watchdog_expired(deadline - 1));
    TEST_ASSERT_TRUE(rc.watchdog_expired(deadline + 1));
}

}  // namespace

int main() {
    UNITY_BEGIN();
    RUN_TEST(test_accepted_request_sets_active);
    RUN_TEST(test_overlapping_request_is_dropped_and_counted);
    RUN_TEST(test_overlapping_request_keeps_original_duration);
    RUN_TEST(test_new_request_accepted_after_complete);
    RUN_TEST(test_on_complete_without_active_reward_is_noop);
    RUN_TEST(test_watchdog_false_when_no_active_reward);
    RUN_TEST(test_watchdog_deadline_is_duration_plus_margin);
    RUN_TEST(test_watchdog_does_not_trip_across_millis_rollover);
    RUN_TEST(test_watchdog_trips_past_deadline_across_millis_rollover);
    return UNITY_END();
}
