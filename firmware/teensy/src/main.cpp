// Hapticore Teensy 4.1 sync hub firmware.
//
// Implements the serial protocol documented in
// docs/architecture.md § "Serial protocol" and python/hapticore/sync/protocol.py.
//
// Hardware-timed outputs:
//   - 1 Hz sync pulse (IntervalTimer)
//   - Camera frame trigger (IntervalTimer, two-phase ISR: rise, then fall)
//   - 8-bit parallel event codes with strobe (main-loop timed)
//   - Reward TTL (one-shot IntervalTimer ends the pulse; loop() only runs a
//     watchdog backstop, so serial stays live during a reward)
//
// See ADR-013 for architectural context, ADR-014 for event-code timing.

#include <Arduino.h>
#include <IntervalTimer.h>

#include "parser.h"
#include "pins.h"
#include "reward.h"
#include "timing.h"

namespace {

using namespace hapticore;

// ---- Hardware timers --------------------------------------------------------

// PIT budget: Teensy 4 has four PIT channels, and every IntervalTimer shares
// one PIT interrupt at one priority, so time spent in any callback delays every
// other timer's edge. Sync, camera, and reward use three channels, leaving one
// spare; do not use the fourth. No ISR may call delay() or delayMicroseconds(),
// busy-wait, loop, or call Serial.
IntervalTimer g_sync_timer;
IntervalTimer g_camera_timer;
IntervalTimer g_reward_timer;

// Sync pulse ISR: toggles the sync pin every half-period for a 1 Hz square wave.
void sync_isr() {
    static volatile bool level = false;
    level = !level;
    digitalWriteFast(pins::SYNC_PULSE, level ? HIGH : LOW);
}

// Camera trigger state. g_camera_period_us is written by loop() and read by the
// rising-edge ISR. g_camera_pin_high is the ISR's phase: it is touched only by
// the ISR and by stop_camera() after the timer has ended.
volatile uint32_t g_camera_period_us = 1'000'000u / 60u;  // default 60 Hz; not started
volatile bool g_camera_pin_high = false;

// Camera trigger ISR: two-phase, so it never waits. Interrupts alternate
// between raising and lowering the pin; each pulse is CAMERA_PULSE_US wide and
// rising edges are one period apart.
//
// IntervalTimer::update() on a running timer takes effect only after the
// current interval completes. Inside this ISR the current interval is the one
// that just started (the hardware reloaded it at expiry, before the ISR ran),
// so each phase must load the length of the interval AFTER that one:
//   - rising:  the interval now running is the pulse, so load the low time that
//              follows it (period - CAMERA_PULSE_US).
//   - falling: the interval now running is the low time, so load the next
//              pulse (CAMERA_PULSE_US).
// The PIT reloads in hardware at each expiry, so rising edges stay exactly one
// commanded period apart with no cumulative drift.
void camera_isr() {
    if (!g_camera_pin_high) {
        digitalWriteFast(pins::CAMERA_TRIGGER, HIGH);
        g_camera_pin_high = true;
        const uint32_t period_us = g_camera_period_us;  // read once
        g_camera_timer.update(period_us - timing::CAMERA_PULSE_US);
    } else {
        digitalWriteFast(pins::CAMERA_TRIGGER, LOW);
        g_camera_pin_high = false;
        g_camera_timer.update(timing::CAMERA_PULSE_US);
    }
}

// Reward state. g_reward is the hardware-free policy (see reward.h) and is only
// touched from loop(). The timer ISR touches only g_reward_done.
reward::RewardController g_reward;
volatile bool g_reward_done = false;

// Reward timer ISR: ends the pulse. Drives the pin LOW, ends its own one-shot
// timer, and flags completion for loop(). It never touches g_reward.
void reward_isr() {
    digitalWriteFast(pins::REWARD, LOW);
    g_reward_timer.end();
    g_reward_done = true;
}

// ---- Output handlers --------------------------------------------------------

void start_sync() {
    digitalWriteFast(pins::SYNC_PULSE, LOW);
    g_sync_timer.begin(sync_isr, timing::SYNC_PULSE_PERIOD_US);
}

void stop_sync() {
    g_sync_timer.end();
    digitalWriteFast(pins::SYNC_PULSE, LOW);
}

// Only stores the period. While the trigger runs, the camera timer's interval
// alternates between the pulse and the low time, so writing it here would
// overwrite the phase. The rising-edge ISR reads the stored period, so a rate
// change takes effect at the next rising edge.
void set_camera_rate(uint32_t rate_hz) {
    g_camera_period_us = 1'000'000u / rate_hz;
}

void stop_camera() {
    g_camera_timer.end();
    digitalWriteFast(pins::CAMERA_TRIGGER, LOW);
    g_camera_pin_high = false;
}

void start_camera() {
    // Start from a clean phase (and a LOW pin) even if the trigger is running.
    stop_camera();
    g_camera_timer.begin(camera_isr, g_camera_period_us);
    // begin() sets only the first interval, one period long, after which the
    // first rising edge fires. Load the first pulse's width now so it is in
    // place when that interval expires.
    g_camera_timer.update(timing::CAMERA_PULSE_US);
}

// Set the 8 data lines to the given byte. Pins may be on different GPIO ports;
// 5A.6 may relocate them to a single port for an atomic write. The strobe
// timing in send_event_code() hides any inter-pin skew.
void write_event_code_data(uint32_t code) {
    digitalWriteFast(pins::EVENT_CODE_BIT0, (code >> 0) & 1u ? HIGH : LOW);
    digitalWriteFast(pins::EVENT_CODE_BIT1, (code >> 1) & 1u ? HIGH : LOW);
    digitalWriteFast(pins::EVENT_CODE_BIT2, (code >> 2) & 1u ? HIGH : LOW);
    digitalWriteFast(pins::EVENT_CODE_BIT3, (code >> 3) & 1u ? HIGH : LOW);
    digitalWriteFast(pins::EVENT_CODE_BIT4, (code >> 4) & 1u ? HIGH : LOW);
    digitalWriteFast(pins::EVENT_CODE_BIT5, (code >> 5) & 1u ? HIGH : LOW);
    digitalWriteFast(pins::EVENT_CODE_BIT6, (code >> 6) & 1u ? HIGH : LOW);
    digitalWriteFast(pins::EVENT_CODE_BIT7, (code >> 7) & 1u ? HIGH : LOW);
}

// Emit an event code with the ADR-014 strobe sequence. Blocks for ~2 ms
// total. This is acceptable: the hardware timer ISRs run on their own
// schedule unaffected by main-loop blocking, and the USB-serial buffer
// holds incoming bytes during the block.
void send_event_code(uint8_t code) {
    write_event_code_data(code);
    delayMicroseconds(timing::EVENT_CODE_SETTLE_US);
    digitalWriteFast(pins::EVENT_CODE_STROBE, HIGH);
    delayMicroseconds(timing::EVENT_CODE_STROBE_US);
    digitalWriteFast(pins::EVENT_CODE_STROBE, LOW);
    delayMicroseconds(timing::EVENT_CODE_CLEAR_US);
    write_event_code_data(0);
}

// Start a single-shot reward pulse of the requested duration. Returns at once:
// a one-shot timer ends the pulse (reward_isr), so loop() keeps reading serial.
// A request while a reward is active is dropped, never queued, so the subject
// never gets more than commanded.
void start_reward(uint32_t duration_ms) {
    if (!g_reward.request(duration_ms, millis())) {
        // TODO(uplink): report g_reward.dropped_count() to the host once the
        // Teensy→host serial uplink exists.
        return;
    }
    // Start the timer before raising the pin, and raise the pin only if
    // begin() succeeds. Raising it first and then failing to start the timer
    // would leave the solenoid open until the watchdog fired.
    if (g_reward_timer.begin(reward_isr, duration_ms * 1000u)) {
        digitalWriteFast(pins::REWARD, HIGH);
    } else {
        g_reward.on_complete();
    }
}

// Reward housekeeping, run once per loop(): consume the ISR's completion flag,
// then enforce the watchdog. The watchdog is a backstop for a timer that never
// fired (reward_isr normally ends the pulse on time).
void update_reward() {
    if (g_reward_done) {
        g_reward_done = false;
        g_reward.on_complete();
    }
    if (g_reward.watchdog_expired(millis())) {
        g_reward_timer.end();
        digitalWriteFast(pins::REWARD, LOW);
        g_reward_done = false;
        g_reward.on_complete();
    }
}

// ---- Serial command dispatch -----------------------------------------------

void dispatch(parser::ParsedCommand cmd) {
    using parser::Command;
    switch (cmd.kind) {
        case Command::START_SYNC:       start_sync(); break;
        case Command::STOP_SYNC:        stop_sync(); break;
        case Command::SET_CAMERA_RATE:  set_camera_rate(cmd.value); break;
        case Command::START_CAMERA:     start_camera(); break;
        case Command::STOP_CAMERA:      stop_camera(); break;
        case Command::EVENT_CODE:       send_event_code(static_cast<uint8_t>(cmd.value)); break;
        case Command::REWARD:           start_reward(cmd.value); break;
        case Command::NONE:             break;  // silently ignored
    }
}

// Non-blocking serial line accumulator. Reads available bytes; on '\n', parses
// and dispatches the buffered line, then resets.
char g_buf[timing::SERIAL_BUFFER_SIZE];
uint32_t g_buf_len = 0;
bool g_buf_overflow = false;

void poll_serial() {
    while (Serial.available() > 0) {
        const int b = Serial.read();
        if (b < 0) return;
        const char c = static_cast<char>(b);
        if (c == '\n') {
            if (!g_buf_overflow) {
                dispatch(parser::parse(g_buf, g_buf_len));
            }
            g_buf_len = 0;
            g_buf_overflow = false;
            continue;
        }
        if (c == '\r') continue;  // tolerate stray CR
        if (!g_buf_overflow && g_buf_len < timing::SERIAL_BUFFER_SIZE) {
            g_buf[g_buf_len++] = c;
        } else {
            // Overflow: discard all remaining bytes until the next newline.
            g_buf_overflow = true;
        }
    }
}

}  // namespace

