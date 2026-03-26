#include "channel_field.hpp"
#include "msgpack_helpers.hpp"

Vec3 ChannelField::compute(const Vec3& pos, const Vec3& vel, double /*dt*/) {
    Vec3 force{};
    for (int i = 0; i < 3; ++i) {
        auto idx = static_cast<size_t>(i);
        if (active_[idx]) {
            force[idx] = -stiffness_ * (pos[idx] - center_[idx])
                       - damping_ * vel[idx];
        }
    }
    return force;
}

std::string ChannelField::name() const {
    return "channel";
}

bool ChannelField::update_params(const msgpack::object& params) {
    if (params.type != msgpack::type::MAP) return false;

    auto map = params.via.map;
    std::array<bool, 3> new_active = active_;
    double new_stiffness = stiffness_;
    double new_damping = damping_;
    Vec3 new_center = center_;
    bool has_any = false;

    for (uint32_t i = 0; i < map.size; ++i) {
        auto& key = map.ptr[i].key;
        auto& val = map.ptr[i].val;

        if (key.type != msgpack::type::STR) continue;
        std::string key_str(key.via.str.ptr, key.via.str.size);

        if (key_str == "axes") {
            if (val.type != msgpack::type::ARRAY) return false;
            // Reset all axes to false, then set the specified ones
            std::array<bool, 3> parsed_active = {false, false, false};
            for (uint32_t j = 0; j < val.via.array.size; ++j) {
                auto& elem = val.via.array.ptr[j];
                if (elem.type != msgpack::type::POSITIVE_INTEGER) {
                    return false;
                }
                auto axis = elem.via.u64;
                if (axis > 2) return false;
                parsed_active[static_cast<size_t>(axis)] = true;
            }
            new_active = parsed_active;
            has_any = true;
        } else if (key_str == "stiffness") {
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

    // Safety limits
    if (new_stiffness > MAX_STIFFNESS) return false;
    if (new_stiffness < 0.0) return false;
    if (new_damping > MAX_DAMPING) return false;
    if (new_damping < 0.0) return false;

    active_ = new_active;
    stiffness_ = new_stiffness;
    damping_ = new_damping;
    center_ = new_center;
    return true;
}
