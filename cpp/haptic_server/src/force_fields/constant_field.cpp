#include "constant_field.hpp"
#include "msgpack_helpers.hpp"

Vec3 ConstantField::compute(const Vec3& /*pos*/, const Vec3& /*vel*/, double /*dt*/) {
    return force_;
}

std::string ConstantField::name() const {
    return "constant";
}

bool ConstantField::update_params(const msgpack::object& params) {
    if (params.type != msgpack::type::MAP) return false;

    auto map = params.via.map;
    bool found_force = false;

    for (uint32_t i = 0; i < map.size; ++i) {
        auto& key = map.ptr[i].key;
        auto& val = map.ptr[i].val;

        if (key.type != msgpack::type::STR) continue;
        std::string key_str(key.via.str.ptr, key.via.str.size);

        if (key_str == "force") {
            if (!haptic::try_get_vec3(val, force_)) return false;
            found_force = true;
        }
    }

    return found_force;
}
