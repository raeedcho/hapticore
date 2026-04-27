// Pin assignments for the Teensy 4.1 sync hub.
//
// All pin numbers live here. Do not scatter pin literals through the firmware.
//
// These assignments are PROVISIONAL. The bench-validation phase (5A.6) may
// reassign pins to optimize event-code data-line skew (single GPIO port write)
// or to accommodate physical board layout. Update this file in a single commit
// when reassigning; downstream code references symbolic names only.

#pragma once

namespace hapticore::pins {

// 1 Hz cross-system sync pulse output.
constexpr int SYNC_PULSE = 2;

// Camera frame trigger output.
constexpr int CAMERA_TRIGGER = 3;

// Reward solenoid TTL.
constexpr int REWARD = 4;

// 8-bit parallel event-code data bus, LSB to MSB.
// These are placeholder assignments. 5A.6 may move them to a single GPIO port
// for atomic byte writes; the strobe timing currently hides any inter-pin skew.
constexpr int EVENT_CODE_BIT0 = 14;
constexpr int EVENT_CODE_BIT1 = 15;
constexpr int EVENT_CODE_BIT2 = 16;
constexpr int EVENT_CODE_BIT3 = 17;
constexpr int EVENT_CODE_BIT4 = 18;
constexpr int EVENT_CODE_BIT5 = 19;
constexpr int EVENT_CODE_BIT6 = 20;
constexpr int EVENT_CODE_BIT7 = 21;

// Strobe line for the event-code bus. Rising edge latches the data lines.
constexpr int EVENT_CODE_STROBE = 22;

}  // namespace hapticore::pins
