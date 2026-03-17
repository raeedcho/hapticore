#pragma once

#include <cstdint>
#include <string>

#include <msgpack.hpp>

#include "types.hpp"

struct HapticStateData {
    double timestamp = 0.0;
    uint64_t sequence = 0;
    Vec3 position = {0.0, 0.0, 0.0};
    Vec3 velocity = {0.0, 0.0, 0.0};
    Vec3 force = {0.0, 0.0, 0.0};
    std::string active_field = "null";
    msgpack::sbuffer field_state_buf;  // pre-packed field state

    // Pack the full state message into the provided buffer.
    // Produces a msgpack map with 7 keys matching the Python HapticState dataclass.
    void pack(msgpack::sbuffer& buf) const {
        msgpack::packer<msgpack::sbuffer> pk(buf);
        pk.pack_map(7);
        pk.pack("timestamp");    pk.pack(timestamp);
        pk.pack("sequence");     pk.pack(sequence);
        pk.pack("position");     pk.pack(position);
        pk.pack("velocity");     pk.pack(velocity);
        pk.pack("force");        pk.pack(force);
        pk.pack("active_field"); pk.pack(active_field);
        pk.pack("field_state");
        // field_state_buf already contains a valid msgpack value (a map),
        // so writing the raw bytes directly produces a correct key-value pair.
        if (field_state_buf.size() > 0) {
            buf.write(field_state_buf.data(), field_state_buf.size());
        } else {
            // No pre-packed field state — write an empty map
            msgpack::packer<msgpack::sbuffer> pk2(buf);
            pk2.pack_map(0);
        }
    }
};
