#pragma once

#include <cstddef>

#include <msgpack.hpp>

#include "types.hpp"

namespace haptic {

inline bool try_get_double(const msgpack::object& obj, double& out) {
    switch (obj.type) {
        case msgpack::type::FLOAT64:
        case msgpack::type::FLOAT32:
            out = obj.via.f64;
            return true;
        case msgpack::type::POSITIVE_INTEGER:
            out = static_cast<double>(obj.via.u64);
            return true;
        case msgpack::type::NEGATIVE_INTEGER:
            out = static_cast<double>(obj.via.i64);
            return true;
        default:
            return false;
    }
}

inline bool try_get_vec3(const msgpack::object& obj, Vec3& out) {
    if (obj.type != msgpack::type::ARRAY || obj.via.array.size != 3) return false;
    for (int i = 0; i < 3; ++i) {
        if (!try_get_double(obj.via.array.ptr[i], out[static_cast<size_t>(i)])) return false;
    }
    return true;
}

inline bool try_get_bool(const msgpack::object& obj, bool& out) {
    if (obj.type != msgpack::type::BOOLEAN) return false;
    out = obj.via.boolean;
    return true;
}

} // namespace haptic
