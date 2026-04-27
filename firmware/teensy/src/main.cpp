// Hapticore Teensy 4.1 sync hub firmware.
//
// Implements the serial protocol documented in
// docs/architecture.md § "Serial protocol" and python/hapticore/sync/protocol.py.
//
// Hardware-timed outputs:
//   - 1 Hz sync pulse (IntervalTimer)
//   - Camera frame trigger (IntervalTimer)
//   - 8-bit parallel event codes with strobe (main-loop timed)
//   - Reward TTL (main-loop timed, single shot per command)
//
// See ADR-013 for architectural context, ADR-014 for event-code timing.

#include <Arduino.h>
#include <IntervalTimer.h>

#include "parser.h"
#include "pins.h"
#include "timing.h"

namespace {

using namespace hapticore;

// ---- Hardware timers --------------------------------------------------------

IntervalTimer g_sync_timer;
IntervalTimer g_camera_timer;

// Sync pulse ISR: toggles the sync pin every half-period for a 1 Hz square wave.
void sync_isr() {
    static volatile bool level = false;
    level = !level;
    digitalWriteFast(pins::SYNC_PULSE, level ? HIGH : LOW);
}

// Camera trigger ISR: emits a short pulse, returns. Pulse width is short enough
// that delayMicroseconds() inside the ISR is acceptable here (≤100 µs vs.
// ~16 ms minimum inter-trigger period at 60 Hz).
void camera_isr() {
    digitalWriteFast(pins::CAMERA_TRIGGER, HIGH);
    delayMicroseconds(timing::CAMERA_PULSE_US);
    digitalWriteFast(pins::CAMERA_TRIGGER, LOW);
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

uint32_t g_camera_period_us = 1'000'000u / 60u;  // default 60 Hz; not started

void set_camera_rate(uint32_t rate_hz) {
    g_camera_period_us = 1'000'000u / rate_hz;
}

void start_camera() {
    digitalWriteFast(pins::CAMERA_TRIGGER, LOW);
    g_camera_timer.begin(camera_isr, g_camera_period_us);
}

void stop_camera() {
    g_camera_timer.end();
    digitalWriteFast(pins::CAMERA_TRIGGER, LOW);
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

// Pulse the reward TTL for the requested duration. Single-shot, blocks the
// main loop for the duration. For typical 50–300 ms rewards, this is fine —
// task code does not issue another command while waiting for reward.
void send_reward(uint32_t duration_ms) {
    digitalWriteFast(pins::REWARD, HIGH);
    delay(duration_ms);
    digitalWriteFast(pins::REWARD, LOW);
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
        case Command::REWARD:           send_reward(cmd.value); break;
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
}
