// Host-side unit tests for the command parser.
//
// Run with: pio test -e native -f test_parser
//
// These tests do not exercise hardware behavior — they only verify that the
// serial protocol parser accepts the byte sequences emitted by
// python/hapticore/sync/protocol.py and rejects malformed input.

#include <cstring>
#include <unity.h>

#include "../../src/parser.h"

using hapticore::parser::Command;
using hapticore::parser::ParsedCommand;
using hapticore::parser::parse;

namespace {

ParsedCommand parse_str(const char* s) {
    return parse(s, static_cast<uint32_t>(strlen(s)));
}

void test_start_sync() {
    auto cmd = parse_str("S1");
    TEST_ASSERT_EQUAL(static_cast<int>(Command::START_SYNC), static_cast<int>(cmd.kind));
}

void test_stop_sync() {
    auto cmd = parse_str("S0");
    TEST_ASSERT_EQUAL(static_cast<int>(Command::STOP_SYNC), static_cast<int>(cmd.kind));
}

void test_start_camera() {
    auto cmd = parse_str("T1");
    TEST_ASSERT_EQUAL(static_cast<int>(Command::START_CAMERA), static_cast<int>(cmd.kind));
}

void test_stop_camera() {
    auto cmd = parse_str("T0");
    TEST_ASSERT_EQUAL(static_cast<int>(Command::STOP_CAMERA), static_cast<int>(cmd.kind));
}

void test_camera_rate() {
    auto cmd = parse_str("C60");
    TEST_ASSERT_EQUAL(static_cast<int>(Command::SET_CAMERA_RATE), static_cast<int>(cmd.kind));
    TEST_ASSERT_EQUAL_UINT32(60u, cmd.value);
}

void test_camera_rate_out_of_range_rejected() {
    TEST_ASSERT_EQUAL(static_cast<int>(Command::NONE), static_cast<int>(parse_str("C0").kind));
    TEST_ASSERT_EQUAL(static_cast<int>(Command::NONE), static_cast<int>(parse_str("C501").kind));
}

void test_event_code() {
    auto cmd = parse_str("E42");
    TEST_ASSERT_EQUAL(static_cast<int>(Command::EVENT_CODE), static_cast<int>(cmd.kind));
    TEST_ASSERT_EQUAL_UINT32(42u, cmd.value);
}

void test_event_code_boundaries() {
    TEST_ASSERT_EQUAL(static_cast<int>(Command::EVENT_CODE), static_cast<int>(parse_str("E0").kind));
    TEST_ASSERT_EQUAL(static_cast<int>(Command::EVENT_CODE), static_cast<int>(parse_str("E255").kind));
    TEST_ASSERT_EQUAL(static_cast<int>(Command::NONE), static_cast<int>(parse_str("E256").kind));
}

void test_reward() {
    auto cmd = parse_str("R150");
    TEST_ASSERT_EQUAL(static_cast<int>(Command::REWARD), static_cast<int>(cmd.kind));
    TEST_ASSERT_EQUAL_UINT32(150u, cmd.value);
}

void test_reward_boundaries() {
    TEST_ASSERT_EQUAL(static_cast<int>(Command::REWARD), static_cast<int>(parse_str("R1").kind));
    TEST_ASSERT_EQUAL(static_cast<int>(Command::REWARD), static_cast<int>(parse_str("R10000").kind));
    TEST_ASSERT_EQUAL(static_cast<int>(Command::NONE), static_cast<int>(parse_str("R0").kind));
    TEST_ASSERT_EQUAL(static_cast<int>(Command::NONE), static_cast<int>(parse_str("R10001").kind));
}

void test_garbage_rejected() {
    TEST_ASSERT_EQUAL(static_cast<int>(Command::NONE), static_cast<int>(parse_str("").kind));
    TEST_ASSERT_EQUAL(static_cast<int>(Command::NONE), static_cast<int>(parse_str("X").kind));
    TEST_ASSERT_EQUAL(static_cast<int>(Command::NONE), static_cast<int>(parse_str("S2").kind));
    TEST_ASSERT_EQUAL(static_cast<int>(Command::NONE), static_cast<int>(parse_str("EABC").kind));
    TEST_ASSERT_EQUAL(static_cast<int>(Command::NONE), static_cast<int>(parse_str("E").kind));  // missing arg
}

}  // namespace

int main() {
    UNITY_BEGIN();
    RUN_TEST(test_start_sync);
    RUN_TEST(test_stop_sync);
    RUN_TEST(test_start_camera);
    RUN_TEST(test_stop_camera);
    RUN_TEST(test_camera_rate);
    RUN_TEST(test_camera_rate_out_of_range_rejected);
    RUN_TEST(test_event_code);
    RUN_TEST(test_event_code_boundaries);
    RUN_TEST(test_reward);
    RUN_TEST(test_reward_boundaries);
    RUN_TEST(test_garbage_rejected);
    return UNITY_END();
}
