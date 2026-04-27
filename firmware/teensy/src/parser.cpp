// firmware/teensy/src/parser.cpp
#include "parser.h"

#include <cstdint>

namespace hapticore::parser {

namespace {

// Parse a non-negative ASCII integer. Returns false on overflow, empty input,
// or any non-digit character. On success, `out` holds the parsed value.
bool parse_uint32(const char* s, uint32_t len, uint32_t& out) {
    if (len == 0) return false;
    uint32_t v = 0;
    for (uint32_t i = 0; i < len; ++i) {
        const char c = s[i];
        if (c < '0' || c > '9') return false;
        const uint32_t d = static_cast<uint32_t>(c - '0');
        // Overflow guard. Max possible input is 5 digits ('10000' for reward).
        if (v > (UINT32_MAX - d) / 10u) return false;
        v = v * 10u + d;
    }
    out = v;
    return true;
}

}  // namespace

ParsedCommand parse(const char* line, uint32_t len) {
    ParsedCommand cmd;
    if (len == 0) return cmd;

    const char op = line[0];
    const char* arg = line + 1;
    const uint32_t arg_len = len - 1;

    switch (op) {
        case 'S':
            if (arg_len == 1 && arg[0] == '1') cmd.kind = Command::START_SYNC;
            else if (arg_len == 1 && arg[0] == '0') cmd.kind = Command::STOP_SYNC;
            return cmd;

        case 'T':
            if (arg_len == 1 && arg[0] == '1') cmd.kind = Command::START_CAMERA;
            else if (arg_len == 1 && arg[0] == '0') cmd.kind = Command::STOP_CAMERA;
            return cmd;

        case 'C': {
            uint32_t rate = 0;
            if (!parse_uint32(arg, arg_len, rate)) return cmd;
            if (rate < 1u || rate > 500u) return cmd;  // matches protocol.py
            cmd.kind = Command::SET_CAMERA_RATE;
            cmd.value = rate;
            return cmd;
        }

        case 'E': {
            uint32_t code = 0;
            if (!parse_uint32(arg, arg_len, code)) return cmd;
            if (code > 255u) return cmd;
            cmd.kind = Command::EVENT_CODE;
            cmd.value = code;
            return cmd;
        }

        case 'R': {
            uint32_t ms = 0;
            if (!parse_uint32(arg, arg_len, ms)) return cmd;
            if (ms < 1u || ms > 10000u) return cmd;
            cmd.kind = Command::REWARD;
            cmd.value = ms;
            return cmd;
        }

        default:
            return cmd;
    }
}

}  // namespace hapticore::parser
