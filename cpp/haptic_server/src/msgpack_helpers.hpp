#pragma once

#include <cstddef>

#include <msgpack.hpp>
#include <optional>
#include <string>

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

/// Extract a Vec3 from a msgpack map value keyed by ``key_name``.
/// Returns std::nullopt if the key is missing, the value is not an array
/// of exactly 3 elements, or any element is not numeric.
inline std::optional<Vec3> try_get_keyed_vec3(
    const msgpack::object& params, const char* key_name
) {
    if (params.type != msgpack::type::MAP) return std::nullopt;
    auto map = params.via.map;
    for (uint32_t i = 0; i < map.size; ++i) {
        auto& key = map.ptr[i].key;
        if (key.type != msgpack::type::STR) continue;
        std::string k(key.via.str.ptr, key.via.str.size);
        if (k == key_name) {
            Vec3 v{};
            if (!try_get_vec3(map.ptr[i].val, v)) return std::nullopt;
            return v;
        }
    }
    return std::nullopt;
}

} // namespace haptic
