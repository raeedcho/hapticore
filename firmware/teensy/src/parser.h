// firmware/teensy/src/parser.h
#pragma once

#include <cstdint>

namespace hapticore::parser {

enum class Command : uint8_t {
    NONE,            // empty line or parse error
    START_SYNC,      // S1
    STOP_SYNC,       // S0
    SET_CAMERA_RATE, // C<rate>
    START_CAMERA,    // T1
    STOP_CAMERA,     // T0
    EVENT_CODE,      // E<0..255>
    REWARD,          // R<1..10000>
};

struct ParsedCommand {
    Command kind = Command::NONE;
    // For SET_CAMERA_RATE: rate in Hz (1..500). For EVENT_CODE: 0..255.
    // For REWARD: duration_ms (1..10000). Unused otherwise.
    uint32_t value = 0;
};

// Parse a single newline-terminated command line.
// `line` need not be null-terminated; `len` is the number of bytes excluding
// the terminating newline. On parse failure, returns Command::NONE.
//
// Range validation matches python/hapticore/sync/protocol.py.
ParsedCommand parse(const char* line, uint32_t len);

}  // namespace hapticore::parser
