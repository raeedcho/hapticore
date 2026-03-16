#include "spring_damper_field.hpp"
#include "msgpack_helpers.hpp"

Vec3 SpringDamperField::compute(const Vec3& pos, const Vec3& vel, double /*dt*/) {
    Vec3 force{};
    for (int i = 0; i < 3; ++i) {
        auto idx = static_cast<size_t>(i);
        force[idx] = -stiffness_ * (pos[idx] - center_[idx]) - damping_ * vel[idx];
    }
    return force;
}

std::string SpringDamperField::name() const {
    return "spring_damper";
}

bool SpringDamperField::update_params(const msgpack::object& params) {
    if (params.type != msgpack::type::MAP) return false;

    auto map = params.via.map;
    double new_stiffness = stiffness_;
    double new_damping = damping_;
    Vec3 new_center = center_;
    bool has_any = false;

    for (uint32_t i = 0; i < map.size; ++i) {
        auto& key = map.ptr[i].key;
        auto& val = map.ptr[i].val;

        if (key.type != msgpack::type::STR) continue;
        std::string key_str(key.via.str.ptr, key.via.str.size);

        if (key_str == "stiffness") {
            if (!haptic::try_get_double(val, new_stiffness)) return false;
            has_any = true;
        } else if (key_str == "damping") {
            if (!haptic::try_get_double(val, new_damping)) return false;
            has_any = true;
        } else if (key_str == "center") {
            if (!haptic::try_get_vec3(val, new_center)) return false;
            has_any = true;
        }
    }

    if (!has_any) return false;

    // Safety limit: reject stiffness > 3000 N/m
    if (new_stiffness > MAX_STIFFNESS) return false;
    if (new_stiffness < 0.0) return false;
    if (new_damping < 0.0) return false;

    stiffness_ = new_stiffness;
    damping_ = new_damping;
    center_ = new_center;
    return true;
}