// ---- Arduino entry points ---------------------------------------------------

void setup() {
    using namespace hapticore;
    pinMode(pins::SYNC_PULSE,       OUTPUT);
    pinMode(pins::CAMERA_TRIGGER,   OUTPUT);
    pinMode(pins::REWARD,           OUTPUT);
    pinMode(pins::EVENT_CODE_BIT0,  OUTPUT);
    pinMode(pins::EVENT_CODE_BIT1,  OUTPUT);
    pinMode(pins::EVENT_CODE_BIT2,  OUTPUT);
    pinMode(pins::EVENT_CODE_BIT3,  OUTPUT);
    pinMode(pins::EVENT_CODE_BIT4,  OUTPUT);
    pinMode(pins::EVENT_CODE_BIT5,  OUTPUT);
    pinMode(pins::EVENT_CODE_BIT6,  OUTPUT);
    pinMode(pins::EVENT_CODE_BIT7,  OUTPUT);
    pinMode(pins::EVENT_CODE_STROBE, OUTPUT);

    digitalWriteFast(pins::SYNC_PULSE,        LOW);
    digitalWriteFast(pins::CAMERA_TRIGGER,    LOW);
    digitalWriteFast(pins::REWARD,            LOW);
    digitalWriteFast(pins::EVENT_CODE_STROBE, LOW);
    write_event_code_data(0);

    Serial.begin(115200);
    // Note: do not wait for Serial — the host opens the port asynchronously,
    // and blocking on Serial here would prevent standalone power-on operation.
}

void loop() {
    poll_serial();
    update_reward();
}
